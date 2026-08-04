"""Offline tests for P2-A's public Telegram adapter.

No test imports Telethon, reads credentials, or contacts a channel.  The input
messages are synthetic fixtures and are only held in memory.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    read_source_checkpoint,
)
from core.market_intelligence.public_telegram.ingest import (
    PublicTelegramMessage,
    ingest_public_message,
)
from core.market_intelligence.public_telegram.parser import (
    parse_public_message,
    should_ignore_public_message,
)
from core.market_intelligence.public_telegram.sources import source_for_code
from core.market_intelligence.public_telegram.transport import (
    PublicTelegramCredentials,
    PublicTelegramTransportSettings,
    collect_public_market_telegram,
)


class PublicTelegramParserTests(unittest.TestCase):
    def test_melted_gram_and_hourly_summaries_are_ignored(self) -> None:
        events = parse_public_message(
            "MELTED_AGGREGATE",
            "🔺#آبشده‌نقدی 80,150,000\n🔺#گرم‌طلا: 18,503,128",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "MELTED_GOLD_AGGREGATE")
        self.assertEqual(str(events[0].price), "801500000")
        self.assertEqual(events[0].trade_form, "PHYSICAL")
        self.assertTrue(
            should_ignore_public_message(
                "MELTED_AGGREGATE",
                "پیوت #آبشده\n#مرورنوسانات\nسقف 80,500,000",
            )
        )

    def test_paper_melted_axes_and_naghdp_default_tomorrow(self) -> None:
        aggregate = parse_public_message(
            "MELTED_AGGREGATE",
            "#آبشده فردایی 80,250,000 فروش",
        )[0]
        flow = parse_public_message(
            "MELTED_FLOW",
            "79,270,000⏳باحواله✅معامله",
        )[0]
        self.assertEqual(
            (aggregate.settlement_term, aggregate.trade_form, aggregate.side),
            ("TOMORROW", "PAPER_NORMAL", "SELL"),
        )
        self.assertEqual(
            (flow.settlement_term, flow.trade_form, flow.event_type, flow.side),
            ("TOMORROW", "PAPER_NORMAL", "TRADE", "UNKNOWN"),
        )

    def test_dollar_cash_marker_is_the_only_physical_signal(self) -> None:
        cash = parse_public_message(
            "USD_HERAT",
            "هرات نقدی 114,300 فروش",
        )[0]
        today = parse_public_message(
            "USD_HERAT",
            "هرات امروز 114,400 خرید",
        )[0]
        self.assertEqual(
            (cash.trade_form, cash.settlement_term, str(cash.price)),
            ("PHYSICAL", "UNKNOWN", "1143000"),
        )
        self.assertEqual(
            (today.trade_form, today.settlement_term, str(today.price)),
            ("PAPER_NORMAL", "TODAY", "1144000"),
        )

    def test_ounce_is_spot_not_a_trade(self) -> None:
        event = parse_public_message(
            "XAUUSD",
            "🔴4538.39 [1405-02-26 00:24:35]",
        )[0]
        self.assertEqual(
            (event.settlement_term, event.trade_form, event.event_type, event.side),
            ("SPOT", "NOT_APPLICABLE", "QUOTE", "MID"),
        )


class PublicTelegramIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.database = Path(self._tmpdir.name) / "market.sqlite3"
        self.connection = connect_market_store(self.database)
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self._tmpdir.cleanup()

    def ingest(
        self,
        source_code: str,
        message_id: int,
        published_at_utc: str,
        text: str,
        *,
        available_at_utc: str | None = None,
        is_forwarded: bool = False,
    ):
        result = ingest_public_message(
            self.connection,
            source_code=source_code,
            message=PublicTelegramMessage(
                message_id=message_id,
                published_at_utc=published_at_utc,
                available_at_utc=available_at_utc or published_at_utc,
                text=text,
                is_forwarded=is_forwarded,
            ),
        )
        self.connection.commit()
        return result

    def test_normalized_rows_have_no_message_id_or_text(self) -> None:
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-08-04T05:35:30Z",
            "#آبشده نقدی 80,000,000",
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(market_observations)")
        }
        self.assertNotIn("raw_text", columns)
        self.assertNotIn("message_id", columns)
        self.assertEqual(read_source_checkpoint(self.connection, "MELTED_AGGREGATE"), 100)

    def test_edited_message_replaces_by_opaque_key(self) -> None:
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-08-04T05:35:30Z",
            "#آبشده نقدی 80,000,000",
        )
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-08-04T05:35:30Z",
            "#آبشده نقدی 80,200,000",
            available_at_utc="2026-08-04T05:36:30Z",
        )
        row = self.connection.execute(
            "SELECT COUNT(*) AS count, price_value FROM market_observations"
        ).fetchone()
        self.assertEqual(row["count"], 1)
        self.assertEqual(row["price_value"], "802000000")

    def test_xau_compacts_to_latest_event_in_the_minute(self) -> None:
        self.ingest("XAUUSD", 2, "2026-08-04T05:35:59Z", "🔵4539.50")
        result = self.ingest("XAUUSD", 1, "2026-08-04T05:35:01Z", "🔴4538.10")
        row = self.connection.execute(
            "SELECT event_time_utc, price_value FROM market_observations"
        ).fetchone()
        self.assertEqual(row["event_time_utc"], "2026-08-04T05:35:59Z")
        self.assertEqual(row["price_value"], "4539.50")
        self.assertTrue(result.compact_older_message_ignored)
        self.assertEqual(read_source_checkpoint(self.connection, "XAUUSD"), 2)

    def test_forwarded_message_advances_cursor_without_a_fact(self) -> None:
        result = self.ingest(
            "USD_HERAT",
            50,
            "2026-08-04T05:35:30Z",
            "هرات فردایی 114,500 خرید",
            is_forwarded=True,
        )
        self.assertTrue(result.ignored)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
            0,
        )
        self.assertEqual(read_source_checkpoint(self.connection, "USD_HERAT"), 50)

    def test_melted_flow_trade_gets_only_a_strictly_prior_matching_side(self) -> None:
        self.ingest(
            "MELTED_FLOW",
            1,
            "2026-08-04T05:35:00Z",
            "79,270,000⏳باحواله🔵خرید",
        )
        result = self.ingest(
            "MELTED_FLOW",
            2,
            "2026-08-04T05:35:30Z",
            "79,270,000⏳باحواله✅معامله",
        )
        row = self.connection.execute(
            """
            SELECT side, parse_confidence, parser_version
            FROM market_observations
            WHERE event_type = 'TRADE'
            """
        ).fetchone()
        self.assertEqual(result.linked_melted_flow_trades, 1)
        self.assertEqual(row["side"], "BUY")
        self.assertEqual(row["parse_confidence"], 0.97)
        self.assertIn("+offer-link-v1", row["parser_version"])

    def test_schema_v1_upgrades_only_the_operational_checkpoint_table(self) -> None:
        self.connection.execute("DROP TABLE market_source_checkpoints")
        self.connection.execute(
            "UPDATE market_store_metadata SET schema_version = 1 WHERE singleton = 1"
        )
        self.connection.commit()
        initialize_market_store(self.connection)
        row = self.connection.execute(
            "SELECT schema_version FROM market_store_metadata"
        ).fetchone()
        self.assertEqual(row["schema_version"], 2)
        self.assertIsNotNone(
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'market_source_checkpoints'"
            ).fetchone()
        )


class PublicTelegramTransportTests(unittest.TestCase):
    def test_fake_optional_transport_writes_only_normalized_facts(self) -> None:
        class FakeTelegramClient:
            def __init__(self, *_args, **_kwargs) -> None:
                self.disconnected = False

            async def connect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return True

            async def start(self, *, phone: str) -> None:
                self.phone = phone

            async def get_entity(self, username: str):
                return username

            async def disconnect(self) -> None:
                self.disconnected = True

            async def iter_messages(self, _entity, **_kwargs):
                yield SimpleNamespace(
                    id=10,
                    date=datetime(2026, 8, 4, 5, 35, tzinfo=timezone.utc),
                    message="#آبشده نقدی 80,000,000",
                    fwd_from=None,
                )

        fake_telethon = ModuleType("telethon")
        fake_telethon.TelegramClient = FakeTelegramClient
        credentials = PublicTelegramCredentials(
            api_id=12345,
            api_hash="a" * 32,
            phone="+15551234567",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = PublicTelegramTransportSettings(
                credentials=credentials,
                market_store_path=root / "market.sqlite3",
                session_path=root / "private" / "market-reader",
            )
            with patch.dict(sys.modules, {"telethon": fake_telethon}):
                import asyncio

                result = asyncio.run(
                    collect_public_market_telegram(
                        settings,
                        sources=(source_for_code("MELTED_AGGREGATE"),),
                        days=2,
                        resume_from_checkpoint=False,
                        as_of=datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc),
                    )
                )
            connection = connect_market_store(settings.market_store_path)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
                    1,
                )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(market_observations)")
                }
            finally:
                connection.close()
        self.assertEqual(result["MELTED_AGGREGATE"]["events"], 1)
        self.assertNotIn("message", columns)

    def test_noninteractive_run_refuses_an_unapproved_session(self) -> None:
        class FakeTelegramClient:
            start_calls = 0

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def connect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                return False

            async def start(self, *, phone: str) -> None:
                type(self).start_calls += 1

            async def disconnect(self) -> None:
                return None

        fake_telethon = ModuleType("telethon")
        fake_telethon.TelegramClient = FakeTelegramClient
        credentials = PublicTelegramCredentials(
            api_id=12345,
            api_hash="a" * 32,
            phone="+15551234567",
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            sys.modules, {"telethon": fake_telethon}
        ):
            import asyncio

            settings = PublicTelegramTransportSettings(
                credentials=credentials,
                market_store_path=Path(directory) / "market.sqlite3",
                session_path=Path(directory) / "session" / "market-reader",
            )
            with self.assertRaisesRegex(
                RuntimeError, "session_authorization_required"
            ):
                asyncio.run(
                    collect_public_market_telegram(
                        settings,
                        sources=(source_for_code("MELTED_AGGREGATE"),),
                        days=1,
                        resume_from_checkpoint=True,
                    )
                )
        self.assertEqual(FakeTelegramClient.start_calls, 0)

    def test_credentials_are_redacted_and_repository_runtime_path_is_rejected(self) -> None:
        credentials = PublicTelegramCredentials(
            api_id=12345,
            api_hash="a" * 32,
            phone="+15551234567",
        )
        self.assertNotIn("a" * 32, repr(credentials))
        self.assertNotIn("+15551234567", repr(credentials))
        repository = Path(__file__).resolve().parents[1]
        settings = PublicTelegramTransportSettings(
            credentials=credentials,
            market_store_path=repository / "market.sqlite3",
            session_path=Path("/tmp/coin-market-public-telegram-test/session"),
        )
        with self.assertRaisesRegex(ValueError, "runtime_path_inside_repository"):
            settings.validate_paths(repository_root=repository)


if __name__ == "__main__":
    unittest.main()
