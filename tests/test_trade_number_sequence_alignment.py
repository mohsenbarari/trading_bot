import unittest

from scripts.align_trade_number_sequence import (
    expected_trade_number_parity,
    normalize_database_url,
    plan_trade_number_alignment,
)


class TradeNumberSequenceAlignmentTests(unittest.TestCase):
    def test_server_modes_use_disjoint_trade_number_parity(self):
        self.assertEqual(expected_trade_number_parity("foreign"), 0)
        self.assertEqual(expected_trade_number_parity("iran"), 1)

        with self.assertRaises(RuntimeError):
            expected_trade_number_parity("")
        with self.assertRaises(RuntimeError):
            expected_trade_number_parity("unknown")

    def test_verified_sequence_keeps_current_next_value(self):
        target, next_value = plan_trade_number_alignment(
            server_mode="foreign",
            last_value=10008,
            is_called=True,
            max_partition_trade_number=10008,
        )

        self.assertIsNone(target)
        self.assertEqual(next_value, 10010)

    def test_wrong_foreign_parity_is_repaired_without_reusing_consumed_value(self):
        target, next_value = plan_trade_number_alignment(
            server_mode="foreign",
            last_value=10011,
            is_called=True,
            max_partition_trade_number=None,
        )

        self.assertEqual(target, 10012)
        self.assertEqual(next_value, 10012)

    def test_wrong_iran_parity_is_repaired_above_existing_iran_numbers(self):
        target, next_value = plan_trade_number_alignment(
            server_mode="iran",
            last_value=10012,
            is_called=True,
            max_partition_trade_number=10015,
        )

        self.assertEqual(target, 10017)
        self.assertEqual(next_value, 10017)

    def test_uncalled_candidate_is_preserved_when_safe(self):
        target, next_value = plan_trade_number_alignment(
            server_mode="iran",
            last_value=10015,
            is_called=False,
            max_partition_trade_number=10013,
        )

        self.assertIsNone(target)
        self.assertEqual(next_value, 10015)

    def test_candidate_colliding_with_synced_partition_is_advanced(self):
        target, next_value = plan_trade_number_alignment(
            server_mode="foreign",
            last_value=10014,
            is_called=False,
            max_partition_trade_number=10014,
        )

        self.assertEqual(target, 10016)
        self.assertEqual(next_value, 10016)

    def test_async_database_url_is_normalized_for_psycopg2(self):
        self.assertEqual(
            normalize_database_url("postgresql+asyncpg://db/example"),
            "postgresql://db/example",
        )


if __name__ == "__main__":
    unittest.main()
