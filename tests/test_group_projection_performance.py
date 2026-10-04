"""Window indexing must preserve the exact point-in-time quality decision."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from scripts import project_group_market_to_estimator as projection


def offer(number, seconds, *, delay=0, price=100, settlement="CASH", instrument="COIN_IMAM"):
    start = datetime(2026, 10, 3, tzinfo=timezone.utc)
    return {
        "event_key": number.to_bytes(32, "big"),
        "event_type": "OFFER", "quality_state": "ELIGIBLE", "is_conditional": 0,
        "event_time_utc": (start + timedelta(seconds=seconds)).isoformat(),
        "available_at_utc": (start + timedelta(seconds=seconds + delay)).isoformat(),
        "price_num": price, "settlement_term": settlement,
        "instrument": instrument, "trade_form": "PHYSICAL",
    }


class GroupProjectionPerformanceTests(unittest.TestCase):
    def test_index_matches_full_book_with_boundaries_and_late_availability(self):
        rows = [
            offer(1, 0), offer(2, 1), offer(3, 2),
            offer(4, 299, settlement="TOMORROW"), offer(5, 300, price=160),
            offer(6, 301, delay=100), offer(7, 302, delay=299, price=160),
            offer(8, 303, delay=301), offer(9, 304, instrument="COIN_BAHAR"),
            offer(10, 601), offer(11, 600, price=160),
        ]
        # Deliberately include non-monotonic delivery/economic order.
        rows.reverse()
        expected = {}
        for row in rows:
            reason = projection._model_exclusion_reason(row)
            if reason is None:
                candidates = [other for other in rows if projection._model_exclusion_reason(other) is None]
                reason = projection._live_book_price_exclusion_reason(row, eligible_offers=candidates)
            expected[row["event_key"]] = reason
        self.assertIn("LIVE_BOOK_PRICE_OUTLIER", expected.values())
        actual = projection._model_exclusion_reasons(rows, root_event_keys={}, root_reference_errors={})
        self.assertEqual(actual, expected)

    def test_long_history_does_not_rescan_each_entire_book(self):
        rows = [offer(number + 1, number * 601) for number in range(2000)]
        original = projection._live_book_price_exclusion_reason
        sizes = []

        def inspected(row, **kwargs):
            sizes.append(len(kwargs["eligible_offers"]))
            return original(row, **kwargs)

        with patch.object(projection, "_live_book_price_exclusion_reason", side_effect=inspected):
            result = projection._model_exclusion_reasons(rows, root_event_keys={}, root_reference_errors={})
        self.assertEqual(len(result), 2000)
        self.assertTrue(all(reason is None for reason in result.values()))
        self.assertEqual(max(sizes), 1)


if __name__ == "__main__":
    unittest.main()
