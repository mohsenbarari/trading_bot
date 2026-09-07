"""Public history replay must not recreate completed work after spool rotation."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.capture_event_adapter import (
    decode_market_channel_event, initialize_capture_adapter,
    project_capture_changes, stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_store import connect_market_store, initialize_market_store
from core.market_intelligence.private_capture_telegram import SOURCE_POLICIES, build_market_event
from core.market_intelligence.private_coin_processor import _ingest_file
from tests.test_market_pipeline_stage4_capture import snapshot


class PublicReplayIdempotenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.staging = connect_coin_group_staging(self.root / "capture.sqlite")
        self.market = connect_market_store(self.root / "market.sqlite")
        initialize_capture_adapter(self.staging)
        initialize_market_store(self.market)
        self.now = datetime(2026, 9, 7, 6, tzinfo=timezone.utc)

    def tearDown(self):
        self.staging.close()
        self.market.close()
        self.temp.cleanup()

    def event(self, source, sequence=100):
        text = "XAUUSD 3500.5" if source == "XAUUSD" else "فروش 95000000"
        return decode_market_channel_event(build_market_event(
            SOURCE_POLICIES[source],
            snapshot(sequence, published=self.now - timedelta(hours=2), text=text),
            event_type="message_snapshot", received_at=self.now,
            backfill=True, explicit_backfill=True,
        ))

    def test_public_replays_do_not_requeue_completed_rows(self):
        for source in ("XAUUSD", "MELTED_AGGREGATE", "MELTED_FLOW", "USD_HERAT"):
            with self.subTest(source=source):
                event = self.event(source)
                self.assertTrue(stage_capture_event(self.staging, event).accepted)
                project_capture_changes(self.staging, self.market, as_of_utc=self.now)
                self.market.commit()
                self.staging.commit()
                before = tuple(self.staging.execute(
                    "SELECT * FROM capture_event_lineage WHERE event_id=?", (event.event_id,),
                ).fetchone())
                self.assertEqual(before[8], "PARSED")
                for _ in range(3):
                    report = stage_capture_event(self.staging, event)
                    self.assertTrue(report.duplicate)
                    self.assertFalse(report.staged_change)
                    self.assertEqual(self.staging.execute(
                        "SELECT count(*) FROM capture_dirty_market_messages WHERE source_id=?", (source,),
                    ).fetchone()[0], 0)
                after = tuple(self.staging.execute(
                    "SELECT * FROM capture_event_lineage WHERE event_id=?", (event.event_id,),
                ).fetchone())
                self.assertEqual(before, after)

    def test_repeat_preserves_pending_work_and_causal_timestamp(self):
        event = self.event("XAUUSD")
        stage_capture_event(self.staging, event)
        before = tuple(self.staging.execute("SELECT * FROM capture_dirty_market_messages").fetchone())
        self.assertTrue(stage_capture_event(self.staging, event).duplicate)
        after = tuple(self.staging.execute("SELECT * FROM capture_dirty_market_messages").fetchone())
        self.assertEqual(before, after)
        project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.assertEqual(self.staging.execute("SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 0)

    def test_new_explicit_identity_is_still_accepted(self):
        first, second = self.event("XAUUSD", 100), self.event("XAUUSD", 101)
        stage_capture_event(self.staging, first)
        project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.assertTrue(stage_capture_event(self.staging, second).accepted)
        self.assertEqual(self.staging.execute("SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 1)

    def test_retention_inode_replacement_replays_file_without_recreating_work(self):
        document = build_market_event(SOURCE_POLICIES["XAUUSD"],
            snapshot(101, published=self.now - timedelta(hours=2), text="XAUUSD 3500.5"),
            event_type="message_snapshot", received_at=self.now,
            backfill=True, explicit_backfill=True)
        content = json.dumps(document) + "\n"
        path = self.root / "market-events.jsonl"
        path.write_text(content)
        now = self.now.isoformat().replace("+00:00", "Z")
        first = _ingest_file(self.staging, stream="market", path=path,
                             now_utc=now, remaining_records=10)
        self.assertEqual(first["changes"], 1)
        project_capture_changes(self.staging, self.market, as_of_utc=self.now)
        self.staging.commit()
        self.market.commit()
        for _ in range(3):
            inode = path.stat().st_ino
            replacement = self.root / "retained.tmp"
            replacement.write_text(content)
            os.replace(replacement, path)
            self.assertNotEqual(path.stat().st_ino, inode)
            replay = _ingest_file(self.staging, stream="market", path=path,
                                  now_utc=now, remaining_records=10)
            self.assertEqual(replay["records"], 1)
            self.assertEqual(replay["duplicates"], 1)
            self.assertEqual(replay["changes"], 0)
            self.assertEqual(self.staging.execute(
                "SELECT count(*) FROM capture_dirty_market_messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
