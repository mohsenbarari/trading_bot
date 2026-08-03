from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.coin_intelligence_private_ingest.train_group_trade_pair_model import _rows


class GroupTradePairModelTests(unittest.TestCase):
    def test_only_quality_eligible_confirmed_trades_become_positive_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "conversation.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE messages (
                    import_id INTEGER, message_id INTEGER, event_time_utc TEXT,
                    text TEXT, reply_to_message_id INTEGER
                );
                CREATE TABLE offers (import_id INTEGER, message_id INTEGER);
                CREATE TABLE confirmed_trades (
                    id INTEGER PRIMARY KEY, import_id INTEGER,
                    confirmation_message_id INTEGER, offer_message_id INTEGER,
                    event_time_utc TEXT
                );
                CREATE TABLE trade_market_quality (
                    trade_id INTEGER PRIMARY KEY, training_eligible INTEGER
                );
                """
            )
            connection.executemany(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                [
                    (1, 10, "2026-08-03T08:00:00Z", "offer", None),
                    (1, 11, "2026-08-03T08:01:00Z", "accepted", 10),
                    (1, 20, "2026-08-03T08:02:00Z", "bad offer", None),
                    (1, 21, "2026-08-03T08:03:00Z", "bad accepted", 20),
                    (1, 30, "2026-08-03T08:04:00Z", "other offer", None),
                    (1, 31, "2026-08-03T08:05:00Z", "unconfirmed reply", 30),
                ],
            )
            connection.executemany("INSERT INTO offers VALUES(?,?)", [(1, 10), (1, 20), (1, 30)])
            connection.executemany(
                "INSERT INTO confirmed_trades VALUES(?,?,?,?,?)",
                [
                    (1, 1, 11, 10, "2026-08-03T08:01:00Z"),
                    (2, 1, 21, 20, "2026-08-03T08:03:00Z"),
                ],
            )
            connection.executemany(
                "INSERT INTO trade_market_quality VALUES(?,?)", [(1, 1), (2, 0)]
            )
            connection.commit()
            connection.close()

            rows = _rows(database)

        positives = [row for row in rows if row[2] == 1]
        self.assertEqual(len(positives), 1)
        self.assertEqual(positives[0][1], "offer [REPLY] accepted")
        self.assertTrue(any(row[2] == 0 and "unconfirmed" in row[1] for row in rows))


if __name__ == "__main__":
    unittest.main()
