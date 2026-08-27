import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from pydantic import ValidationError

from core.market_intelligence.market_history_backfill import (
    EncryptedParticipantV1,
    EncryptedRawTextV1,
    HistoryBackfillError,
    HistoryFactRecordV1,
    HistoryImportBundleV1,
    _scan_forbidden,
    build_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def coin_record(**changes):
    occurred = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    value = {
        "contract": "market_history_fact/1.0",
        "lineage": {"source_record_id_hash": "1" * 64, "source_revision": 1},
        "event_key": "2" * 64,
        "origin_event_key": "2" * 64,
        "source_code": "GROUP_1",
        "occurred_at_utc": occurred.isoformat(),
        "available_at_utc": (occurred + timedelta(milliseconds=20)).isoformat(),
        "parser_version": "stage11-test-v1",
        "quality_state": "ELIGIBLE",
        "quality_reason_codes": [],
        "payload": {
            "kind": "COIN_OFFER",
            "group_code": 1,
            "instrument": "COIN_IMAM",
            "side": "SELL",
            "settlement": "TOMORROW",
            "trade_form": "PHYSICAL",
            "offered_price_value": "188600",
            "price_unit": "PROJECT_THOUSAND_TOMAN",
            "quantity_value": "5",
            "quantity_unit": "COIN",
        },
    }
    value.update(changes)
    return value


class MarketPipelineStage11BackfillTests(unittest.TestCase):
    def test_bundle_hash_is_exact_and_tamper_evident(self):
        bundle = build_bundle(
            source_code="GROUP_1",
            source_system="LEGACY_MARKET_STORE",
            records=[coin_record()],
        )
        self.assertEqual(bundle.source_code, "GROUP_1")
        changed = bundle.model_dump(mode="json")
        changed["records"][0]["payload"]["offered_price_value"] = "999999"
        with self.assertRaisesRegex(ValidationError, "source_artifact_hash_mismatch"):
            HistoryImportBundleV1.model_validate(changed)

    def test_record_contract_rejects_non_permanent_or_wrong_source(self):
        record = coin_record(source_code="MELTED_FLOW")
        record["payload"] = {
            "kind": "OBSERVATION",
            "instrument": "MELTED_GOLD",
            "event_type": "OFFER",
            "side": "SELL",
            "settlement": "CASH",
            "trade_form": "PHYSICAL",
            "price_value": "100000000",
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "currency": "TOMAN",
        }
        with self.assertRaisesRegex(ValidationError, "history_source_not_permanent"):
            HistoryFactRecordV1.model_validate(record)
        with self.assertRaisesRegex(ValidationError, "history_bundle_source_not_permanent"):
            build_bundle(
                source_code="MELTED_FLOW",
                source_system="LEGACY_MARKET_STORE",
                records=[record],
            )

    def test_transient_seed_is_explicit_and_never_accepts_permanent_source(self):
        record = coin_record(source_code="MELTED_FLOW")
        record["payload"] = {
            "kind": "OBSERVATION",
            "instrument": "MELTED_GOLD",
            "event_type": "OFFER",
            "side": "SELL",
            "settlement": "CASH",
            "trade_form": "PHYSICAL",
            "price_value": "100000000",
            "price_unit": "TOMAN_PER_MESGHAL_750",
            "currency": "TOMAN",
        }
        parsed = HistoryFactRecordV1.model_validate(
            record,
            context={"allow_transient_seed": True},
        )
        bundle = build_bundle(
            source_code="MELTED_FLOW",
            source_system="LEGACY_MARKET_STORE",
            retention_mode="TRANSIENT_SEED",
            records=[parsed.model_dump(mode="json")],
        )
        self.assertEqual(bundle.retention_mode, "TRANSIENT_SEED")
        with self.assertRaisesRegex(
            ValidationError, "history_bundle_transient_source_must_not_be_permanent"
        ):
            build_bundle(
                source_code="GROUP_1",
                source_system="LEGACY_MARKET_STORE",
                retention_mode="TRANSIENT_SEED",
                records=[coin_record()],
            )

    def test_forbidden_urls_credentials_and_plain_envelope_are_rejected(self):
        for field, value in (
            ("message_link", "https://t.me/example/1"),
            ("credential", "not-a-real-secret"),
            ("raw_payload", {"text": "plaintext"}),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                HistoryBackfillError, "FORBIDDEN_FIELD"
            ):
                _scan_forbidden({field: value})
        with self.assertRaisesRegex(HistoryBackfillError, "FORBIDDEN_VALUE"):
            _scan_forbidden({"note": "https://example.invalid"})

    def test_only_ciphertext_crosses_sensitive_archive_boundary(self):
        ciphertext = base64.b64encode(b"authenticated-ciphertext-fixture").decode()
        raw = EncryptedRawTextV1(
            ciphertext_b64=ciphertext,
            plaintext_hash="3" * 64,
            encryption_key_id="history-key:v1",
        )
        actor = EncryptedParticipantV1(
            actor_role="OFFERER",
            telegram_id_ciphertext_b64=ciphertext,
            telegram_id_lookup_hmac="4" * 64,
            display_name_ciphertext_b64=ciphertext,
            encryption_key_id="history-key:v1",
        )
        record = HistoryFactRecordV1.model_validate(
            coin_record(
                encrypted_raw_text=raw.model_dump(mode="json"),
                encrypted_participants=[actor.model_dump(mode="json")],
            )
        )
        self.assertEqual(record.encrypted_participants[0].actor_role, "OFFERER")
        with self.assertRaises(ValidationError):
            EncryptedRawTextV1(
                ciphertext_b64="plaintext",
                plaintext_hash="3" * 64,
                encryption_key_id="history-key:v1",
            )

    def test_additive_migration_and_seed_query_keep_private_tables_separate(self):
        migration = (
            REPO_ROOT
            / "deploy/market-data/migrations/0002_history_backfill.up.sql"
        ).read_text(encoding="utf-8")
        implementation = (
            REPO_ROOT
            / "core/market_intelligence/market_history_backfill.py"
        ).read_text(encoding="utf-8")
        for table in (
            "history_import_batches",
            "history_import_items",
            "history_import_quarantine",
        ):
            self.assertIn(f"CREATE TABLE market_data.{table}", migration)
        seed_query = implementation.split("def export_bot_seed", 1)[1]
        self.assertNotIn("JOIN market_data.curated_raw_texts", seed_query)
        self.assertNotIn("JOIN market_data.market_actor_identities", seed_query)
        self.assertIn('"contains_raw_telegram_history": False', seed_query)

    def test_rehearsal_requires_an_explicit_candidate_image(self):
        gate = (
            REPO_ROOT / "scripts/run_market_history_stage11_gate.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '${MARKET_STAGE11_IMAGE:?MARKET_STAGE11_IMAGE is required}', gate
        )
        self.assertNotIn("stage11-worktree", gate)


if __name__ == "__main__":
    unittest.main()
