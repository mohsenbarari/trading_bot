import unittest
from collections import Counter

from scripts.run_telegram_publisher_live_matrix import (
    MATRIX_INGRESS_INTERVAL_SECONDS,
    build_live_matrix_workload,
)


class TelegramPublisherLiveMatrixTests(unittest.TestCase):
    def test_builds_exact_source_ratio_and_interaction_mix(self):
        workload = build_live_matrix_workload(
            total_offers=1000,
            bot_offers=600,
            webapp_offers=400,
            interaction_count=10,
            ingress_interval_seconds=MATRIX_INGRESS_INTERVAL_SECONDS,
        )

        self.assertEqual(len(workload.origins), 1000)
        self.assertEqual(workload.origins.count("bot"), 600)
        self.assertEqual(workload.origins.count("webapp"), 400)
        self.assertEqual(workload.origins[:10], ("bot",) * 6 + ("webapp",) * 4)
        self.assertEqual(
            Counter(workload.scenarios),
            {
                "direct_wholesale_trade": 100,
                "direct_retail_lot_trade": 100,
                "overtime_approved_trade": 30,
                "overtime_owner_rejected": 30,
                "overtime_decision_timeout": 240,
                "manual_expiry": 100,
                "natural_expiry": 400,
            },
        )
        for start, stop in (
            (0, 100),
            (100, 200),
            (200, 230),
            (230, 260),
            (260, 500),
            (500, 600),
            (600, 1000),
        ):
            self.assertEqual(workload.origins[start:stop].count("bot"), (stop - start) * 3 // 5)
            self.assertEqual(workload.origins[start:stop].count("webapp"), (stop - start) * 2 // 5)
        self.assertEqual(workload.interaction_origins, ("bot",) * 6 + ("webapp",) * 4)
        self.assertEqual(len(workload.interaction_offsets_seconds), 10)
        self.assertEqual(
            tuple(sorted(workload.interaction_offsets_seconds)),
            workload.interaction_offsets_seconds,
        )

    def test_rejects_any_non_two_per_second_rate(self):
        with self.assertRaisesRegex(RuntimeError, "two_per_second"):
            build_live_matrix_workload(
                total_offers=1000,
                bot_offers=600,
                webapp_offers=400,
                interaction_count=10,
                ingress_interval_seconds=0.51,
            )


if __name__ == "__main__":
    unittest.main()
