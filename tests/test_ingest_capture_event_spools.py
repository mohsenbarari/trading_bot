"""Bounded-file tests for the one-shot capture spool consumer."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.capture_event_adapter import initialize_capture_adapter
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from scripts.ingest_capture_event_spools import _ingest_file


def _event(sequence: int) -> bytes:
    text = "95,000,000 باحواله فروش"
    payload = {
        "schema": "market_channel_event",
        "schema_version": "1.0",
        "event_id": f"20000000-0000-7000-8000-{sequence:012d}",
        "event_type": "message_created",
        "source": {
            "market": "coin_intelligence",
            "source_id": "MELTED_FLOW",
            "parser_profile": "MELTED_FLOW",
        },
        "message": {
            "message_id": str(sequence),
            "published_at_utc": "2026-08-24T10:00:00Z",
            "edited_at_utc": None,
            "text": text,
            "text_sha256": sha256(text.encode()).hexdigest(),
            "is_forwarded": False,
        },
        "producer": {"available_at_utc": "2026-08-24T10:00:01Z"},
    }
    return json.dumps(payload, separators=(",", ":")).encode() + b"\n"


class CaptureSpoolBoundTests(unittest.TestCase):
    def test_cycle_stops_at_size_snapshot_when_file_has_grown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = root / "events-2026-08-24.jsonl"
            first = _event(1)
            spool.write_bytes(first + _event(2))
            actual = spool.stat()
            staging = connect_coin_group_staging(root / "capture.sqlite3")
            try:
                initialize_capture_adapter(staging)
                size_snapshot = SimpleNamespace(
                    st_dev=actual.st_dev,
                    st_ino=actual.st_ino,
                    st_size=len(first),
                )
                with patch.object(Path, "stat", return_value=size_snapshot):
                    report = _ingest_file(
                        staging,
                        stream="market",
                        path=spool,
                        now_utc="2026-08-24T10:00:02Z",
                        remaining_records=100,
                    )
                self.assertEqual(report["records"], 1)
                self.assertEqual(report["accepted"], 1)
                cursor = staging.execute(
                    "SELECT byte_offset FROM capture_file_cursors"
                ).fetchone()
                self.assertEqual(cursor["byte_offset"], len(first))
            finally:
                staging.close()

    def test_stale_market_backlog_advances_cursor_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spool = root / "events-2026-08-24.jsonl"
            payload = _event(3)
            spool.write_bytes(payload)
            staging = connect_coin_group_staging(root / "capture.sqlite3")
            try:
                initialize_capture_adapter(staging)
                report = _ingest_file(
                    staging,
                    stream="market",
                    path=spool,
                    now_utc="2026-08-24T11:00:00Z",
                    remaining_records=100,
                    market_minimum_available_at_utc="2026-08-24T10:30:00Z",
                )
                self.assertEqual(report["stale_market_skipped"], 1)
                self.assertEqual(report["accepted"], 0)
                self.assertEqual(
                    staging.execute("SELECT COUNT(*) FROM capture_market_messages").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    staging.execute(
                        "SELECT byte_offset FROM capture_file_cursors"
                    ).fetchone()[0],
                    len(payload),
                )
            finally:
                staging.close()


if __name__ == "__main__":
    unittest.main()
