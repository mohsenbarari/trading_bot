import unittest
from unittest.mock import MagicMock

from scripts.sample_telegram_stage4_offer_shapes import (
    Stage4SamplerSafetyError,
    build_sanitized_fixture,
    database_fingerprint,
    sample_read_only,
)


class TelegramQueueStage4SamplerTests(unittest.TestCase):
    def test_database_fingerprint_excludes_credentials(self):
        first = database_fingerprint("postgresql://alice:one@db.example/stage")
        second = database_fingerprint("postgresql://bob:two@db.example/stage")
        self.assertEqual(first, second)
        self.assertNotIn("alice", first)
        self.assertNotIn("one", first)

    def test_fixture_contains_shapes_but_no_identity_fields(self):
        fixture = build_sanitized_fixture(
            [
                ("commodity-a", "buy", "cash", 10, 1000, True, None),
                ("commodity-a", "sell", "tomorrow", 10, 1100, False, [4, 6]),
            ]
        )
        template = fixture["commodities"][0]["templates"][1]
        self.assertEqual(
            set(template),
            {
                "offer_type",
                "settlement_type",
                "quantity",
                "price",
                "lot_shape",
                "lot_sizes",
            },
        )
        self.assertTrue(all(value is False for value in fixture["privacy_contract"].values()))
        self.assertEqual(template["lot_shape"], "2")

    def test_transaction_is_read_only_before_select_and_always_rolls_back(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("on",)
        cursor.fetchall.return_value = [
            ("commodity-a", "buy", "cash", 10, 1000, True, None)
        ]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        fixture = sample_read_only(connection, seed="seed", limit=10)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("READ ONLY", statements[0])
        self.assertEqual(statements[2], "SHOW transaction_read_only")
        self.assertIn("SELECT c.name", statements[3])
        connection.rollback.assert_called_once()
        cursor.close.assert_called_once()
        self.assertEqual(fixture["schema_version"], 1)

    def test_sampler_refuses_when_database_does_not_confirm_read_only(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("off",)
        connection = MagicMock()
        connection.cursor.return_value = cursor
        with self.assertRaisesRegex(Stage4SamplerSafetyError, "not_read_only"):
            sample_read_only(connection, seed="seed", limit=10)
        connection.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
