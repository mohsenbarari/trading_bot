from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingMessage,
    connect_coin_group_staging,
    initialize_coin_group_staging,
    stage_coin_group_message,
)
from core.market_intelligence.market_research_backfill import run as run_backfill
from core.market_intelligence.research_archive import (
    ResearchActor,
    ResearchArchiveError,
    ResearchArchiveKey,
    archive_research_actors,
)


class _RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.calls.append((query, parameters))


class MarketResearchArchiveTests(unittest.TestCase):
    def test_backfill_dry_run_reports_only_counts_and_source_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.sqlite3"
            staging = connect_coin_group_staging(path)
            initialize_coin_group_staging(staging)
            stage_coin_group_message(
                staging,
                CoinGroupStagingMessage(
                    group_number=1,
                    message_id=10,
                    event_time_utc="2026-08-27T10:00:00Z",
                    available_at_utc="2026-08-27T10:00:01Z",
                    text="5 امام 188600 ف",
                    sender_identity="opaque-sender",
                    sender_telegram_id="7001",
                    sender_display_name="Research User",
                ),
            )
            staging.commit()
            staging.close()
            report = run_backfill((path,), apply=False)
            self.assertEqual(report["status"], "dry-run")
            self.assertEqual(report["candidate_messages"], 1)
            self.assertEqual(report["source_messages"], {"GROUP_1": 1})
            serialized = str(report)
            self.assertNotIn("188600", serialized)
            self.assertNotIn("7001", serialized)
            self.assertNotIn("Research User", serialized)

    def test_key_file_and_lookup_are_stable_without_exposing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "research.key"
            path.write_bytes(base64.b64encode(b"k" * 64))
            first = ResearchArchiveKey.from_file(path, key_id="research:test-v1")
            second = ResearchArchiveKey.from_file(path, key_id="research:test-v1")
            digest = first.lookup_hmac(purpose="TELEGRAM_ID", value="7001")
            self.assertEqual(
                digest,
                second.lookup_hmac(purpose="TELEGRAM_ID", value="7001"),
            )
            self.assertEqual(len(digest), 32)
            self.assertNotIn(b"7001", digest)

    @unittest.skipUnless(importlib.util.find_spec("pyaes"), "pyaes not installed")
    def test_authenticated_encryption_roundtrip_and_tamper_rejection(self):
        key = ResearchArchiveKey(b"m" * 64, key_id="research:test-v1")
        sealed = key.seal("پنج امام فروش", purpose="RAW_TEXT")
        self.assertNotIn("پنج امام فروش".encode("utf-8"), sealed)
        self.assertEqual(key.open(sealed, purpose="RAW_TEXT"), "پنج امام فروش")
        tampered = sealed[:-1] + bytes([sealed[-1] ^ 1])
        with self.assertRaisesRegex(
            ResearchArchiveError, "research_archive_authentication_failed"
        ):
            key.open(tampered, purpose="RAW_TEXT")
        with self.assertRaisesRegex(
            ResearchArchiveError, "research_archive_authentication_failed"
        ):
            key.open(sealed, purpose="DISPLAY_NAME")

    @unittest.skipUnless(importlib.util.find_spec("pyaes"), "pyaes not installed")
    def test_actor_id_and_name_are_encrypted_before_database_write(self):
        key = ResearchArchiveKey(b"a" * 64, key_id="research:test-v1")
        cursor = _RecordingCursor()
        archive_research_actors(
            cursor,
            fact_id="ab" * 32,
            actors={
                "OFFERER": ResearchActor(
                    telegram_id="7001",
                    display_name="Research User",
                )
            },
            key=key,
        )
        self.assertEqual(len(cursor.calls), 1)
        parameters = cursor.calls[0][1]
        self.assertEqual(parameters[1], "OFFERER")
        self.assertEqual(key.open(parameters[2], purpose="TELEGRAM_ID"), "7001")
        self.assertEqual(
            key.open(parameters[4], purpose="DISPLAY_NAME"), "Research User"
        )
        serialized = repr(parameters)
        self.assertNotIn("7001", serialized)
        self.assertNotIn("Research User", serialized)


if __name__ == "__main__":
    unittest.main()
