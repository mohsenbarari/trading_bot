import unittest

from scripts.align_change_log_source_sequence import (
    normalize_database_url,
    plan_change_log_sequence_alignment,
)


class ChangeLogSourceSequenceAlignmentTests(unittest.TestCase):
    def test_called_sequence_below_receiver_floor_moves_strictly_above_it(self):
        target, next_value = plan_change_log_sequence_alignment(
            last_value=55,
            is_called=True,
            max_change_id=55,
            required_floor=893155,
        )

        self.assertEqual(target, 893155)
        self.assertEqual(next_value, 893156)

    def test_sequence_already_above_floor_is_only_verified(self):
        target, next_value = plan_change_log_sequence_alignment(
            last_value=893200,
            is_called=True,
            max_change_id=893200,
            required_floor=893155,
        )

        self.assertIsNone(target)
        self.assertEqual(next_value, 893201)

    def test_existing_row_id_is_never_reused_when_it_exceeds_floor(self):
        target, next_value = plan_change_log_sequence_alignment(
            last_value=100,
            is_called=True,
            max_change_id=900000,
            required_floor=893155,
        )

        self.assertEqual(target, 900000)
        self.assertEqual(next_value, 900001)

    def test_uncalled_next_value_is_preserved_when_strictly_safe(self):
        target, next_value = plan_change_log_sequence_alignment(
            last_value=893156,
            is_called=False,
            max_change_id=0,
            required_floor=893155,
        )

        self.assertIsNone(target)
        self.assertEqual(next_value, 893156)

    def test_negative_floor_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_change_log_sequence_alignment(
                last_value=1,
                is_called=False,
                max_change_id=0,
                required_floor=-1,
            )

    def test_async_database_url_is_normalized_for_psycopg2(self):
        self.assertEqual(
            normalize_database_url("postgresql+asyncpg://db/example"),
            "postgresql://db/example",
        )


if __name__ == "__main__":
    unittest.main()
