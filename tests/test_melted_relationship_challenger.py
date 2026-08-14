from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.melted_relationship_challenger import (
    MeltedRelationshipRow,
    chronological_split,
    median_baseline,
    readiness,
)


UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)


def row(index: int, value: float) -> MeltedRelationshipRow:
    realized = START + timedelta(hours=index)
    return MeltedRelationshipRow(
        available_at_utc=realized - timedelta(seconds=1),
        realized_at_utc=realized,
        target_market="PAPER:TOMORROW:NORMAL",
        target_return_bps=value,
        features={"PAPER:TOMORROW:NORMAL|1m|offer_imbalance": value},
    )


class MeltedRelationshipChallengerTests(unittest.TestCase):
    def test_purged_chronological_split_keeps_sparse_data_gated(self):
        split = chronological_split([row(index, float(index)) for index in range(20)])
        self.assertGreater(split.purged_rows, 0)
        self.assertFalse(readiness(split)["ready"])

    def test_baseline_has_no_future_fit_leakage(self):
        result = median_baseline([row(1, 1.0), row(2, 3.0)], [row(3, 2.0)])
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["mae_bps"], 0.0)


if __name__ == "__main__":
    unittest.main()
