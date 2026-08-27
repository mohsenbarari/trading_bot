from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_rate_engine import COIN_SPECS
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence.shadow_parity import (
    CAPTURE_SOURCE_INVENTORY,
    FAILURE_DRILLS,
    FALLBACK_CAPTURE_SOURCES,
    build_lane_evidence_from_market_store,
    capture_manifest_hash,
    compare_shadow_lanes,
    feature_evidence_snapshot_hash,
    sign_parity_report,
    verify_parity_report,
)


START = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)
SESSION_ID = "STAGE12_OPEN_MARKET_SESSION"
MODEL_HASH = sha256(b"same-model-artifact").hexdigest()
SIGNING_KEY = b"stage12-test-signing-key-material-32-bytes-minimum"
SOURCE_CODES = (
    "GROUP_1",
    "GROUP_2",
    "PRIVATE_GOLD_CHANNEL",
    "USD_HERAT",
    "XAUUSD",
    "WALLEX_PUBLIC_API",
)


def dimensions(price="188600", unit="PROJECT_THOUSAND_TOMAN"):
    return {
        "instrument": "COIN_IMAM",
        "event_type": "OFFER",
        "side": "SELL",
        "settlement": "CASH",
        "trade_form": "PHYSICAL",
        "price_value": price,
        "price_unit": unit,
        "quantity_value": "5",
        "quantity_unit": "COIN",
    }


def _snapshot_times(end: datetime, count: int = 12) -> list[datetime]:
    usable_seconds = int((end - START).total_seconds()) - 10
    return [
        START + timedelta(seconds=5 + (usable_seconds * index // (count - 1)))
        for index in range(count)
    ]


def _inventory(captures, end):
    counts = Counter(item["source_code"] for item in captures)
    return [
        {
            "source_code": source,
            "captured_event_count": counts.get(source, 0),
            "healthy": True,
            "observed_at_utc": end.isoformat(),
            "zero_event_reason": (
                "FALLBACK_NOT_SELECTED"
                if source in FALLBACK_CAPTURE_SOURCES and not counts.get(source, 0)
                else None
            ),
        }
        for source in sorted(CAPTURE_SOURCE_INVENTORY)
    ]


def lane(role, *, duration=timedelta(hours=2), snapshot_count=12):
    end = START + duration
    snapshots = _snapshot_times(end, snapshot_count)
    captures = []
    facts = []
    features = []
    estimates = []
    for index, evaluation in enumerate(snapshots):
        key = f"{index + 1:064x}"
        occurred = evaluation - timedelta(seconds=5)
        available = occurred + timedelta(milliseconds=20)
        dims = dimensions(price=str(188600 + index))
        source = SOURCE_CODES[index % len(SOURCE_CODES)]
        captures.append(
            {
                "event_key": key,
                "source_code": source,
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
            }
        )
        facts.append(
            {
                "event_key": key,
                "source_code": source,
                "eligible": True,
                "dimensions": dims,
                "parser_fingerprint": content_hash(dims),
                "lifecycle_state": "ACTIVE",
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
                "parsed_at_utc": (occurred + timedelta(milliseconds=100)).isoformat(),
                "transferred_at_utc": (
                    occurred + timedelta(milliseconds=200)
                ).isoformat(),
                "next_snapshot_at_utc": evaluation.isoformat(),
            }
        )
        snapshot_features = [
            {
                "evaluation_at_utc": evaluation.isoformat(),
                "component": "XAUUSD",
                "point_value": "3400.25",
                "mean_value": "3400.20",
                "unit": "USD_PER_TROY_OUNCE",
                "sample_count": 5,
                "source_event_key": key,
                "freshness": "FRESH",
            },
            {
                "evaluation_at_utc": evaluation.isoformat(),
                "component": "USDT_IRT",
                "point_value": "96000",
                "mean_value": "95990",
                "unit": "TOMAN_PER_USDT",
                "sample_count": 6,
                "source_event_key": key,
                "freshness": "FRESH",
            },
        ]
        features.extend(snapshot_features)
        input_hash = feature_evidence_snapshot_hash(snapshot_features)
        for rate_index, instrument in enumerate(COIN_SPECS):
            for settlement in ("CASH", "TOMORROW"):
                no_data = instrument == "ONE_GRAM" and settlement == "TOMORROW"
                center = 188700 + rate_index * 100
                estimates.append(
                    {
                        "evaluation_at_utc": evaluation.isoformat(),
                        "model_artifact_hash": MODEL_HASH,
                        "input_snapshot_hash": input_hash,
                        "instrument": f"COIN_{instrument}",
                        "settlement": settlement,
                        "status": "NO_DATA" if no_data else "ESTIMATED",
                        "value": None if no_data else str(center),
                        "lower_bound": None if no_data else str(center - 700),
                        "upper_bound": None if no_data else str(center + 300),
                        "reason_code": "NO_SAFE_ANCHOR" if no_data else None,
                        "unit": "PROJECT_THOUSAND_TOMAN",
                        "confidence": "NONE" if no_data else "HIGH",
                        "method": "NO_SAFE_ANCHOR" if no_data else "WEIGHTED_BOOK",
                        "underlying_source": (
                            None if no_data else "PRIVATE_PHYSICAL_TODAY"
                        ),
                        "underlying_age_seconds": None if no_data else 1.0,
                        "anchor_age_seconds": None if no_data else 30.0,
                        "market_regime": "NORMAL",
                    }
                )
    return {
        "contract": "market_shadow_lane/1.0",
        "lane": role,
        "session_id": SESSION_ID,
        "window_start_utc": START.isoformat(),
        "window_end_utc": end.isoformat(),
        "capture_manifest_complete": True,
        "model_artifact_hash": MODEL_HASH,
        "captures": captures,
        "capture_prefix": {
            "contract": "market_immutable_capture_prefix/1.0",
            "capture_authority": "NEW_SINGLE_OWNER_CAPTURE",
            "session_id": SESSION_ID,
            "pinned_at_utc": (START - timedelta(minutes=1)).isoformat(),
            "sealed_at_utc": (end + timedelta(minutes=1)).isoformat(),
            "byte_range_start": 0,
            "byte_range_end": len(captures) * 100,
            "prefix_event_count": len(captures),
            "ordered_manifest_hash": capture_manifest_hash(captures),
            "seed_receipt_hash": content_hash({"session_id": SESSION_ID, "seed": 1}),
            "sealed_byte_range_hash": content_hash(
                {"session_id": SESSION_ID, "events": len(captures)}
            ),
            "single_owner_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "owners": 1}
            ),
            "sequence_health_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "sequence_gaps": 0}
            ),
            "reconciliation_receipt_hash": content_hash(
                {"session_id": SESSION_ID, "reconciliation_gaps": 0}
            ),
            "unresolved_sequence_gap_count": 0,
            "unresolved_reconciliation_gap_count": 0,
        },
        "capture_inventory": _inventory(captures, end),
        "facts": facts,
        "features": features,
        "snapshot_versions": [
            {
                "snapshot_version": index + 100,
                "evaluation_at_utc": evaluation.isoformat(),
            }
            for index, evaluation in enumerate(snapshots)
        ],
        "estimates": estimates,
        "transport": {
            "unresolved_sequence_gap_count": 0,
            "duplicate_eligible_fact_count": 0 if role == "PRIVATE_SHADOW" else None,
            "duplicate_evidence": (
                "DELIVERY_LEDGER" if role == "PRIVATE_SHADOW" else "NOT_APPLICABLE"
            ),
            "duplicate_evidence_receipt_hash": (
                content_hash({"session_id": SESSION_ID, "ledger_rows": len(facts)})
                if role == "PRIVATE_SHADOW"
                else None
            ),
            "duplicate_evidence_row_count": (
                len(facts) if role == "PRIVATE_SHADOW" else None
            ),
            "rejected_delivery_count": 0,
            "receiver_checkpoint_count": 6 if role == "PRIVATE_SHADOW" else 0,
        },
    }


def soak(mode="HISTORICAL_REPLAY", full=False, *, duration=timedelta(hours=2)):
    end = START + duration
    receipts = []
    for index, drill in enumerate(sorted(FAILURE_DRILLS)):
        drill_start = START + timedelta(seconds=30 + index * 10)
        receipts.append(
            {
                "drill": drill,
                "session_id": SESSION_ID,
                "receipt_hash": sha256(drill.encode("ascii")).hexdigest(),
                "started_at_utc": drill_start.isoformat(),
                "completed_at_utc": (drill_start + timedelta(seconds=5)).isoformat(),
                "passed": True,
            }
        )
    return {
        "contract": "market_failure_soak/1.0",
        "session_id": SESSION_ID,
        "evidence_mode": mode,
        "started_at_utc": START.isoformat(),
        "completed_at_utc": end.isoformat(),
        "full_market_session": full,
        "receiver_restart_passed": True,
        "route_partition_passed": True,
        "lost_ack_passed": True,
        "rollback_passed": True,
        "disk_failure_passed": True,
        "market_schedule": {
            "contract": "market_session_schedule/1.0",
            "session_id": SESSION_ID,
            "schedule_id": "TEHRAN_MARKET_SESSION",
            "schedule_version": "SCHEDULE_V1",
            "timezone_name": "Asia/Tehran",
            "official_open_at_utc": START.isoformat(),
            "official_close_at_utc": end.isoformat(),
            "schedule_receipt_hash": sha256(
                f"{START.isoformat()}:{end.isoformat()}".encode("ascii")
            ).hexdigest(),
        },
        "drill_receipts": receipts,
    }


class MarketPipelineStage12ParityTests(unittest.TestCase):
    def compare(self, private=None, labels=(), soak_value=None, legacy=None):
        return compare_shadow_lanes(
            legacy or lane("REFERENCE_PROJECTION"),
            private or lane("PRIVATE_SHADOW"),
            soak_value=soak_value or soak(),
            labels_value=labels,
        )

    def test_live_readiness_is_blocked_without_trusted_attestation(self):
        report = self.compare(soak_value=soak("LIVE_OPEN_MARKET", True))
        self.assertGreaterEqual(report["severity_1_count"], 1)
        self.assertEqual(report["severity_2_count"], 0)
        self.assertTrue(report["session_bound"])
        self.assertFalse(report["live_open_market_passed"])
        self.assertTrue(report["common_immutable_capture_prefix"])
        self.assertTrue(report["capture_session_preopen_pinned_and_sealed"])
        self.assertEqual(report["reference_lane"], "REFERENCE_PROJECTION")
        self.assertEqual(report["live_capture_authority"], "NEW_SINGLE_OWNER_CAPTURE")
        self.assertEqual(report["private_snapshot_timeline"]["snapshot_count"], 12)
        self.assertIn(
            "TRUSTED_LIVE_ATTESTATION_UNAVAILABLE",
            {item["code"] for item in report["issues"]},
        )
        self.assertIn(
            "LIVE_SNAPSHOT_CADENCE_GAP",
            {item["code"] for item in report["issues"]},
        )
        self.assertEqual(
            report["promotion_recommendation"],
            "HOLD_BLOCKING_PARITY_FINDINGS",
        )
        self.assertFalse(report["cutover_performed"])

    def test_old_live_collector_lane_and_unsealed_prefix_cannot_be_ready(self):
        old_reference = lane("LEGACY")
        report = self.compare(
            legacy=old_reference,
            soak_value=soak("LIVE_OPEN_MARKET", True),
        )
        self.assertIn(
            "OLD_LIVE_COLLECTOR_REFERENCE_FORBIDDEN",
            {item["code"] for item in report["issues"]},
        )
        reference = lane("REFERENCE_PROJECTION")
        private = lane("PRIVATE_SHADOW")
        reference["capture_prefix"]["pinned_at_utc"] = (
            START + timedelta(seconds=1)
        ).isoformat()
        private["capture_prefix"] = deepcopy(reference["capture_prefix"])
        report = self.compare(
            private,
            legacy=reference,
            soak_value=soak("LIVE_OPEN_MARKET", True),
        )
        self.assertIn(
            "CAPTURE_SESSION_NOT_PREOPEN_PINNED_AND_SEALED",
            {item["code"] for item in report["issues"]},
        )

    def test_clean_replay_holds_for_live_open_market_session(self):
        report = self.compare()
        self.assertEqual(report["severity_1_count"], 0)
        self.assertEqual(report["severity_2_count"], 0)
        self.assertEqual(report["source_to_snapshot_p95_seconds"], 5.0)
        self.assertEqual(
            report["promotion_recommendation"], "HOLD_LIVE_OPEN_MARKET_REQUIRED"
        )

    def test_empty_or_single_source_capture_cannot_be_ready(self):
        for retained_sources in (set(), {"GROUP_1"}):
            with self.subTest(retained_sources=retained_sources):
                private = lane("PRIVATE_SHADOW")
                private["captures"] = [
                    item
                    for item in private["captures"]
                    if item["source_code"] in retained_sources
                ]
                private["capture_inventory"] = _inventory(
                    private["captures"], START + timedelta(hours=2)
                )
                report = self.compare(
                    private,
                    soak_value=soak("LIVE_OPEN_MARKET", True),
                )
                codes = {item["code"] for item in report["issues"]}
                self.assertIn("REQUIRED_CAPTURE_SOURCE_NOT_EVENTFUL", codes)
                self.assertEqual(
                    report["promotion_recommendation"],
                    "HOLD_BLOCKING_PARITY_FINDINGS",
                )

    def test_all_nine_sources_need_healthy_inventory_in_both_lanes(self):
        private = lane("PRIVATE_SHADOW")
        private["capture_inventory"] = private["capture_inventory"][:-1]
        report = self.compare(private)
        self.assertIn(
            "CAPTURE_SOURCE_INVENTORY_INCOMPLETE",
            {item["code"] for item in report["issues"]},
        )
        legacy = lane("REFERENCE_PROJECTION")
        legacy["capture_inventory"] = legacy["capture_inventory"][:-1]
        report = self.compare(legacy=legacy)
        self.assertIn(
            "CAPTURE_SOURCE_INVENTORY_INCOMPLETE",
            {item["code"] for item in report["issues"]},
        )
        self.assertEqual(
            set(report["private_capture_source_event_counts"]),
            CAPTURE_SOURCE_INVENTORY,
        )

    def test_valid_fallback_zero_event_reasons_do_not_block(self):
        report = self.compare()
        components = {
            item["component"]
            for item in report["issues"]
            if item["code"] == "REQUIRED_CAPTURE_SOURCE_NOT_EVENTFUL"
        }
        self.assertFalse(components & FALLBACK_CAPTURE_SOURCES)

    def test_short_live_window_cannot_be_ready(self):
        duration = timedelta(hours=1)
        report = self.compare(
            lane("PRIVATE_SHADOW", duration=duration),
            legacy=lane("REFERENCE_PROJECTION", duration=duration),
            soak_value=soak("LIVE_OPEN_MARKET", True, duration=duration),
        )
        self.assertIn(
            "LIVE_PARITY_WINDOW_TOO_SHORT",
            {item["code"] for item in report["issues"]},
        )

    def test_session_and_window_must_bind_to_soak(self):
        evidence = soak("LIVE_OPEN_MARKET", True)
        evidence["session_id"] = "ANOTHER_SESSION"
        evidence["market_schedule"]["session_id"] = "ANOTHER_SESSION"
        for receipt in evidence["drill_receipts"]:
            receipt["session_id"] = "ANOTHER_SESSION"
        report = self.compare(soak_value=evidence)
        self.assertFalse(report["session_bound"])
        self.assertIn(
            "SESSION_OR_WINDOW_BINDING_INVALID",
            {item["code"] for item in report["issues"]},
        )

    def test_failure_boolean_without_receipts_cannot_be_ready(self):
        evidence = soak("LIVE_OPEN_MARKET", True)
        evidence["drill_receipts"] = []
        report = self.compare(soak_value=evidence)
        self.assertFalse(report["failure_soak_passed"])
        self.assertIn(
            "FAILURE_SOAK_RECEIPTS_INCOMPLETE",
            {item["code"] for item in report["issues"]},
        )

    def test_full_session_boolean_without_official_schedule_cannot_be_ready(self):
        evidence = soak("LIVE_OPEN_MARKET", True)
        evidence["market_schedule"] = None
        report = self.compare(soak_value=evidence)
        self.assertFalse(report["official_market_schedule_bound"])
        self.assertIn(
            "OFFICIAL_MARKET_SCHEDULE_NOT_BOUND",
            {item["code"] for item in report["issues"]},
        )
        self.assertEqual(
            report["promotion_recommendation"], "HOLD_BLOCKING_PARITY_FINDINGS"
        )

    def test_unknown_duplicate_evidence_cannot_be_ready(self):
        private = lane("PRIVATE_SHADOW")
        private["transport"]["duplicate_eligible_fact_count"] = None
        private["transport"]["duplicate_evidence"] = "UNKNOWN"
        private["transport"]["duplicate_evidence_receipt_hash"] = None
        private["transport"]["duplicate_evidence_row_count"] = None
        report = self.compare(private)
        self.assertIsNone(report["duplicate_eligible_fact_count"])
        self.assertIn(
            "DUPLICATE_LEDGER_EVIDENCE_UNKNOWN",
            {item["code"] for item in report["issues"]},
        )
        private = lane("PRIVATE_SHADOW")
        private["transport"]["duplicate_evidence_receipt_hash"] = None
        report = self.compare(private)
        self.assertIn(
            "DUPLICATE_LEDGER_EVIDENCE_UNKNOWN",
            {item["code"] for item in report["issues"]},
        )

    def test_one_rate_or_no_data_status_difference_cannot_be_ready(self):
        private = lane("PRIVATE_SHADOW")
        private["estimates"] = private["estimates"][:1]
        report = self.compare(private)
        self.assertIn(
            "RATE_GRID_INCOMPLETE", {item["code"] for item in report["issues"]}
        )
        private = lane("PRIVATE_SHADOW")
        target = next(item for item in private["estimates"] if item["status"] == "NO_DATA")
        target.update(
            status="ESTIMATED",
            value="100",
            lower_bound="90",
            upper_bound="110",
            reason_code=None,
            confidence="HIGH",
            method="WEIGHTED_BOOK",
            underlying_source="PRIVATE_PHYSICAL_TODAY",
            underlying_age_seconds=1.0,
            anchor_age_seconds=30.0,
        )
        report = self.compare(private)
        self.assertIn(
            "RATE_STATUS_MISMATCH", {item["code"] for item in report["issues"]}
        )
        private = lane("PRIVATE_SHADOW")
        private["features"] = [
            item for item in private["features"] if item["component"] != "USDT_IRT"
        ]
        report = self.compare(private)
        self.assertIn(
            "EXTERNAL_FEATURE_GRID_INCOMPLETE",
            {item["code"] for item in report["issues"]},
        )

    def test_timeline_must_be_sufficient_connected_and_bound(self):
        private = lane("PRIVATE_SHADOW")
        removed = private["snapshot_versions"].pop(5)
        report = self.compare(private)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SNAPSHOT_VERSION_GAP", codes)
        self.assertIn("SNAPSHOT_TRACE_NOT_IN_TIMELINE", codes)
        self.assertIn("LANE_SNAPSHOT_TIMELINE_MISMATCH", codes)
        self.assertEqual(removed["snapshot_version"], 105)

    def test_feature_freshness_input_hash_and_estimate_metadata_are_gated(self):
        private = lane("PRIVATE_SHADOW")
        private["features"][0]["freshness"] = "STALE"
        report = self.compare(private)
        self.assertIn(
            "EXTERNAL_FEATURE_NOT_FRESH",
            {item["code"] for item in report["issues"]},
        )

        private = lane("PRIVATE_SHADOW")
        private["estimates"][0]["input_snapshot_hash"] = "f" * 64
        report = self.compare(private)
        self.assertIn(
            "INPUT_SNAPSHOT_HASH_INVALID",
            {item["code"] for item in report["issues"]},
        )

        private = lane("PRIVATE_SHADOW")
        private["estimates"][0]["method"] = "DIFFERENT_METHOD"
        report = self.compare(private)
        self.assertIn(
            "ESTIMATE_METADATA_MISMATCH",
            {item["code"] for item in report["issues"]},
        )

    def test_live_session_with_only_no_data_rates_is_blocked(self):
        reference = lane("REFERENCE_PROJECTION")
        private = lane("PRIVATE_SHADOW")
        for evidence in (reference, private):
            for estimate in evidence["estimates"]:
                estimate.update(
                    status="NO_DATA",
                    value=None,
                    lower_bound=None,
                    upper_bound=None,
                    reason_code="NO_SAFE_ANCHOR",
                    confidence="NONE",
                    method="NO_SAFE_ANCHOR",
                    underlying_source=None,
                    underlying_age_seconds=None,
                    anchor_age_seconds=None,
                )
        report = self.compare(
            private,
            legacy=reference,
            soak_value=soak("LIVE_OPEN_MARKET", True),
        )
        self.assertIn(
            "LIVE_SESSION_HAS_NO_ESTIMATED_RATE",
            {item["code"] for item in report["issues"]},
        )

    def test_capture_loss_and_transport_gap_are_severity_one(self):
        private = lane("PRIVATE_SHADOW")
        private["captures"] = private["captures"][1:]
        private["capture_inventory"] = _inventory(
            private["captures"], START + timedelta(hours=2)
        )
        private["transport"]["unresolved_sequence_gap_count"] = 1
        report = self.compare(private)
        self.assertEqual(report["private_capture_loss_count"], 1)
        self.assertGreaterEqual(report["severity_1_count"], 2)

    def test_parser_difference_requires_approved_human_label(self):
        private = lane("PRIVATE_SHADOW")
        dims = dimensions(price="188650")
        private["facts"][0]["dimensions"] = dims
        private["facts"][0]["parser_fingerprint"] = content_hash(dims)
        unresolved = self.compare(private)
        self.assertEqual(unresolved["severity_2_count"], 1)
        labels = [
            {
                "event_key": private["facts"][0]["event_key"],
                "resolution": "PRIVATE_CORRECT",
                "label_id_hash": "a" * 64,
                "approved_at_utc": START.isoformat(),
            }
        ]
        accepted = self.compare(private, labels)
        self.assertEqual(accepted["severity_2_count"], 0)
        self.assertEqual(accepted["accepted_labeled_difference_count"], 1)

    def test_unit_external_value_and_estimator_mismatch_block(self):
        private = lane("PRIVATE_SHADOW")
        dims = dimensions(unit="TOMAN_PER_COIN")
        private["facts"][0]["dimensions"] = dims
        private["facts"][0]["parser_fingerprint"] = content_hash(dims)
        private["features"][0]["point_value"] = "3401"
        private["estimates"][0]["value"] = "188800"
        report = self.compare(private)
        self.assertGreaterEqual(report["severity_1_count"], 3)
        self.assertEqual(report["consumed_external_mismatch_count"], 1)
        self.assertGreaterEqual(report["same_input_estimator_mismatch_count"], 1)

    def test_missing_snapshot_trace_and_slow_p95_block(self):
        private = lane("PRIVATE_SHADOW")
        private["facts"][0]["next_snapshot_at_utc"] = None
        private["facts"][1]["next_snapshot_at_utc"] = (
            datetime.fromisoformat(private["facts"][1]["occurred_at_utc"])
            + timedelta(seconds=8)
        ).isoformat()
        report = self.compare(private)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SNAPSHOT_TRACE_MISSING", codes)
        self.assertIn("SNAPSHOT_TRACE_NOT_IN_TIMELINE", codes)
        self.assertIn("SOURCE_TO_SNAPSHOT_P95_EXCEEDED", codes)

    def test_report_signature_detects_tampering(self):
        signed = sign_parity_report(
            self.compare(), key=SIGNING_KEY, key_id="stage12-test:v1"
        )
        self.assertTrue(verify_parity_report(signed, key=SIGNING_KEY))
        tampered = deepcopy(signed)
        tampered["private_fact_count"] += 1
        self.assertFalse(verify_parity_report(tampered, key=SIGNING_KEY))

    def test_read_only_builder_preserves_microseconds_and_snapshot_guard(self):
        before_insert = datetime.now(timezone.utc)
        event_time = before_insert - timedelta(seconds=10)
        event_key = derive_event_key("stage12-builder", "xau")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            connection = connect_market_store(path)
            try:
                initialize_market_store(connection)
                upsert_observation(
                    connection,
                    MarketObservation(
                        event_key=event_key,
                        source_code="XAUUSD",
                        source_family="EXTERNAL_MARKET",
                        event_time_utc=event_time,
                        available_at_utc=event_time + timedelta(milliseconds=10),
                        instrument="XAUUSD",
                        market_label="GLOBAL_SPOT",
                        settlement_term="SPOT",
                        trade_form="NOT_APPLICABLE",
                        event_type="QUOTE",
                        side="MID",
                        price="3400.25",
                        price_unit="USD_PER_TROY_OUNCE",
                        currency="USD",
                        parse_confidence=1.0,
                        parser_version="stage12-builder-v1",
                        quality_state="ELIGIBLE",
                        quality_policy_version="stage12-builder-v1",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            snapshot_at = datetime.now(timezone.utc) + timedelta(seconds=1)
            manifest = [
                {
                    "event_key": event_key.hex(),
                    "source_code": "XAUUSD",
                    "occurred_at_utc": event_time.isoformat(),
                    "available_at_utc": (
                        event_time + timedelta(milliseconds=10)
                    ).isoformat(),
                }
            ]
            evidence = build_lane_evidence_from_market_store(
                market_store_path=path,
                lane="LEGACY",
                window_start_utc=event_time - timedelta(seconds=1),
                window_end_utc=snapshot_at,
                model_artifact_hash=MODEL_HASH,
                capture_manifest=manifest,
                snapshot_times={event_key.hex(): snapshot_at},
            )
        self.assertTrue(evidence.capture_manifest_complete)
        self.assertEqual(len(evidence.captures), 1)
        self.assertEqual(len(evidence.facts), 1)
        self.assertEqual(evidence.facts[0].next_snapshot_at_utc, snapshot_at)
        xau = next(item for item in evidence.features if item.component == "XAUUSD")
        self.assertEqual(xau.point_value, "3400.25")


if __name__ == "__main__":
    unittest.main()
