"""Research lookup must retrieve only the current export batch's raw rows."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.capture_event_adapter import (
    decode_market_channel_event, initialize_capture_adapter, stage_capture_event,
)
from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingMessage, connect_coin_group_staging, stage_coin_group_message,
)
from core.market_intelligence.private_capture_telegram import SOURCE_POLICIES, build_market_event
from core.market_intelligence.research_archive import _context_rows, has_archived_research_context
from tests.test_market_pipeline_stage4_capture import snapshot


class CountingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.read_rows = 0
        self.queries = []

    def execute(self, sql, parameters=()):
        cursor = self.connection.execute(sql, parameters)
        if "FROM coin_group_fact_research_context AS c" not in sql and "FROM capture_projection_keys AS p" not in sql:
            return cursor
        rows = cursor.fetchall()
        self.read_rows += len(rows)
        self.queries.append((sql, parameters))
        class Result:
            def fetchall(self):
                return rows
        return Result()


class ResearchContextBatchTests(unittest.TestCase):
    def test_retained_context_requires_exact_fact_revision_source_and_kind(self):
        class Cursor:
            def __init__(self, row):
                self.row = row
            def execute(self, query, parameters):
                self.query, self.parameters = query, parameters
            def fetchone(self):
                return self.row
        for source, role in (("GROUP_1", "OFFER_TEXT"), ("PRIVATE_GOLD_CHANNEL", "OFFER_TEXT"),
                             ("MELTED_AGGREGATE", "SOURCE_TEXT"), ("MELTED_FLOW", "SOURCE_TEXT")):
            for row in (None, (1,)):
                cursor = Cursor(row)
                self.assertEqual(has_archived_research_context(cursor,
                    fact_id="a" * 64, fact_revision=4, source_code=source), row is not None)
                self.assertEqual(cursor.parameters, ("a" * 64, 4, source, role, role))
                self.assertIn("m.plaintext_hash=r.plaintext_hash", cursor.query)
                self.assertIn("r.fact_revision=%s", cursor.query)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.staging = connect_coin_group_staging(Path(self.temp.name) / "capture.sqlite")
        initialize_capture_adapter(self.staging)
        self.keys = []
        now = datetime(2026, 9, 7, 6, tzinfo=timezone.utc)
        stamp = "2026-09-07T06:00:00Z"
        for index in range(1, 7):
            key = sha256(str(index).encode()).digest()
            self.keys.append(key)
            if index < 4:
                stage_coin_group_message(self.staging, CoinGroupStagingMessage(
                    group_number=1, message_id=index, event_time_utc=stamp,
                    available_at_utc=stamp, text=f"5 امام {188000 + index} ف",
                    sender_identity="fixture", sender_telegram_id=str(index),
                    sender_display_name="fixture actor",
                ))
                self.staging.execute("INSERT INTO coin_group_fact_research_context VALUES(?,?,?,?,?)",
                    (key, 1, index, None, "2026-09-10T06:00:00Z"))
            else:
                event = decode_market_channel_event(build_market_event(
                    SOURCE_POLICIES["MELTED_AGGREGATE"],
                    snapshot(index, published=now, text=f"آبشده فروش {95000000 + index}"),
                    event_type="message_created", received_at=now, backfill=False,
                ))
                stage_capture_event(self.staging, event)
                self.staging.execute("INSERT INTO capture_projection_keys VALUES(?,?,?,?)",
                    ("MELTED_AGGREGATE", index, key, None))
        self.staging.commit()

    def tearDown(self):
        self.staging.close()
        self.temp.cleanup()

    def test_only_selected_group_and_channel_contexts_are_read(self):
        reference = _context_rows(self.staging, frozenset(self.keys))
        self.assertEqual(len(reference), 6)
        for key in self.keys:
            counted = CountingConnection(self.staging)
            result = _context_rows(counted, frozenset({key}))
            self.assertEqual(result, {key: reference[key]})
            self.assertEqual(counted.read_rows, 1)
            plans = [row[3] for sql, parameters in counted.queries
                     for row in self.staging.execute("EXPLAIN QUERY PLAN " + sql, parameters)]
            self.assertTrue(any("idx_capture_projection_event_key" in item for item in plans))

    def test_large_key_set_is_chunked_without_duplicates_or_unrelated_rows(self):
        keys = frozenset(self.keys[:2] + [sha256(f"missing-{n}".encode()).digest() for n in range(1100)])
        counted = CountingConnection(self.staging)
        result = _context_rows(counted, keys)
        self.assertEqual(set(result), set(self.keys[:2]))
        self.assertEqual(counted.read_rows, 2)
        self.assertTrue(all(len(parameters) <= 503 for _, parameters in counted.queries))
        self.assertEqual(len(counted.queries), 6)

    def test_existing_schema_gets_additive_lookup_index(self):
        self.staging.execute("DROP INDEX idx_capture_projection_event_key")
        initialize_capture_adapter(self.staging)
        self.assertIsNotNone(self.staging.execute("SELECT 1 FROM sqlite_master WHERE name=?",
            ("idx_capture_projection_event_key",)).fetchone())
        self.assertEqual(len(_context_rows(self.staging, frozenset(self.keys))), 6)


if __name__ == "__main__":
    unittest.main()
