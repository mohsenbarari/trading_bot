from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.private_pipeline_contracts import content_hash
from core.market_intelligence.shadow_parity import (
    build_lane_evidence_from_market_store,
    compare_shadow_lanes,
    sign_parity_report,
    verify_parity_report,
)


START = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)
MODEL_HASH = sha256(b"same-model-artifact").hexdigest()
SIGNING_KEY = b"stage12-test-signing-key-material-32-bytes-minimum"


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


def lane(role, count=20):
    captures = []
    facts = []
    for index in range(count):
        key = f"{index + 1:064x}"
        occurred = START + timedelta(seconds=index)
        available = occurred + timedelta(milliseconds=20)
        dims = dimensions(price=str(188600 + index))
        captures.append(
            {
                "event_key": key,
                "source_code": "GROUP_1",
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
            }
        )
        facts.append(
            {
                "event_key": key,
                "source_code": "GROUP_1",
                "eligible": True,
                "dimensions": dims,
                "parser_fingerprint": content_hash(dims),
                "lifecycle_state": "ACTIVE",
                "occurred_at_utc": occurred.isoformat(),
                "available_at_utc": available.isoformat(),
                "parsed_at_utc": (occurred + timedelta(milliseconds=100)).isoformat(),
                "transferred_at_utc": (occurred + timedelta(milliseconds=200)).isoformat(),
                "next_snapshot_at_utc": (occurred + timedelta(seconds=5)).isoformat(),
            }
        )
    evaluation = START + timedelta(minutes=2)
    features = [
        {
            "evaluation_at_utc": evaluation.isoformat(),
            "component": "XAUUSD",
            "point_value": "3400.25",
            "mean_value": "3400.20",
            "unit": "USD_PER_TROY_OUNCE",
            "sample_count": 5,
            "source_event_key": f"{1:064x}",
            "freshness": "FRESH",
        },
        {
            "evaluation_at_utc": evaluation.isoformat(),
            "component": "USDT_IRT",
            "point_value": "96000",
            "mean_value": "95990",
            "unit": "TOMAN_PER_USDT",
            "sample_count": 6,
            "source_event_key": f"{2:064x}",
            "freshness": "FRESH",
        },
    ]
    input_hash = content_hash(features)
    return {
        "contract": "market_shadow_lane/1.0",
        "lane": role,
        "window_start_utc": START.isoformat(),
        "window_end_utc": evaluation.isoformat(),
        "capture_manifest_complete": True,
        "model_artifact_hash": MODEL_HASH,
        "captures": captures,
        "facts": facts,
        "features": features,
        "estimates": [
            {
                "evaluation_at_utc": evaluation.isoformat(),
                "model_artifact_hash": MODEL_HASH,
                "input_snapshot_hash": input_hash,
                "instrument": "COIN_IMAM",
                "settlement": "CASH",
                "value": "188700",
                "lower_bound": "188000",
                "upper_bound": "189000",
            }
        ],
        "transport": {
            "unresolved_sequence_gap_count": 0,
            "duplicate_eligible_fact_count": 0,
            "rejected_delivery_count": 0,
            "receiver_checkpoint_count": 1 if role == "PRIVATE_SHADOW" else 0,
        },
    }


def soak(mode="HISTORICAL_REPLAY", full=False):
    return {
        "contract": "market_failure_soak/1.0",
        "evidence_mode": mode,
        "started_at_utc": START.isoformat(),
        "completed_at_utc": (START + timedelta(hours=2)).isoformat(),
        "full_market_session": full,
        "receiver_restart_passed": True,
        "route_partition_passed": True,
        "lost_ack_passed": True,
        "rollback_passed": True,
        "disk_failure_passed": True,
    }


class MarketPipelineStage12ParityTests(unittest.TestCase):
    def compare(self, private=None, labels=(), soak_value=None):
        return compare_shadow_lanes(
            lane("LEGACY"),
            private or lane("PRIVATE_SHADOW"),
            soak_value=soak_value or soak(),
            labels_value=labels,
        )

    def test_clean_replay_holds_only_for_live_open_market_session(self):
        report = self.compare()
        self.assertEqual(report["severity_1_count"], 0)
        self.assertEqual(report["severity_2_count"], 0)
        self.assertEqual(report["source_to_snapshot_p95_seconds"], 5.0)
        self.assertEqual(
            report["promotion_recommendation"], "HOLD_LIVE_OPEN_MARKET_REQUIRED"
        )
        live = self.compare(soak_value=soak("LIVE_OPEN_MARKET", True))
        self.assertEqual(live["promotion_recommendation"], "PROMOTE_PRIVATE_PRIMARY")

    def test_capture_loss_and_transport_gap_are_severity_one(self):
        private = lane("PRIVATE_SHADOW")
        private["captures"] = private["captures"][1:]
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

    def test_unit_and_external_consumed_value_mismatch_are_severity_one(self):
        private = lane("PRIVATE_SHADOW")
        dims = dimensions(unit="TOMAN_PER_COIN")
        private["facts"][0]["dimensions"] = dims
        private["facts"][0]["parser_fingerprint"] = content_hash(dims)
        private["features"][0]["point_value"] = "3401"
        report = self.compare(private)
        self.assertGreaterEqual(report["severity_1_count"], 2)
        self.assertEqual(report["consumed_external_mismatch_count"], 1)

    def test_same_input_estimator_difference_is_severity_one(self):
        private = lane("PRIVATE_SHADOW")
        private["estimates"][0]["value"] = "188800"
        report = self.compare(private)
        self.assertEqual(report["same_input_estimator_mismatch_count"], 1)
        self.assertGreaterEqual(report["severity_1_count"], 1)

    def test_missing_snapshot_trace_and_slow_p95_block(self):
        private = lane("PRIVATE_SHADOW")
        private["facts"][0]["next_snapshot_at_utc"] = None
        for fact in private["facts"][1:]:
            occurred = datetime.fromisoformat(fact["occurred_at_utc"])
            fact["next_snapshot_at_utc"] = (occurred + timedelta(seconds=8)).isoformat()
        report = self.compare(private)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("SNAPSHOT_TRACE_MISSING", codes)
        self.assertIn("SOURCE_TO_SNAPSHOT_P95_EXCEEDED", codes)

    def test_incomplete_capture_manifest_blocks_promotion(self):
        private = lane("PRIVATE_SHADOW")
        private["capture_manifest_complete"] = False
        report = self.compare(private)
        self.assertGreaterEqual(report["severity_1_count"], 1)

    def test_report_signature_detects_tampering(self):
        signed = sign_parity_report(
            self.compare(), key=SIGNING_KEY, key_id="stage12-test:v1"
        )
        self.assertTrue(verify_parity_report(signed, key=SIGNING_KEY))
        tampered = deepcopy(signed)
        tampered["private_fact_count"] += 1
        self.assertFalse(verify_parity_report(tampered, key=SIGNING_KEY))

    def test_read_only_market_store_builder_uses_explicit_capture_manifest(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        event_time = now - timedelta(seconds=10)
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
                window_end_utc=now,
                model_artifact_hash=MODEL_HASH,
                capture_manifest=manifest,
                snapshot_times={event_key.hex(): now},
            )
        self.assertTrue(evidence.capture_manifest_complete)
        self.assertEqual(len(evidence.captures), 1)
        self.assertEqual(len(evidence.facts), 1)
        xau = next(item for item in evidence.features if item.component == "XAUUSD")
        self.assertEqual(xau.point_value, "3400.25")


if __name__ == "__main__":
    unittest.main()
