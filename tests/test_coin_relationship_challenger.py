from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.coin_relationship_challenger import (
    CoinBubbleRow,
    chronological_split,
    median_baseline,
    readiness,
)


UTC = timezone.utc
START = datetime(2026, 8, 1, tzinfo=UTC)


def row(hour: int, bubble: float) -> CoinBubbleRow:
    realized = START + timedelta(hours=hour)
    return CoinBubbleRow(
        available_at_utc=realized - timedelta(seconds=1),
        realized_at_utc=realized,
        commodity="امام",
        settlement="TOMORROW",
        trade_form="PHYSICAL",
        bubble_ratio=bubble,
        features={"PAPER:TOMORROW:NORMAL|1m|offer_imbalance": bubble},
    )


class CoinRelationshipChallengerTests(unittest.TestCase):
    def test_split_purges_boundary_context_and_gates_sparse_data(self):
        split = chronological_split([row(index, index / 100.0) for index in range(20)])
        self.assertGreater(split.purged_rows, 0)
        self.assertFalse(readiness(split)["ready"])
        self.assertLess(len(split.fit) + len(split.validation) + len(split.test), 20)

    def test_baseline_uses_fit_market_median_only(self):
        fit = [row(1, 0.01), row(2, 0.03), row(3, 0.05)]
        result = median_baseline(fit, [row(4, 0.03)])
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["mae"], 0.0)


if __name__ == "__main__":
    unittest.main()
