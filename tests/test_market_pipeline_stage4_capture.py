"""Stage 4 durable capture, retention, and authority-gate tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from core.market_intelligence import private_capture as capture
from core.market_intelligence.private_capture_telegram import (
    AUTHORITY_MARKER_CONTRACT,
    SOURCE_POLICIES,
    CaptureBinding,
    TelegramCaptureConfig,
    TelegramMessageSnapshot,
    build_deleted_event,
    build_group_event,
    build_market_event,
    validate_authority_marker,
)
from core.market_intelligence import private_capture_telegram as telegram_capture


UTC = timezone.utc


def snapshot(
    message_id: int,
    *,
    published: datetime,
    text: str = "fixture market event",
    edited: datetime | None = None,
    reply_to: int | None = None,
    sender_id: int | None = 7001,
) -> TelegramMessageSnapshot:
    return TelegramMessageSnapshot(
        message_id=message_id,
        published_at=published,
        edited_at=edited,
        text=text,
        has_media=False,
        media_type=None,
        action_type=None,
        entities=(),
        reply_to_message_id=reply_to,
        reply_to_top_id=None,
        grouped_id=None,
        sender_id=sender_id,
        sender_kind="user" if sender_id is not None else "unknown",
        sender_display_name=("Test User" if sender_id is not None else None),
        is_forwarded=False,
        via_bot=False,
        post=False,
        silent=False,
        pinned=False,
        noforwards=False,
        is_forum=False,
    )


def market_document(
    source: str,
    message_id: int,
    *,
    published: datetime,
    received: datetime,
    event_type: str = "message_created",
    backfill: bool = False,
    text: str = "95,000,000 فروش",
) -> dict[str, object]:
    return build_market_event(
        SOURCE_POLICIES[source],
        snapshot(message_id, published=published, text=text),
        event_type=event_type,  # type: ignore[arg-type]
        received_at=received,
        backfill=backfill,
    )


class CaptureFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = capture.CaptureState(
            self.root / "state/capture.sqlite", account="account1"
        )
        self.spool = capture.DurableEventSpool(
            self.root / "capture", account="account1"
        )
        self.engine = capture.CaptureEngine(self.state, self.spool)

    def tearDown(self) -> None:
        self.state.close()
        self.temp.cleanup()

    def test_global_sequence_duplicate_delivery_and_first_receipt_are_stable(self):
        first_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        first = market_document(
            "XAUUSD", 1, published=first_at, received=first_at + timedelta(seconds=1)
        )
        second = market_document(
            "USD_HERAT",
            2,
            published=first_at + timedelta(seconds=2),
            received=first_at + timedelta(seconds=3),
        )
        replay = market_document(
            "XAUUSD",
            1,
            published=first_at,
            received=first_at + timedelta(minutes=5),
            event_type="message_snapshot",
            backfill=True,
        )
        self.assertEqual(self.engine.accept(first).sequence, 1)
        self.assertEqual(self.engine.accept(second).sequence, 2)
        duplicate = self.engine.accept(replay)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(duplicate.sequence, 1)
        rows = [
            json.loads(raw)
            for raw in next((self.root / "capture").glob("events-*.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [row["producer"]["capture_sequence"] for row in rows], [1, 2]
        )
        self.assertEqual(
            rows[0]["producer"]["available_at_utc"], "2026-08-26T10:00:01.000000Z"
        )

    def test_reconciliation_quarantines_media_only_message_and_continues(self):
        now = telegram_capture.utc_now()
        valid = SimpleNamespace(
            id=102,
            date=now - timedelta(seconds=1),
            edit_date=None,
            message="2,350.50",
        )
        media_only = SimpleNamespace(
            id=101,
            date=now - timedelta(seconds=2),
            edit_date=None,
            message="",
            media=SimpleNamespace(),
        )

        class FakeClient:
            async def iter_messages(self, _entity, *, limit):
                self.limit = limit
                for item in (valid, media_only):
                    yield item

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._entity_by_source["XAUUSD"] = SimpleNamespace(forum=False)
        client = FakeClient()
        asyncio.run(provider._reconcile_source(client, SOURCE_POLICIES["XAUUSD"]))

        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_seen"
            ).fetchone()[0],
            1,
        )
        quarantine = self.state.connection.execute(
            "SELECT reason_code,occurrences FROM capture_quarantine"
        ).fetchone()
        self.assertEqual(tuple(quarantine), ("CAPTURE_MESSAGE_TEXT_INVALID", 1))

    def test_reconciliation_stops_after_durable_watermark_overlap(self):
        now = telegram_capture.utc_now()
        seed = market_document(
            "XAUUSD",
            1000,
            published=now - timedelta(seconds=2),
            received=now - timedelta(seconds=1),
        )
        self.engine.accept(seed)
        self.assertEqual(self.state.highest_message_id("XAUUSD"), 1000)

        class FakeClient:
            yielded = 0

            async def iter_messages(self, _entity, *, limit):
                self.limit = limit
                for message_id in range(1001, 0, -1):
                    self.yielded += 1
                    yield SimpleNamespace(
                        id=message_id,
                        date=now - timedelta(seconds=1),
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._entity_by_source["XAUUSD"] = SimpleNamespace(forum=False)
        client = FakeClient()
        asyncio.run(provider._reconcile_source(client, SOURCE_POLICIES["XAUUSD"]))

        self.assertEqual(client.yielded, 201)
        self.assertFalse(provider.reconciliation_truncated)
        self.assertEqual(self.state.highest_message_id("XAUUSD"), 1001)

    def test_fsync_failure_keeps_outbox_and_restart_recovers_without_loss(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        document = market_document(
            "XAUUSD", 3, published=moment, received=moment + timedelta(seconds=1)
        )
        real_fsync = capture.os.fsync
        failed = False

        def fail_first(descriptor: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(28, "fixture disk full")
            real_fsync(descriptor)

        with patch.object(capture.os, "fsync", side_effect=fail_first):
            with self.assertRaises(OSError):
                self.engine.accept(document)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            0,
        )
        # The bytes may have reached the file before fsync failed.  Startup
        # indexes that exact event and completes the durable outbox once.
        recovered_spool = capture.DurableEventSpool(
            self.root / "capture", account="account1"
        )
        recovered = capture.CaptureEngine(self.state, recovered_spool)
        self.assertEqual(recovered.drain(), 1)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            0,
        )

    def test_write_failure_never_acknowledges_internal_delivery(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        document = market_document(
            "XAUUSD", 4, published=moment, received=moment + timedelta(seconds=1)
        )
        with patch.object(capture.os, "write", side_effect=OSError(28, "fixture full")):
            with self.assertRaises(OSError):
                self.engine.accept(document)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            0,
        )

    def test_exact_three_day_retention_compacts_mixed_file_and_is_auditable(self):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        expired = now - timedelta(days=3, seconds=1)
        boundary = now - timedelta(days=3)
        self.engine.accept(
            market_document(
                "XAUUSD", 10, published=expired, received=expired, text="old raw"
            ),
            now=expired,
        )
        self.engine.accept(
            market_document(
                "XAUUSD", 11, published=boundary, received=boundary, text="kept raw"
            ),
            now=expired,
        )
        report = self.engine.retention(now=now)
        self.assertEqual(report["spool"]["purged_records"], 1)
        spool_text = next((self.root / "capture").glob("events-*.jsonl")).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("old raw", spool_text)
        self.assertIn("kept raw", spool_text)
        audit = next((self.root / "capture").glob("retention-audit-*.jsonl")).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("old raw", audit)
        self.assertIn('"purged_records":1', audit)

    def test_spool_repair_and_retention_never_buffer_whole_files(self):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        for message_id in range(100, 300):
            moment = now - timedelta(days=4 if message_id < 200 else 1)
            self.engine.accept(
                market_document(
                    "XAUUSD",
                    message_id,
                    published=moment,
                    received=moment,
                    text="x" * 4096,
                ),
                now=moment,
            )

        original_open = Path.open

        class NoBulkRead:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def __iter__(self):
                return iter(self.handle)

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def readlines(self, *_args, **_kwargs):
                raise AssertionError("capture spool must not bulk-read a file")

        def guarded_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            return NoBulkRead(handle) if "b" in mode else handle

        with patch.object(Path, "open", guarded_open):
            repaired = capture.DurableEventSpool(
                self.root / "capture", account="account1"
            )
            report = repaired.purge(now=now)

        self.assertEqual(report["purged_records"], 100)
        self.assertEqual(len(repaired.event_ids), 100)

    def test_live_reply_cache_skips_account1_and_is_bounded_for_account2(self):
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._remember_live_reply_parent(SOURCE_POLICIES["XAUUSD"], 1)
        self.assertEqual(provider._live_seen, set())

        with patch.object(telegram_capture, "LIVE_REPLY_CACHE_MAX_ENTRIES", 2):
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 1)
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 2)
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 3)
        self.assertEqual(
            provider._live_seen,
            {("GROUP_1", 2), ("GROUP_1", 3)},
        )

    def test_partial_tail_is_repaired_but_corrupt_middle_fails_closed(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        self.engine.accept(
            market_document(
                "XAUUSD", 20, published=moment, received=moment + timedelta(seconds=1)
            )
        )
        path = next((self.root / "capture").glob("events-*.jsonl"))
        with path.open("ab") as handle:
            handle.write(b'{"partial":')
        repaired = capture.DurableEventSpool(self.root / "capture", account="account1")
        self.assertEqual(repaired.max_sequence, 1)
        self.assertTrue(tuple((self.root / "capture").glob("quarantine-*.jsonl")))
        original = path.read_bytes()
        path.write_bytes(b"not-json\n" + original)
        with self.assertRaisesRegex(
            capture.CaptureSpoolCorruption, "corrupt_middle"
        ):
            capture.DurableEventSpool(self.root / "capture", account="account1")

    def test_heartbeat_is_per_source_and_contains_no_raw_event(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        self.engine.accept(
            market_document(
                "XAUUSD",
                30,
                published=moment,
                received=moment + timedelta(seconds=1),
                text="must not enter heartbeat",
            )
        )
        health = self.state.heartbeat(
            role="market-capture-account1",
            release_sha="a" * 40,
            mode="fixture",
            started_at_utc=capture.utc_text(moment),
            last_durable_append=self.spool.last_durable_append,
            now=moment + timedelta(seconds=2),
        )
        self.assertEqual(set(health["sources"]), capture.ACCOUNT_SOURCES["account1"])
        self.assertEqual(health["sources"]["XAUUSD"]["created"], 1)
        self.assertNotIn("must not enter heartbeat", json.dumps(health))


class CaptureContractTests(unittest.TestCase):
    def test_market_revision_identity_covers_pinned_and_silent_metadata(self):
        published = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        baseline = snapshot(1, published=published)
        policy = SOURCE_POLICIES["MELTED_AGGREGATE"]

        original = build_market_event(
            policy,
            baseline,
            event_type="message_created",
            received_at=published + timedelta(seconds=1),
            backfill=False,
        )
        pinned = build_market_event(
            policy,
            replace(baseline, pinned=True),
            event_type="message_created",
            received_at=published + timedelta(seconds=2),
            backfill=True,
        )
        silent = build_market_event(
            policy,
            replace(baseline, silent=True),
            event_type="message_created",
            received_at=published + timedelta(seconds=3),
            backfill=True,
        )

        self.assertEqual(len({original["event_id"], pinned["event_id"], silent["event_id"]}), 3)
        self.assertEqual(
            len(
                {
                    original["message"]["revision_sha256"],
                    pinned["message"]["revision_sha256"],
                    silent["message"]["revision_sha256"],
                }
            ),
            3,
        )

    def test_marked_group_identity_normalizes_for_anonymous_admin_detection(self):
        self.assertEqual(
            telegram_capture._bare_peer_id(-1_001_234_567_890), 1_234_567_890
        )
        self.assertEqual(telegram_capture._bare_peer_id(-12345), 12345)
        self.assertEqual(telegram_capture._bare_peer_id(12345), 12345)

    def test_group_reply_edit_delete_metadata_matches_existing_contract(self):
        key = b"k" * 32
        published = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        group = build_group_event(
            SOURCE_POLICIES["GROUP_1"],
            snapshot(
                2,
                published=published,
                edited=published + timedelta(seconds=2),
                reply_to=1,
                text="قبوله 3 تا 188600",
            ),
            event_type="message_edited",
            received_at=published + timedelta(seconds=3),
            backfill=False,
            reply_status="resolved_from_live_stream",
            hmac_key=key,
        )
        source, decoded = capture.validate_ingress(group, "account2")
        self.assertEqual(source, "GROUP_1")
        self.assertEqual(decoded.reply_to_message_id, 1)
        self.assertEqual(decoded.edited_at_utc, "2026-08-26T10:00:02Z")
        self.assertRegex(decoded.sender_identity or "", r"^[0-9a-f]{16}$")
        self.assertEqual(decoded.sender_telegram_id, "7001")
        self.assertEqual(decoded.sender_display_name, "Test User")
        self.assertEqual(group["schema_version"], "2.1")
        deleted = build_deleted_event(
            SOURCE_POLICIES["GROUP_1"], message_id=2, received_at=published
        )
        self.assertEqual(
            capture.validate_ingress(deleted, "account2")[1].event_type,
            "message_deleted",
        )

    def test_group_backfill_updates_gap_recovery_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = capture.CaptureState(root / "state.sqlite", account="account2")
            try:
                spool = capture.DurableEventSpool(root / "spool", account="account2")
                engine = capture.CaptureEngine(state, spool)
                moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
                document = build_group_event(
                    SOURCE_POLICIES["GROUP_2"],
                    snapshot(8, published=moment, text="fixture recovered"),
                    event_type="message_created",
                    received_at=moment + timedelta(seconds=1),
                    backfill=True,
                    reply_status="not_reply",
                    hmac_key=b"h" * 32,
                )
                engine.accept(document)
                health = state.heartbeat(
                    role="market-capture-account2",
                    release_sha="a" * 40,
                    mode="fixture",
                    started_at_utc=capture.utc_text(moment),
                    last_durable_append=spool.last_durable_append,
                    now=moment + timedelta(seconds=2),
                )
                self.assertEqual(health["sources"]["GROUP_2"]["gap_recovered"], 1)
            finally:
                state.close()

    def test_config_requires_exact_account_allowlist(self):
        bindings = tuple(
            CaptureBinding(source_code=source, peer_id=-(index + 1))
            for index, source in enumerate(sorted(capture.ACCOUNT_SOURCES["account1"]))
        )
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=bindings,
        )
        self.assertEqual(
            {item.source_code for item in config.sources},
            capture.ACCOUNT_SOURCES["account1"],
        )
        with self.assertRaises(ValidationError):
            TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account1",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account1.session",
                sources=bindings[:-1],
            )

    def test_live_authority_marker_is_release_bound_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "authority-container.json"
            marker.write_text(
                json.dumps(
                    {
                        "contract": AUTHORITY_MARKER_CONTRACT,
                        "authority": "container",
                        "role": "market-capture-account1",
                        "release_sha": "a" * 40,
                        "authorized_at_utc": "2026-08-26T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(marker, 0o600)
            validate_authority_marker(
                root, role="market-capture-account1", release_sha="a" * 40
            )
            with self.assertRaisesRegex(
                capture.CaptureRuntimeError, "marker_mismatch"
            ):
                validate_authority_marker(
                    root, role="market-capture-account1", release_sha="b" * 40
                )


if __name__ == "__main__":
    unittest.main()
