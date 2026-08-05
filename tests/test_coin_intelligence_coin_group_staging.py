"""Contract tests for bounded, private coin-group staging."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingError,
    CoinGroupStagingMessage,
    assert_staging_path_outside_repository,
    connect_coin_group_staging,
    initialize_coin_group_staging,
    list_current_staged_coin_group_messages,
    purge_expired_coin_group_staging,
    stage_coin_group_message,
)


class CoinGroupStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "private" / "coin-groups.sqlite3"
        self.connection = connect_coin_group_staging(self.database)
        initialize_coin_group_staging(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def message(self, text: str = "امام فروش 186,900 / 5 تا", **changes: object) -> CoinGroupStagingMessage:
        values: dict[str, object] = {
            "group_number": 1,
            "message_id": 17,
            "event_time_utc": "2026-08-04T10:00:00Z",
            "available_at_utc": "2026-08-04T10:00:04Z",
            "text": text,
            "sender_identity": "private offerer name",
        }
        values.update(changes)
        return CoinGroupStagingMessage(**values)  # type: ignore[arg-type]

    def test_staging_is_idempotent_and_edit_replaces_current_version(self) -> None:
        self.assertTrue(stage_coin_group_message(self.connection, self.message()))
        self.assertFalse(stage_coin_group_message(self.connection, self.message()))
        self.assertTrue(
            stage_coin_group_message(
                self.connection,
                self.message("امام فروش 187,000 / 5 تا", edited_at_utc="2026-08-04T10:01:00Z"),
            )
        )
        self.connection.commit()

        rows = list_current_staged_coin_group_messages(
            self.connection, as_of_utc="2026-08-04T10:02:00Z"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].revision, rows[0].text, rows[0].edited_at_utc), (2, "امام فروش 187,000 / 5 تا", "2026-08-04T10:01:00Z"))

    def test_staging_keeps_reply_graph_but_not_plain_sender_identity(self) -> None:
        stage_coin_group_message(
            self.connection,
            self.message(message_id=18, reply_to_message_id=17, sender_identity="private buyer name"),
        )
        self.connection.commit()

        row = self.connection.execute("SELECT sender_digest, message_text, reply_to_message_id FROM coin_group_staged_messages").fetchone()
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(coin_group_staged_messages)")}
        self.assertEqual(row["reply_to_message_id"], 17)
        self.assertEqual(len(bytes(row["sender_digest"])), 32)
        self.assertNotIn("sender_identity", columns)
        self.assertNotIn("private buyer name", str(row["sender_digest"]))
        self.assertIn("امام", row["message_text"])

    def test_retention_is_exactly_three_days_and_expired_text_is_deleted(self) -> None:
        stage_coin_group_message(self.connection, self.message())
        self.connection.commit()
        self.assertEqual(
            purge_expired_coin_group_staging(self.connection, as_of_utc="2026-08-07T10:00:03Z"),
            0,
        )
        self.assertEqual(
            purge_expired_coin_group_staging(self.connection, as_of_utc="2026-08-07T10:00:04Z"),
            1,
        )
        self.connection.commit()
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM coin_group_staged_messages").fetchone()[0], 0
        )

    def test_repository_path_is_rejected_when_runtime_policy_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as repo:
            with self.assertRaisesRegex(CoinGroupStagingError, "inside_repository"):
                assert_staging_path_outside_repository(
                    Path(repo) / "tmp" / "private.sqlite3", repository_root=Path(repo)
                )

    def test_invalid_identity_or_timestamp_fails_closed(self) -> None:
        with self.assertRaisesRegex(CoinGroupStagingError, "identity_invalid"):
            stage_coin_group_message(self.connection, self.message(group_number=3))
        with self.assertRaisesRegex(CoinGroupStagingError, "timestamp_order_invalid"):
            stage_coin_group_message(
                self.connection,
                self.message(available_at_utc="2026-08-04T09:59:59Z"),
            )


if __name__ == "__main__":
    unittest.main()
