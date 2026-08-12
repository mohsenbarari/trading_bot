import unittest

from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES
from scripts.run_telegram_publisher_b2b_matrix import (
    MATRIX_INGRESS_INTERVAL_SECONDS,
    build_matrix_workload,
)


class TelegramPublisherB2BMatrixTests(unittest.TestCase):
    def test_matrix_evenly_assigns_1000_commands_and_spreads_interactions(self):
        workload = build_matrix_workload(
            lanes=TELEGRAM_PUBLISHER_IDENTITIES,
            total_commands=1000,
            interaction_count=10,
            ingress_interval_seconds=MATRIX_INGRESS_INTERVAL_SECONDS,
        )

        self.assertEqual(len(workload.lane_sequence), 1000)
        self.assertEqual(
            {lane: workload.lane_sequence.count(lane) for lane in TELEGRAM_PUBLISHER_IDENTITIES},
            {lane: 200 for lane in TELEGRAM_PUBLISHER_IDENTITIES},
        )
        self.assertEqual(len(workload.interaction_offsets_seconds), 10)
        self.assertEqual(
            tuple(sorted(workload.interaction_offsets_seconds)),
            workload.interaction_offsets_seconds,
        )
        self.assertLess(workload.interaction_offsets_seconds[-1], 500.0)


if __name__ == "__main__":
    unittest.main()
