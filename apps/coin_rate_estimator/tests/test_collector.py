import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from live_server import _channel_backfill_cutoff, live_collection_loop
from telegram_price_collector.collector import _emit_progress
from telegram_price_collector.config import (
    SOURCE_PEER_IDS,
    Settings,
    resolve_configured_channel,
)
from telegram_price_collector.db import connect, initialize, upsert_raw_post
from telegram_price_collector.models import RawPost


class CollectorProgressTests(unittest.TestCase):
    def test_progress_contains_channel_and_tehran_date(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            _emit_progress(
                "channel_progress",
                channel_username="abshdh",
                channel_title="JUST IN TIME",
                source_time=datetime(2026, 7, 20, 9, 10, 43, tzinfo=timezone.utc),
                messages=1_000,
                price_events=1_850,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["channel_username"], "@abshdh")
        self.assertEqual(payload["channel_title"], "JUST IN TIME")
        self.assertEqual(payload["source_date_tehran"], "2026-07-20")
        self.assertEqual(payload["source_datetime_tehran"], "2026-07-20T12:40:43+03:30")
        self.assertEqual(payload["messages"], 1_000)

    def test_resolve_configured_channel_prefers_peer_id_and_preserves_parser_key(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[object] = []

            async def get_entity(self, target: object):
                self.calls.append(target)
                if target == SOURCE_PEER_IDS["MELTED_FLOW"]:
                    return SimpleNamespace(
                        id=1209799291,
                        username=None,
                        title="Naghd Private",
                    )
                raise ValueError('No user has "naghdp" as username')

        client = FakeClient()
        entity, source_code, parser_channel, title = asyncio.run(
            resolve_configured_channel(client, "NaghdP")
        )
        self.assertEqual(client.calls, [SOURCE_PEER_IDS["MELTED_FLOW"]])
        self.assertEqual(entity.id, 1209799291)
        self.assertEqual(source_code, "MELTED_FLOW")
        self.assertEqual(parser_channel, "NaghdP")
        self.assertEqual(title, "Naghd Private")

    def test_channel_backfill_cutoff_extends_for_recent_outage_with_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "market.sqlite3")
            try:
                initialize(conn)
                now = datetime(2026, 11, 14, 8, 45, 0, tzinfo=timezone.utc)
                upsert_raw_post(
                    conn,
                    source_code="MELTED_AGGREGATE",
                    post=RawPost(
                        message_id=101,
                        published_at_utc="2026-11-14T06:15:26Z",
                        raw_text="sample",
                    ),
                )
                conn.commit()
                cutoff = _channel_backfill_cutoff(
                    conn,
                    "MELTED_AGGREGATE",
                    now_utc=now,
                    backfill_minutes=15,
                    max_gap_minutes=360,
                )
                self.assertEqual(
                    cutoff,
                    datetime(2026, 11, 14, 6, 13, 26, tzinfo=timezone.utc),
                )
                capped = _channel_backfill_cutoff(
                    conn,
                    "MELTED_AGGREGATE",
                    now_utc=now + timedelta(hours=12),
                    backfill_minutes=15,
                    max_gap_minutes=360,
                )
                self.assertEqual(capped, (now + timedelta(hours=12)) - timedelta(minutes=360))
            finally:
                conn.close()

    def test_live_collection_loop_isolates_single_broken_channel(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)

        class FakeEvents:
            class NewMessage:
                def __init__(self, chats=None) -> None:
                    self.chats = chats

        class FakeTelegramClient:
            def __init__(self, *_args, **_kwargs) -> None:
                self.handlers = []

            async def connect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            def is_connected(self) -> bool:
                return True

            async def get_entity(self, target: object):
                if target in (SOURCE_PEER_IDS["MELTED_AGGREGATE"], "abshdh"):
                    return SimpleNamespace(id=1491344050, username="abshdh", title="Abshodeh")
                raise ValueError("channel unavailable")

            async def iter_messages(self, _entity):
                yield SimpleNamespace(
                    id=501,
                    date=now - timedelta(minutes=2),
                    message="#آبشده نقدی 80,000,000",
                    fwd_from=None,
                )

            def on(self, _event_builder):
                def decorator(fn):
                    self.handlers.append(fn)
                    return fn
                return decorator

            async def run_until_disconnected(self) -> None:
                await asyncio.sleep(0.01)

            async def disconnect(self) -> None:
                return None

        fake_telethon = ModuleType("telethon")
        fake_telethon.TelegramClient = FakeTelegramClient
        fake_telethon.events = FakeEvents

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "market_prices.sqlite3"
            health_path = root / "public-telegram-health.json"
            fake_settings = Settings(
                api_id=12345,
                api_hash="a" * 32,
                phone="+15550000000",
                db_path=db_path,
                session_path=root / "session",
            )
            with (
                patch.dict("sys.modules", {"telethon": fake_telethon}),
                patch(
                    "live_server.Settings.with_interactive_credentials",
                    return_value=fake_settings,
                ),
            ):
                asyncio.run(
                    live_collection_loop(
                        db_path,
                        backfill_minutes=15,
                        channels=("NaghdP", "abshdh"),
                        health_state_path=health_path,
                    )
                )
            health = json.loads(health_path.read_text(encoding="utf-8"))
            entry = health["sources"]["PUBLIC_MARKET_TELEGRAM"]
            self.assertEqual(entry["status"], "HEALTHY")
            self.assertEqual(entry["details"]["channel_count"], 1)
            self.assertEqual(entry["details"]["unavailable_channel_count"], 1)
            conn = connect(db_path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM price_events").fetchone()[0]
            finally:
                conn.close()
            self.assertGreaterEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
