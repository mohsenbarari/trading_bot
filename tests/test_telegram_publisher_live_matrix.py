import unittest

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
