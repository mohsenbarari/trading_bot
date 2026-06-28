import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace

from core.sync_parity import build_table_parity_snapshot
from core.sync_repair import (
    build_current_state_replay_item,
    build_repair_plan,
    build_signed_headers,
    build_watermark_repair_payload,
    replay_identity_summary,
    row_to_sync_data,
    validate_replay_identity,
)


def fake_row(table_fields: list[str], **values):
    columns = [SimpleNamespace(name=field) for field in table_fields]
    return SimpleNamespace(__table__=SimpleNamespace(columns=columns), **values)


def snapshot(table_name: str, rows: list[dict]) -> dict:
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "quick",
        "tables": {
            table_name: build_table_parity_snapshot(table_name, rows),
        },
    }


class SyncRepairTests(unittest.TestCase):
    def test_validate_replay_identity_rejects_ambiguous_or_disallowed_fields(self):
        self.assertEqual(validate_replay_identity("offers", {"offer_public_id": "ofr_1"}), {"offer_public_id": "ofr_1"})

        with self.assertRaises(ValueError):
            validate_replay_identity("offers", {"price": 100})
        with self.assertRaises(ValueError):
            validate_replay_identity("offers", {})
        with self.assertRaises(ValueError):
            validate_replay_identity("unknown_table", {"id": 1})

    def test_replay_identity_summary_redacts_sensitive_values(self):
        summary = replay_identity_summary("users", {"mobile_number": "09370809280"})
        encoded = json.dumps(summary, sort_keys=True)

        self.assertEqual(summary["identity_fields"], ["mobile_number"])
        self.assertNotIn("09370809280", encoded)
        self.assertIn("identity_hash", summary)

    def test_row_to_sync_data_drops_local_only_and_lease_fields(self):
        offer_row = fake_row(
            ["id", "offer_public_id", "price", "channel_message_id"],
            id=1,
            offer_public_id="ofr_1",
            price=100,
            channel_message_id=777,
        )
        receipt_row = fake_row(
            ["id", "dedupe_key", "status", "worker_id", "lease_until"],
            id=2,
            dedupe_key="trade_completed:webapp:10001:1",
            status="pending",
            worker_id="worker-1",
            lease_until="2026-06-27T12:00:00",
        )

        self.assertEqual(row_to_sync_data("offers", offer_row), {"id": 1, "offer_public_id": "ofr_1", "price": 100})
        self.assertEqual(
            row_to_sync_data("trade_delivery_receipts", receipt_row),
            {"id": 2, "dedupe_key": "trade_completed:webapp:10001:1", "status": "pending"},
        )

    def test_row_to_sync_data_drops_offer_publication_runtime_fields(self):
        publication_row = fake_row(
            [
                "id",
                "offer_id",
                "offer_public_id",
                "surface",
                "publication_owner_server",
                "status",
                "dedupe_key",
                "telegram_chat_id",
                "telegram_message_id",
                "last_attempt_at",
                "error_message",
                "offer_version_id",
            ],
            id=3,
            offer_id=33,
            offer_public_id="ofr_1",
            surface="telegram_channel",
            publication_owner_server="foreign",
            status="sent",
            dedupe_key="offer-publication:telegram_channel:ofr_1",
            telegram_chat_id=-1001,
            telegram_message_id=777,
            last_attempt_at="2026-06-28T04:00:00",
            error_message="telegram runtime detail",
            offer_version_id=4,
        )

        self.assertEqual(
            row_to_sync_data("offer_publication_states", publication_row),
            {
                "offer_public_id": "ofr_1",
                "surface": "telegram_channel",
                "publication_owner_server": "foreign",
                "status": "sent",
                "dedupe_key": "offer-publication:telegram_channel:ofr_1",
                "offer_version_id": 4,
            },
        )

    def test_build_current_state_replay_item_includes_signed_metadata_without_local_runtime_fields(self):
        row = fake_row(
            ["id", "offer_public_id", "price", "channel_message_id"],
            id=1,
            offer_public_id="ofr_1",
            price=100,
            channel_message_id=777,
        )

        item = build_current_state_replay_item(
            table_name="offers",
            row=row,
            source_server="foreign",
            source_sequence=123,
        )

        self.assertEqual(item["table"], "offers")
        self.assertEqual(item["operation"], "UPDATE")
        self.assertEqual(item["change_log_id"], 123)
        self.assertEqual(item["sync_meta"]["source_server"], "foreign")
        self.assertEqual(item["sync_meta"]["source_sequence"], 123)
        self.assertEqual(item["public_identity"]["kind"], "offer_public_id")
        self.assertNotIn("channel_message_id", item["data"])

    def test_build_signed_headers_matches_receiver_signature_contract(self):
        body = '[{"table":"offers"}]'
        headers = build_signed_headers("secret", body, timestamp=1700000000)
        expected = hmac.new("secret".encode(), f"1700000000:{body}".encode(), hashlib.sha256).hexdigest()

        self.assertEqual(headers["X-Timestamp"], "1700000000")
        self.assertEqual(headers["X-API-Key"], "secret")
        self.assertEqual(headers["X-Signature"], expected)

    def test_build_repair_plan_is_dry_run_and_actionable(self):
        local = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
        peer = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 101}])

        plan = build_repair_plan(local, peer, direction="local-to-peer")

        self.assertEqual(plan["status"], "dry_run")
        self.assertEqual(plan["comparison_status"], "business_drift")
        self.assertEqual(plan["action_count"], 1)
        self.assertEqual(plan["actions"][0]["action"], "replay_current_state")
        self.assertEqual(plan["actions"][0]["target"], "peer")

    def test_watermark_repair_payload_is_redacted_and_dry_run_only(self):
        payload = build_watermark_repair_payload(
            source_server="foreign",
            aggregate_table="offers",
            aggregate_key="ofr_secret_1",
            source_sequence=123,
            payload_hash="abc123",
            operation="UPDATE",
            record_id=1,
        )
        encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual(payload["status"], "dry_run")
        self.assertTrue(payload["requires_backup_and_operator_approval"])
        self.assertIn("INSERT INTO sync_apply_watermarks", payload["sql"])
        self.assertNotIn("ofr_secret_1", encoded)


if __name__ == "__main__":
    unittest.main()
