from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from core.market_intelligence.consumed_input_parity import (
    ConsumedInputParityError,
    assert_redacted,
    build_report,
    compare_rate_sets,
    compare_signal_sets,
    estimator_inputs_as_signals,
    relation,
    transition_trace,
)


AT = datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc)


def _market_snapshot(point: str = "3400", mean: str = "3399.5"):
    return {
        "generated_at_utc": AT.isoformat(),
        "snapshot_status": "PARTIAL_COIN_RATE_STATE",
        "signals": {
            "XAUUSD": {
                "status": "FRESH",
                "price_unit": "USD_PER_TROY_OUNCE",
                "latest_price": point,
                "mean_price": mean,
                "observation_count": 8,
                "last_event_utc": (AT - timedelta(seconds=1)).isoformat(),
                "method": "LATEST_WINDOW",
                "source_codes": ["XAUUSD"],
            }
        },
        "rates": {
            "items": [
                {
                    "commodity_code": "IMAM",
                    "settlement_term": "CASH",
                    "status": "ESTIMATED",
                    "estimated_project_price": "188000",
                    "lower_project_price": "187000",
                    "upper_project_price": "189000",
                    "method": "INTRINSIC",
                }
            ]
        },
    }


def _candidate_snapshot(point: str = "3400", mean: str = "3399.5"):
    return {
        "generated_at_utc": (AT + timedelta(seconds=2)).isoformat(),
        "rates": [
            {
                "instrument": "COIN_IMAM",
                "settlement": "CASH",
                "value": "188000",
                "lower_bound": "187000",
                "upper_bound": "189000",
                "method": "INTRINSIC",
            }
        ],
        "inputs": [
            {
                "component": "XAUUSD",
                "source_codes": ["XAUUSD"],
                "source_event_key": "1" * 64,
                "source_fact_id": "2" * 64,
                "fact_revision": 1,
                "occurred_at_utc": (AT - timedelta(seconds=1)).isoformat(),
                "available_at_utc": AT.isoformat(),
                "parsed_at_utc": (AT + timedelta(milliseconds=250)).isoformat(),
                "transferred_at_utc": (AT + timedelta(milliseconds=500)).isoformat(),
                "point_value": point,
                "mean_value": mean,
                "unit": "USD_PER_TROY_OUNCE",
                "sample_count": 12,
                "selection_method": "LATEST_WINDOW",
                "fallback": False,
                "freshness": "FRESH",
                "age_seconds": 3,
            }
        ],
    }


class Stage13ConsumedInputTimelineTests(unittest.TestCase):
    def test_relation_is_scale_free_and_never_returns_an_economic_number(self):
        self.assertEqual(relation(None, None), "BOTH_MISSING")
        self.assertEqual(relation(None, "1"), "PRESENCE_MISMATCH")
        self.assertEqual(relation("3400", "3400"), "EXACT")
        self.assertEqual(relation("3400", "3400.2"), "WITHIN_1_BPS")
        self.assertEqual(relation("3400", "3402"), "WITHIN_25_BPS")
        self.assertEqual(relation("3400", "3500"), "OUTSIDE_100_BPS")

    def test_scheduled_and_exact_comparisons_emit_only_relations(self):
        reference = _market_snapshot()
        candidate = _candidate_snapshot(mean="3398")
        scheduled = compare_signal_sets(
            reference["signals"],
            estimator_inputs_as_signals(candidate["inputs"]),
        )
        rates = compare_rate_sets(
            reference,
            candidate,
            candidate_is_estimator_snapshot=True,
        )
        self.assertEqual(scheduled[0]["point_relation"], "EXACT")
        self.assertEqual(scheduled[0]["mean_relation"], "WITHIN_5_BPS")
        self.assertEqual(rates[0]["point_relation"], "EXACT")
        serialized = json.dumps({"signals": scheduled, "rates": rates})
        self.assertNotIn("3400", serialized)
        self.assertNotIn("188000", serialized)

    def test_transition_latency_uses_hmac_reference_and_redacts_identity(self):
        traces = transition_trace(
            _candidate_snapshot(),
            identity_key=b"i" * 48,
            baseline=False,
        )
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces[0]["source_ref"]), 64)
        self.assertEqual(traces[0]["transferred_to_snapshot_seconds"], 1.5)
        serialized = json.dumps(traces)
        self.assertNotIn("1" * 64, serialized)
        self.assertNotIn("2" * 64, serialized)

    def test_report_tracks_version_gaps_and_stays_fail_closed_for_promotion(self):
        reference = _market_snapshot()
        candidate = _candidate_snapshot()
        signals = compare_signal_sets(
            reference["signals"],
            estimator_inputs_as_signals(candidate["inputs"]),
        )
        rates = compare_rate_sets(
            reference,
            candidate,
            candidate_is_estimator_snapshot=True,
        )
        sample = {
            "scheduled_signals": signals,
            "exact_as_of_signals": signals,
            "scheduled_rates": rates,
            "exact_as_of_rates": rates,
            "pair_skew_seconds": 2,
        }
        transitions = transition_trace(
            candidate,
            identity_key=b"i" * 48,
            baseline=False,
        )
        report = build_report(
            started_at_utc=AT,
            completed_at_utc=AT + timedelta(minutes=5),
            samples=[sample],
            candidate_snapshot_versions=[10, 11, 13],
            transitions=transitions,
        )
        self.assertEqual(report["candidate_snapshot_version_gap_count"], 1)
        self.assertFalse(report["snapshot_timeline_complete"])
        self.assertEqual(
            report["promotion_recommendation"],
            "HOLD_FULL_OPEN_MARKET_SESSION_REQUIRED",
        )
        assert_redacted(report)

    def test_redaction_rejects_an_economic_field(self):
        with self.assertRaises(ConsumedInputParityError):
            assert_redacted({"price": "3400"})


if __name__ == "__main__":
    unittest.main()
