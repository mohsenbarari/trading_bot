from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from core.config import Settings
from core.market_intelligence.coin_inference import (
    infer_coin_commodity_from_published_snapshot,
)
from core.market_intelligence.coin_rate_engine import COIN_RATE_ENGINE_VERSION
from core.market_intelligence.market_snapshot import publish_market_snapshot_atomically
from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1,
    EstimatorSnapshotV2,
    content_hash,
    estimator_snapshot_id,
)
from core.market_intelligence.product_snapshot_reader import (
    PRODUCT_PRIVATE_SNAPSHOT_PUBLISHER_UID,
    _private_snapshot_owner_is_allowed,
    configured_product_snapshot_authority_path,
    ProductSnapshotReader,
    ProductSnapshotUnavailable,
    project_private_snapshot_for_product,
)
from core.services.offer_model_price_guard import evaluate_offer_model_price_snapshot


NOW = datetime(2026, 8, 27, 5, 0, 10, tzinfo=timezone.utc)


def _snapshot_document(
    *,
    lane: str = "PRIVATE_SHADOW",
    version: int = 1,
    generated_at: str = "2026-08-27T05:00:05Z",
    no_data: bool = False,
) -> dict[str, object]:
    rates = []
    for index, (instrument, settlement) in enumerate(ESTIMATOR_RATE_GRID_V1):
        center = 190_000 - index * 5_000
        rates.append(
            {
                "instrument": instrument,
                "settlement": settlement,
                "status": "NO_DATA" if no_data else "ESTIMATED",
                "value": None if no_data else str(center),
                "unit": "PROJECT_THOUSAND_TOMAN",
                "lower_bound": None if no_data else str(center - 1_000),
                "upper_bound": None if no_data else str(center + 1_000),
                "confidence": "NONE" if no_data else "HIGH",
                "method": (
                    "ABSTAIN_NO_FRESH_MELTED"
                    if no_data
                    else "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER"
                ),
                "reason_code": "NO_FRESH_MELTED" if no_data else None,
                "underlying_source": None if no_data else "PRIVATE_PHYSICAL_TODAY",
                "underlying_age_seconds": None if no_data else 5.0,
                "anchor_age_seconds": None if no_data else 30.0,
                "market_regime": "RANGE",
            }
        )
    payload: dict[str, object] = {
        "contract": "estimator_snapshot/2.0",
        "snapshot_version": version,
        "generated_at_utc": generated_at,
        "input_snapshot_hash": content_hash([]),
        "model_version": COIN_RATE_ENGINE_VERSION,
        "feed_mode": lane,
        "status": "SAFE_NO_DATA" if no_data else "OK",
        "rates": rates,
        "health": [],
        "inputs": [],
        "reason_codes": ["NO_ESTIMATED_COIN_RATES"] if no_data else [],
    }
    payload["snapshot_id"] = estimator_snapshot_id(payload)
    return payload


def _write_private_view(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        json.dumps(
            {
                "contract": "estimator_snapshot_web_view/1.0",
                "snapshot_hash": document["snapshot_id"],
                "snapshot_version": document["snapshot_version"],
                "feed_mode": document["feed_mode"],
                "received_at_utc": "2026-08-27T05:00:06Z",
                "published_at_utc": "2026-08-27T05:00:07Z",
                "transport_state": "FRESH",
                "stale_after_seconds": 30,
                "snapshot": document,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


class ProductSnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.shadow = self.root / "latest-private-shadow.json"
        self.primary = self.root / "latest-private-primary.json"
        self.legacy = self.root / "coin-rates.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_matching_legacy(self, document: dict[str, object]) -> None:
        snapshot = EstimatorSnapshotV2.model_validate(document)
        publish_market_snapshot_atomically(
            self.legacy,
            project_private_snapshot_for_product(snapshot),
        )

    def test_contract_requires_exact_fourteen_cell_grid_and_keeps_no_data(self):
        document = _snapshot_document(no_data=True)
        snapshot = EstimatorSnapshotV2.model_validate(document)
        self.assertEqual(len(snapshot.rates), 14)
        self.assertTrue(all(rate.status == "NO_DATA" for rate in snapshot.rates))
        self.assertTrue(all(rate.reason_code for rate in snapshot.rates))

        missing = deepcopy(document)
        missing["rates"] = missing["rates"][:-1]
        missing["snapshot_id"] = estimator_snapshot_id(missing)
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(missing)

    def test_product_projection_translates_rate_regime_vocabulary(self):
        snapshot = EstimatorSnapshotV2.model_validate(_snapshot_document())
        projected = project_private_snapshot_for_product(snapshot)
        self.assertTrue(
            all(
                item["market_regime"] == "NORMAL"
                for item in projected["rates"]["items"]
            )
        )

    def test_pipeline_runtime_owner_is_an_explicit_accepted_identity(self):
        self.assertEqual(PRODUCT_PRIVATE_SNAPSHOT_PUBLISHER_UID, 10001)
        self.assertTrue(
            _private_snapshot_owner_is_allowed(10001, effective_uid=0)
        )
        self.assertTrue(
            _private_snapshot_owner_is_allowed(23001, effective_uid=23001)
        )
        self.assertFalse(
            _private_snapshot_owner_is_allowed(23002, effective_uid=23001)
        )

    def test_v2_contract_rejects_toman_per_coin_without_unit_confusion(self):
        document = _snapshot_document()
        document["rates"][0]["unit"] = "TOMAN_PER_COIN"
        document["snapshot_id"] = estimator_snapshot_id(document)
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(document)

    def test_v2_contract_enforces_product_confidence_anchor_relations(self):
        high_without_anchor = _snapshot_document()
        high_without_anchor["rates"][0]["anchor_age_seconds"] = None
        high_without_anchor["snapshot_id"] = estimator_snapshot_id(
            high_without_anchor
        )
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(high_without_anchor)

        medium_with_anchor = _snapshot_document()
        medium_with_anchor["rates"][0]["confidence"] = "MEDIUM"
        medium_with_anchor["rates"][0]["anchor_age_seconds"] = 10.0
        medium_with_anchor["snapshot_id"] = estimator_snapshot_id(
            medium_with_anchor
        )
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(medium_with_anchor)

        non_finite_age = _snapshot_document()
        non_finite_age["rates"][0]["underlying_age_seconds"] = float("inf")
        non_finite_age["snapshot_id"] = estimator_snapshot_id(non_finite_age)
        with self.assertRaises(ValidationError):
            EstimatorSnapshotV2.model_validate(non_finite_age)

    def test_default_is_fresh_legacy_authority(self):
        document = _snapshot_document()
        self._write_matching_legacy(document)
        result = ProductSnapshotReader(legacy_path=self.legacy).load(now_utc=NOW)
        self.assertEqual((result.configured_mode, result.authority), ("LEGACY", "LEGACY"))

    def test_settings_reject_unknown_mode_at_startup(self):
        with self.assertRaises(ValidationError):
            Settings(product_estimator_snapshot_mode="AUTO", _env_file=None)

    def test_settings_require_private_paths_for_explicit_private_modes(self):
        with self.assertRaisesRegex(
            ValidationError, "private_primary_product_snapshot_path_required"
        ):
            Settings(product_estimator_snapshot_mode="PRIVATE_PRIMARY")
        with self.assertRaisesRegex(
            ValidationError, "private_shadow_legacy_snapshot_path_required"
        ):
            Settings(product_estimator_snapshot_mode="PRIVATE_SHADOW")

        configured = Settings(
            product_estimator_snapshot_mode="PRIVATE_PRIMARY",
            product_estimator_private_primary_snapshot_path="/runtime/primary.json",
        )
        self.assertEqual(
            configured_product_snapshot_authority_path(configured),
            "/runtime/primary.json",
        )

    def test_shadow_dual_reads_but_keeps_legacy_authority(self):
        document = _snapshot_document()
        self._write_matching_legacy(document)
        _write_private_view(self.shadow, document)
        result = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        ).load(now_utc=NOW)
        self.assertEqual(result.authority, "LEGACY")
        self.assertEqual(result.comparison_status, "MATCH")
        self.assertEqual(result.private_snapshot_hash, document["snapshot_id"])

    def test_private_primary_has_no_legacy_fallback(self):
        document = _snapshot_document(lane="PRIVATE_PRIMARY")
        _write_private_view(self.primary, document)
        result = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_primary_path=self.primary,
            mode="PRIVATE_PRIMARY",
        ).load(now_utc=NOW)
        self.assertEqual(result.authority, "PRIVATE_PRIMARY")
        self.assertEqual(len(result.snapshot["rates"]["items"]), 14)

    def test_inference_uses_the_same_freshness_limit_as_private_reader(self):
        document = _snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at="2026-08-27T04:57:10Z",
        )
        _write_private_view(self.primary, document)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_primary_path=self.primary,
            mode="PRIVATE_PRIMARY",
            maximum_age_seconds=300,
        )

        decision = infer_coin_commodity_from_published_snapshot(
            self.legacy,
            price_project_thousand_toman=190_000,
            settlement_term="CASH",
            now_utc=NOW,
            snapshot_reader=reader,
        )

        self.assertNotEqual(decision.reason, "SNAPSHOT_STALE")
        self.assertIn(decision.status, {"AUTO_SELECT", "CONFIRM"})

    def test_product_rejects_unacknowledged_raw_local_artifact(self):
        document = _snapshot_document(lane="PRIVATE_PRIMARY")
        self.primary.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        with self.assertRaisesRegex(
            ProductSnapshotUnavailable,
            "PRIVATE_SNAPSHOT_CONTRACT_UNSUPPORTED",
        ):
            ProductSnapshotReader(
                legacy_path=self.legacy,
                private_primary_path=self.primary,
                mode="PRIVATE_PRIMARY",
            ).load(now_utc=NOW)

    def test_shadow_never_uses_stale_legacy_as_oracle_or_falls_back_to_private(self):
        stale = _snapshot_document(generated_at="2026-08-27T04:00:00Z")
        self._write_matching_legacy(stale)
        private = _snapshot_document()
        _write_private_view(self.shadow, private)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        )
        with self.assertRaisesRegex(ProductSnapshotUnavailable, "PRODUCT_SNAPSHOT_STALE"):
            reader.load(now_utc=NOW)

    def test_shadow_readiness_ignores_stale_legacy_but_load_still_fails_closed(self):
        stale = _snapshot_document(generated_at="2026-08-27T04:00:00Z")
        self._write_matching_legacy(stale)
        private = _snapshot_document(version=7)
        _write_private_view(self.shadow, private)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        )

        readiness = reader.inspect_private_shadow(now_utc=NOW)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.reason_code, "PRIVATE_SHADOW_VALID")
        self.assertEqual(readiness.private_snapshot_hash, private["snapshot_id"])
        self.assertEqual(readiness.private_snapshot_version, 7)
        self.assertEqual(len(readiness.projected_snapshot["rates"]["items"]), 14)
        with self.assertRaisesRegex(ProductSnapshotUnavailable, "PRODUCT_SNAPSHOT_STALE"):
            reader.load(now_utc=NOW)

    def test_shadow_readiness_ignores_missing_legacy_but_load_never_falls_back(self):
        private = _snapshot_document()
        _write_private_view(self.shadow, private)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        )

        readiness = reader.inspect_private_shadow(now_utc=NOW)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.reason_code, "PRIVATE_SHADOW_VALID")
        self.assertIsNotNone(readiness.projected_snapshot)
        with self.assertRaisesRegex(
            ProductSnapshotUnavailable, "LEGACY_SNAPSHOT_UNAVAILABLE"
        ):
            reader.load(now_utc=NOW)

    def test_shadow_readiness_reports_private_rejection_without_raising(self):
        private = _snapshot_document(generated_at="2026-08-27T04:00:00Z")
        _write_private_view(self.shadow, private)
        readiness = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        ).inspect_private_shadow(now_utc=NOW)

        self.assertFalse(readiness.ready)
        self.assertEqual(readiness.reason_code, "PRODUCT_SNAPSHOT_STALE")
        self.assertIsNone(readiness.private_snapshot_hash)
        self.assertIsNone(readiness.projected_snapshot)

    def test_safe_no_data_shadow_is_valid_but_not_promotion_ready(self):
        private = _snapshot_document(no_data=True)
        _write_private_view(self.shadow, private)
        readiness = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_shadow_path=self.shadow,
            mode="PRIVATE_SHADOW",
        ).inspect_private_shadow(now_utc=NOW)

        self.assertFalse(readiness.ready)
        self.assertEqual(readiness.reason_code, "PRIVATE_SHADOW_NOT_RATE_READY")
        self.assertEqual(readiness.private_snapshot_hash, private["snapshot_id"])
        self.assertIsNotNone(readiness.projected_snapshot)

    def test_failure_snapshot_is_not_projected_or_primary_authority(self):
        failed = _snapshot_document(no_data=True, lane="PRIVATE_PRIMARY")
        failed["status"] = "FAILURE"
        failed["reason_codes"] = ["ESTIMATOR_FAILURE"]
        failed["snapshot_id"] = estimator_snapshot_id(failed)
        _write_private_view(self.primary, failed)
        with self.assertRaisesRegex(
            ProductSnapshotUnavailable,
            "PRIVATE_SNAPSHOT_FAILURE",
        ):
            ProductSnapshotReader(
                legacy_path=self.legacy,
                private_primary_path=self.primary,
                mode="PRIVATE_PRIMARY",
            ).load(now_utc=NOW)

    def test_hash_and_staleness_fail_closed(self):
        document = _snapshot_document(lane="PRIVATE_PRIMARY")
        tampered = deepcopy(document)
        tampered["rates"][0]["value"] = "190001"
        _write_private_view(self.primary, tampered)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_primary_path=self.primary,
            mode="PRIVATE_PRIMARY",
        )
        with self.assertRaisesRegex(ProductSnapshotUnavailable, "PRIVATE_SNAPSHOT_CONTRACT_INVALID"):
            reader.load(now_utc=NOW)

        stale = _snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at="2026-08-27T04:00:00Z",
        )
        _write_private_view(self.primary, stale)
        with self.assertRaisesRegex(ProductSnapshotUnavailable, "PRODUCT_SNAPSHOT_STALE"):
            reader.load(now_utc=NOW)

    def test_version_regression_fails_closed(self):
        version_two = _snapshot_document(lane="PRIVATE_PRIMARY", version=2)
        _write_private_view(self.primary, version_two)
        reader = ProductSnapshotReader(
            legacy_path=self.legacy,
            private_primary_path=self.primary,
            mode="PRIVATE_PRIMARY",
        )
        reader.load(now_utc=NOW)
        version_one = _snapshot_document(lane="PRIVATE_PRIMARY", version=1)
        _write_private_view(self.primary, version_one)
        with self.assertRaisesRegex(
            ProductSnapshotUnavailable, "PRIVATE_SNAPSHOT_VERSION_REGRESSION"
        ):
            reader.load(now_utc=NOW)

    def test_no_data_projection_makes_offer_guard_abstain(self):
        private = EstimatorSnapshotV2.model_validate(_snapshot_document(no_data=True))
        product = project_private_snapshot_for_product(private)
        decision = evaluate_offer_model_price_snapshot(
            product,
            commodity_name="امام",
            settlement_type="cash",
            offer_type="sell",
            proposed_price=200_000,
            now_utc=NOW,
            market_opened_at=None,
        )
        self.assertEqual((decision.status, decision.reason), ("ABSTAINED", "MODEL_RANGE_UNAVAILABLE"))


if __name__ == "__main__":
    unittest.main()
