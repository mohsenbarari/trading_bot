import unittest

from scripts.report_offer_public_id_drift import compare_snapshots


def offer(
    offer_id,
    public_id,
    *,
    status="expired",
    offer_requests_count=0,
    publication_states_count=0,
    trades_count=0,
):
    return {
        "id": offer_id,
        "offer_public_id": public_id,
        "home_server": "iran",
        "status": status,
        "version_id": 1,
        "created_at": None,
        "updated_at": None,
        "expired_at": None,
        "offer_requests_count": offer_requests_count,
        "publication_states_count": publication_states_count,
        "trades_count": trades_count,
    }


class OfferPublicIdDriftReportTests(unittest.TestCase):
    def test_compare_reports_inactive_historical_exemption_candidates(self):
        report = compare_snapshots(
            {"offers": [offer(1, "ofr_iran_1", status="expired")]},
            {"offers": [offer(1, "ofr_foreign_1", status="cancelled")]},
        )

        self.assertTrue(report["dry_run"])
        self.assertFalse(report["writes_performed"])
        self.assertEqual(report["offer_public_id_mismatch_count"], 1)
        self.assertEqual(report["blocking_mismatch_count"], 0)
        self.assertEqual(
            report["mismatches"][0]["classification"],
            "inactive_historical_exemption_candidate",
        )

    def test_compare_blocks_active_or_dependent_mismatches(self):
        report = compare_snapshots(
            {
                "offers": [
                    offer(1, "ofr_iran_1", status="active"),
                    offer(2, "ofr_iran_2", status="expired", offer_requests_count=1),
                ]
            },
            {
                "offers": [
                    offer(1, "ofr_foreign_1", status="expired"),
                    offer(2, "ofr_foreign_2", status="expired"),
                ]
            },
        )

        self.assertEqual(report["offer_public_id_mismatch_count"], 2)
        self.assertEqual(report["blocking_mismatch_count"], 2)
        self.assertEqual(
            report["by_classification"],
            {
                "active_public_id_repair_blocked": 1,
                "dependent_public_id_repair_blocked": 1,
            },
        )

    def test_compare_reports_missing_rows_separately(self):
        report = compare_snapshots(
            {"offers": [offer(1, "ofr_same"), offer(2, "ofr_local_only")]},
            {"offers": [offer(1, "ofr_same"), offer(3, "ofr_peer_only")]},
        )

        self.assertEqual(report["offer_public_id_mismatch_count"], 0)
        self.assertEqual(report["missing_on_peer"], [2])
        self.assertEqual(report["missing_on_local"], [3])


if __name__ == "__main__":
    unittest.main()
