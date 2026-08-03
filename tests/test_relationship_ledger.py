from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.relationship_ledger import append_labels, iter_labels


UTC = timezone.utc
NOW = datetime(2026, 8, 3, 8, tzinfo=UTC)


def label(*, bubble=0.02):
    return {
        "schema_version": "COIN_INTRINSIC_RELATIONSHIP_DATASET_V1_SHADOW_20260803",
        "available_at_utc": (NOW - timedelta(seconds=1)).isoformat(),
        "realized_at_utc": NOW.isoformat(),
        "commodity": "امام",
        "settlement": "TOMORROW",
        "trade_form": "PHYSICAL",
        "melted_anchor_market": "PAPER:TOMORROW:NORMAL",
        "melted_anchor_age_seconds": 5.0,
        "intrinsic_project_price": 180_000.0,
        "actual_project_price": 183_600.0,
        "bubble_ratio": bubble,
        "features": {"PAPER:TOMORROW:NORMAL|1m|offer_imbalance": 0.5},
    }


class RelationshipLedgerTests(unittest.TestCase):
    def test_append_is_idempotent_and_does_not_store_identity_data(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.sqlite3"
            first = append_labels(ledger, [label()], retention_days=None)
            second = append_labels(ledger, [label()], retention_days=None)
            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["unchanged"], 1)
            stored = list(iter_labels(ledger))
            self.assertEqual(len(stored), 1)
            self.assertNotIn("offer_text", stored[0])

    def test_corrected_label_updates_current_economic_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.sqlite3"
            append_labels(ledger, [label(bubble=0.02)], retention_days=None)
            result = append_labels(ledger, [label(bubble=0.03)], retention_days=None)
            self.assertEqual(result["updated"], 1)
            self.assertEqual(list(iter_labels(ledger))[0]["bubble_ratio"], 0.03)

    def test_raw_or_identity_data_is_rejected(self):
        unsafe = label()
        unsafe["offer_text"] = "must not persist"
        with tempfile.TemporaryDirectory() as directory:
            result = append_labels(Path(directory) / "ledger.sqlite3", [unsafe])
            self.assertEqual(result["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
