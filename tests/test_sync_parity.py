import json
import unittest
from datetime import datetime, timezone

from core.sync_parity import (
    build_table_parity_snapshot,
    compare_parity_snapshots,
    synced_parity_table_names,
)


def snapshot(table_name: str, rows: list[dict]) -> dict:
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "quick",
        "tables": {
            table_name: build_table_parity_snapshot(table_name, rows),
        },
    }


class SyncParityTests(unittest.TestCase):
    def test_business_drift_is_detected(self):
        local = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100, "channel_message_id": 10}])
        peer = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 101, "channel_message_id": 10}])

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "business_drift")
        self.assertEqual(report["tables"]["offers"]["severity"], "business_drift")
        self.assertEqual(report["tables"]["offers"]["business_mismatch_count"], 1)

    def test_local_only_difference_does_not_fail_business_parity(self):
        local = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100, "channel_message_id": 10}])
        peer = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100, "channel_message_id": 99}])

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "non_business_difference")
        self.assertEqual(report["tables"]["offers"]["severity"], "local_only_difference")
        self.assertEqual(report["tables"]["offers"]["local_only_difference_count"], 1)

    def test_missing_row_is_critical_drift(self):
        local = snapshot("trades", [{"id": 1, "trade_number": 10001, "price": 100}])
        peer = snapshot(
            "trades",
            [
                {"id": 1, "trade_number": 10001, "price": 100},
                {"id": 2, "trade_number": 10002, "price": 100},
            ],
        )

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "critical_drift")
        self.assertEqual(report["tables"]["trades"]["severity"], "critical_drift")
        self.assertEqual(report["tables"]["trades"]["missing_on_local_count"], 1)

    def test_truncated_snapshot_is_incomplete_not_ok(self):
        local = {
            "status": "ok",
            "schema_version": 1,
            "mode": "deep",
            "tables": {
                "offers": build_table_parity_snapshot(
                    "offers",
                    [
                        {"id": 1, "offer_public_id": "ofr_1", "price": 100},
                        {"id": 2, "offer_public_id": "ofr_2", "price": 100},
                    ],
                    max_rows=1,
                )
            },
        }
        peer = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["severity_counts"]["incomplete"], 1)
        self.assertEqual(report["tables"]["offers"]["severity"], "incomplete")
        self.assertTrue(report["tables"]["offers"]["local_truncated"])

    def test_duplicate_identity_is_critical_drift(self):
        local = snapshot(
            "offers",
            [
                {"id": 1, "offer_public_id": "ofr_dup", "price": 100},
                {"id": 2, "offer_public_id": "ofr_dup", "price": 100},
            ],
        )
        peer = snapshot("offers", [{"id": 3, "offer_public_id": "ofr_dup", "price": 100}])

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(local["tables"]["offers"]["duplicate_identity_count"], 1)
        self.assertEqual(report["status"], "critical_drift")
        self.assertEqual(report["tables"]["offers"]["local_duplicate_identity_count"], 1)
        self.assertEqual(len(report["tables"]["offers"]["samples"]["local_duplicate_identities"]), 1)

    def test_unexplained_row_count_mismatch_is_critical_drift(self):
        local = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
        peer = snapshot("offers", [{"id": 1, "offer_public_id": "ofr_1", "price": 100}])
        peer["tables"]["offers"]["row_count"] = 2

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "critical_drift")
        self.assertEqual(report["tables"]["offers"]["severity"], "critical_drift")
        self.assertTrue(report["tables"]["offers"]["row_count_mismatch"])

    def test_volatile_difference_is_separate_from_business_drift(self):
        local = snapshot(
            "notifications",
            [
                {
                    "id": 1,
                    "dedupe_key": "trade_completed:webapp:10001:1",
                    "message": "done",
                    "updated_at": datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
                }
            ],
        )
        peer = snapshot(
            "notifications",
            [
                {
                    "id": 1,
                    "dedupe_key": "trade_completed:webapp:10001:1",
                    "message": "done",
                    "updated_at": datetime(2026, 6, 27, 12, 5, tzinfo=timezone.utc),
                }
            ],
        )

        report = compare_parity_snapshots(local, peer)

        self.assertEqual(report["status"], "non_business_difference")
        self.assertEqual(report["tables"]["notifications"]["severity"], "volatile_difference")

    def test_snapshot_redacts_sensitive_values(self):
        payload = build_table_parity_snapshot(
            "invitations",
            [
                {
                    "id": 5,
                    "account_name": "acct",
                    "mobile_number": "09370809280",
                    "token": "secret-token",
                    "short_code": "abc123",
                    "is_used": False,
                }
            ],
        )

        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        self.assertNotIn("09370809280", encoded)
        self.assertNotIn("secret-token", encoded)
        self.assertNotIn("abc123", encoded)
        self.assertEqual(payload["records"][0]["identity_fields"], ["token"])
        self.assertNotIn("identity_label", payload["records"][0])

    def test_quick_and_deep_table_lists_are_stable(self):
        quick_tables = synced_parity_table_names("quick")
        deep_tables = synced_parity_table_names("deep")

        self.assertIn("offers", quick_tables)
        self.assertIn("trades", quick_tables)
        self.assertIn("offer_publication_states", deep_tables)
        self.assertTrue(set(quick_tables).issubset(set(deep_tables)))


if __name__ == "__main__":
    unittest.main()
