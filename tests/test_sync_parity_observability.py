import unittest
from datetime import datetime, timezone

from core.sync_parity_observability import (
    infer_parity_comparison_mode,
    strict_alert_gate_from_parity_summary,
    summarize_parity_comparison,
)


class SyncParityObservabilityTests(unittest.TestCase):
    def test_summarize_parity_comparison_counts_operator_fields(self):
        comparison = {
            "status": "business_drift",
            "mode": "deep",
            "compared_at": "2026-06-28T05:00:00Z",
            "severity_counts": {
                "business_drift": 2,
                "critical_drift": 1,
                "incomplete": 0,
                "local_only_difference": 3,
                "volatile_difference": 4,
            },
            "tables": {
                "offers": {
                    "local_truncated": True,
                    "peer_truncated": False,
                    "local_duplicate_identity_count": 2,
                    "peer_duplicate_identity_count": 1,
                }
            },
        }

        summary = summarize_parity_comparison(
            comparison,
            now=datetime(2026, 6, 28, 5, 1, 0, tzinfo=timezone.utc),
            max_age_seconds=120,
        )

        self.assertEqual(summary["status"], "business_drift")
        self.assertTrue(summary["fresh"])
        self.assertEqual(summary["business_drift_count"], 2)
        self.assertEqual(summary["critical_drift_count"], 1)
        self.assertEqual(summary["truncated_table_count"], 1)
        self.assertEqual(summary["duplicate_identity_count"], 3)
        self.assertEqual(summary["non_business_difference_count"], 7)

    def test_strict_alert_gate_blocks_missing_stale_and_business_drift(self):
        self.assertEqual(strict_alert_gate_from_parity_summary(None)["reason"], "missing_parity_evidence")
        stale = {
            "status": "ok",
            "fresh": False,
            "business_drift_count": 0,
            "critical_drift_count": 0,
        }
        self.assertEqual(strict_alert_gate_from_parity_summary(stale)["reason"], "stale_parity_evidence")
        drift = {
            "status": "business_drift",
            "fresh": True,
            "business_drift_count": 1,
            "critical_drift_count": 0,
        }
        self.assertEqual(strict_alert_gate_from_parity_summary(drift)["reason"], "parity_status_business_drift")

    def test_strict_alert_gate_blocks_unknown_or_incomplete_summary_counts(self):
        unknown = {
            "status": "unknown",
            "fresh": True,
            "business_drift_count": 0,
            "critical_drift_count": 0,
        }
        self.assertEqual(strict_alert_gate_from_parity_summary(unknown)["reason"], "parity_status_unknown")

        incomplete = {
            "status": "ok",
            "fresh": True,
            "business_drift_count": 0,
            "critical_drift_count": 0,
            "incomplete_count": 1,
            "truncated_table_count": 1,
        }
        self.assertEqual(
            strict_alert_gate_from_parity_summary(incomplete)["reason"],
            "parity_incomplete_tables",
        )

    def test_strict_alert_gate_passes_fresh_ok_or_non_business_difference(self):
        ok = {
            "status": "non_business_difference",
            "fresh": True,
            "business_drift_count": 0,
            "critical_drift_count": 0,
        }
        self.assertEqual(strict_alert_gate_from_parity_summary(ok)["status"], "passed")

    def test_strict_alert_gate_blocks_unknown_truncated_and_duplicate_evidence(self):
        unknown = {"status": "unknown", "fresh": True}
        self.assertEqual(strict_alert_gate_from_parity_summary(unknown)["reason"], "parity_status_unknown")
        truncated = {"status": "ok", "fresh": True, "truncated_table_count": 1}
        self.assertEqual(strict_alert_gate_from_parity_summary(truncated)["reason"], "parity_truncated_tables")
        duplicate = {"status": "ok", "fresh": True, "duplicate_identity_count": 1}
        self.assertEqual(strict_alert_gate_from_parity_summary(duplicate)["reason"], "parity_duplicate_identities")
        incomplete = {"status": "ok", "fresh": True, "incomplete_count": 1}
        self.assertEqual(strict_alert_gate_from_parity_summary(incomplete)["reason"], "parity_incomplete_tables")

    def test_infer_parity_comparison_mode(self):
        self.assertEqual(infer_parity_comparison_mode({"mode": "deep"}, {"mode": "deep"}), "deep")
        self.assertEqual(infer_parity_comparison_mode({"mode": "quick"}, {"mode": "deep"}), "mixed")
        self.assertEqual(infer_parity_comparison_mode({}, {}), "unknown")


if __name__ == "__main__":
    unittest.main()
