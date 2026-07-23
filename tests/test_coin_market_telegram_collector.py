from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from core.market_intelligence.bundle import load_runtime_bundle
from core.market_intelligence.producer import CoinSnapshotProducer
from core.market_intelligence.telegram_collector.collector import (
    collect_telegram_market,
)
from core.market_intelligence.telegram_collector.config import (
    SOURCES_BY_CODE,
    TelegramCollectorSettings,
    TelegramCredentials,
)
from core.market_intelligence.telegram_collector.models import (
    CollectedMessage,
)
from core.market_intelligence.telegram_collector.parsers import (
    parse_message,
    should_ignore_message,
)
from core.market_intelligence.telegram_collector.storage import (
    connect,
    infer_melted_flow_trade_sides,
    ingest_message,
    initialize,
    read_checkpoint,
)


class TelegramMarketParserTests(unittest.TestCase):
    def test_melted_gram_is_ignored_and_cash_is_physical(self) -> None:
        events = parse_message(
            "abshdh",
            (
                "🔺#آبشده‌نقدی 80,150,000\n"
                "🔺#گرم‌طلا: 18,503,128\n"
                "Just In Time 21:58:35"
            ),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].instrument, "MELTED_GOLD")
        self.assertEqual(events[0].price, Decimal("80150000"))
        self.assertEqual(events[0].trade_form, "PHYSICAL")
        self.assertEqual(events[0].settlement_term, "UNKNOWN")

    def test_melted_tomorrow_is_paper_and_explicitly_settled(self) -> None:
        event = parse_message(
            "abshdh",
            "#آبشده فردایی 80,250,000 فروش",
        )[0]

        self.assertEqual(event.market_label, "آبشده فردایی")
        self.assertEqual(event.trade_form, "PAPER")
        self.assertEqual(event.settlement_term, "TOMORROW")
        self.assertEqual(event.side, "SELL")

    def test_melted_trade_takes_precedence_over_offer_word(self) -> None:
        event = parse_message(
            "abshdh",
            "#آبشده حواله 80,250,000 خرید معامله",
        )[0]

        self.assertEqual(event.event_type, "TRADE")
        self.assertEqual(event.side, "UNKNOWN")

    def test_melted_hourly_summary_is_ignored(self) -> None:
        text = (
            "پیوت #آبشده\n#مرورنوسانات\n"
            "سقف 80,500,000\nشروع 80,000,000\nکف 79,500,000"
        )
        self.assertTrue(should_ignore_message("abshdh", text))
        self.assertEqual(parse_message("abshdh", text), [])

    def test_naghdp_axes_and_trade_are_explicit(self) -> None:
        buy = parse_message(
            "NaghdP",
            "78,750,000☀️امروز🔵خرید\nگرم: 18,179,509",
        )[0]
        trade = parse_message(
            "NaghdP",
            "79,270,000⏳باحواله✅معامله\nگرم: 18,299,552",
        )[0]

        self.assertEqual(
            (
                buy.settlement_term,
                buy.trade_form,
                buy.event_type,
                buy.side,
            ),
            ("TODAY", "PAPER", "OFFER", "BUY"),
        )
        self.assertEqual(
            (
                trade.settlement_term,
                trade.trade_form,
                trade.event_type,
                trade.side,
            ),
            ("TOMORROW", "PAPER", "TRADE", "UNKNOWN"),
        )

    def test_dollar_cash_and_settlement_are_independent(self) -> None:
        cash = parse_message(
            "ToofanHarirodOfficial",
            "هرات نقدی 114,300 فروش",
        )[0]
        today = parse_message(
            "ToofanHarirodOfficial",
            "هرات امروز 114,400 خرید",
        )[0]

        self.assertEqual(
            (cash.trade_form, cash.settlement_term),
            ("PHYSICAL", "UNKNOWN"),
        )
        self.assertEqual(
            (today.trade_form, today.settlement_term),
            ("PAPER", "TODAY"),
        )

    def test_dollar_trade_quantity_is_extracted(self) -> None:
        event = parse_message(
            "ToofanHarirodOfficial",
            "104,200 معامله ⏳ فردایی ✔️ 3میلیارد",
        )[0]

        self.assertEqual(event.event_type, "TRADE")
        self.assertEqual(event.quantity, Decimal("3"))
        self.assertEqual(event.quantity_unit, "BILLION_TOMAN")

    def test_ounce_uses_decimal_spot_quote(self) -> None:
        event = parse_message(
            "qheimat_ounce",
            "🔴4538.39 [1405-02-26 00:24:35]",
        )[0]

        self.assertEqual(event.instrument, "XAUUSD")
        self.assertEqual(event.price, Decimal("4538.39"))
        self.assertEqual(event.settlement_term, "SPOT")


class TelegramMarketStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "market.sqlite3"
        self.connection = connect(self.database)
        initialize(self.connection)
        self.addCleanup(self.connection.close)

    def ingest(
        self,
        source_code: str,
        message_id: int,
        timestamp: str,
        text: str,
        *,
        forwarded: bool = False,
    ):
        return ingest_message(
            self.connection,
            source_code=source_code,
            message=CollectedMessage(
                message_id=message_id,
                published_at_utc=timestamp,
                text=text,
                is_forwarded=forwarded,
            ),
        )

    def test_normalized_rows_do_not_retain_text_or_public_message_id(
        self,
    ) -> None:
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-07-23T10:00:00Z",
            "#آبشده نقدی 80,000,000",
        )
        event_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(price_events)"
            )
        }
        all_columns = {
            row["name"]
            for row in self.connection.execute(
                """
                SELECT name
                FROM pragma_table_info('price_events')
                """
            )
        }

        self.assertEqual(event_columns, all_columns)
        self.assertNotIn("raw_text", event_columns)
        self.assertNotIn("message_id", event_columns)
        self.assertNotIn("source_code", event_columns)
        self.assertEqual(read_checkpoint(self.connection, "MELTED_AGGREGATE"), 100)

    def test_reingest_replaces_edited_message_without_duplication(
        self,
    ) -> None:
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-07-23T10:00:00Z",
            "#آبشده نقدی 80,000,000",
        )
        self.ingest(
            "MELTED_AGGREGATE",
            100,
            "2026-07-23T10:00:00Z",
            "#آبشده نقدی 80,200,000",
        )

        rows = self.connection.execute(
            "SELECT price_num FROM price_events"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_num"], 80_200_000)

    def test_ounce_keeps_latest_quote_per_minute_even_out_of_order(
        self,
    ) -> None:
        self.ingest(
            "XAUUSD",
            2,
            "2026-07-23T10:00:59Z",
            "🔵4539.50",
        )
        result = self.ingest(
            "XAUUSD",
            1,
            "2026-07-23T10:00:01Z",
            "🔴4538.10",
        )

        rows = self.connection.execute(
            """
            SELECT event_time_utc, price_num
            FROM price_events
            WHERE instrument = 'XAUUSD'
            """
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_time_utc"], "2026-07-23T10:00:59Z")
        self.assertEqual(rows[0]["price_num"], 4539.5)
        self.assertTrue(result.ignored)
        self.assertEqual(read_checkpoint(self.connection, "XAUUSD"), 2)

    def test_forwarded_message_advances_checkpoint_without_event(
        self,
    ) -> None:
        result = self.ingest(
            "USD_HERAT",
            50,
            "2026-07-23T10:00:00Z",
            "هرات فردایی 114,500 خرید",
            forwarded=True,
        )

        self.assertTrue(result.ignored)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM price_events"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(read_checkpoint(self.connection, "USD_HERAT"), 50)

    def test_naghdp_trade_side_uses_recent_same_market_offer(self) -> None:
        self.ingest(
            "MELTED_FLOW",
            1,
            "2026-07-23T10:00:00Z",
            "79,270,000⏳باحواله🔵خرید",
        )
        self.ingest(
            "MELTED_FLOW",
            2,
            "2026-07-23T10:00:30Z",
            "79,270,000⏳باحواله✅معامله",
        )

        result = infer_melted_flow_trade_sides(self.connection)
        trade = self.connection.execute(
            """
            SELECT side, parse_confidence, parser_version
            FROM price_events
            WHERE event_type = 'TRADE'
            """
        ).fetchone()
        self.assertEqual(result["matched"], 1)
        self.assertEqual(trade["side"], "BUY")
        self.assertEqual(trade["parse_confidence"], 0.97)
        self.assertIn("+offer-link-v1", trade["parser_version"])

    def test_storage_contract_feeds_snapshot_producer(self) -> None:
        messages = (
            (
                "MELTED_AGGREGATE",
                1,
                "#آبشده نقدی 80,000,000",
            ),
            (
                "MELTED_FLOW",
                2,
                "80,200,000⏳باحواله🔵خرید",
            ),
            (
                "USD_HERAT",
                3,
                "هرات نقدی 190,000 خرید",
            ),
            (
                "XAUUSD",
                4,
                "🔵4040.50",
            ),
            (
                "MELTED_AGGREGATE",
                5,
                "#آبشده نقدی 80,100,000",
            ),
            (
                "USD_HERAT",
                6,
                "هرات نقدی 190,200 خرید",
            ),
        )
        for index, (source, message_id, text) in enumerate(messages):
            self.ingest(
                source,
                message_id,
                f"2026-07-23T10:00:{10 + index:02d}Z",
                text,
            )
        self.connection.commit()

        snapshot = CoinSnapshotProducer(load_runtime_bundle()).build(
            market_db=self.database,
            as_of=datetime(
                2026,
                7,
                23,
                10,
                1,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            len(snapshot["settlements"]["CASH"]["rates"]),
            7,
        )
        self.assertEqual(
            len(snapshot["settlements"]["TOMORROW"]["rates"]),
            7,
        )
        regime_components = {
            row["name"]
            for row in snapshot["settlements"]["CASH"][
                "market_regime"
            ]["components"]
        }
        self.assertIn("MELTED_GOLD", regime_components)
        self.assertIn("USD_HERAT", regime_components)


class TelegramMarketCredentialTests(unittest.TestCase):
    def test_credentials_are_explicit_and_redacted_from_repr(self) -> None:
        credentials = TelegramCredentials.from_environment(
            {
                "COIN_MARKET_TELEGRAM_API_ID": "12345",
                "COIN_MARKET_TELEGRAM_API_HASH": "a" * 32,
                "COIN_MARKET_TELEGRAM_PHONE": "+15551234567",
            }
        )

        rendered = repr(credentials)
        self.assertEqual(credentials.api_id, 12345)
        self.assertNotIn("a" * 32, rendered)
        self.assertNotIn("+15551234567", rendered)

    def test_runtime_paths_inside_repository_are_rejected(self) -> None:
        credentials = TelegramCredentials(
            api_id=12345,
            api_hash="a" * 32,
            phone="+15551234567",
        )
        repository = Path(__file__).resolve().parents[1]
        settings = TelegramCollectorSettings(
            credentials=credentials,
            database_path=repository / "market.sqlite3",
            session_path=Path("/tmp/coin-market-test/session"),
        )

        with self.assertRaisesRegex(
            ValueError,
            "telegram_runtime_path_inside_repository",
        ):
            settings.validate_paths(repository_root=repository)


class TelegramMarketTransportTests(unittest.TestCase):
    def test_fake_read_only_transport_writes_normalized_events(self) -> None:
        class FakeTelegramClient:
            def __init__(self, *_args, **_kwargs) -> None:
                self.disconnected = False

            async def start(self, *, phone: str) -> None:
                self.phone = phone

            async def get_entity(self, username: str):
                return username

            async def disconnect(self) -> None:
                self.disconnected = True

            async def iter_messages(self, _entity, **_kwargs):
                yield SimpleNamespace(
                    id=10,
                    date=datetime(
                        2026,
                        7,
                        23,
                        10,
                        0,
                        tzinfo=timezone.utc,
                    ),
                    message="#آبشده نقدی 80,000,000",
                    fwd_from=None,
                )

        fake_telethon = ModuleType("telethon")
        fake_telethon.TelegramClient = FakeTelegramClient
        credentials = TelegramCredentials(
            api_id=12345,
            api_hash="a" * 32,
            phone="+15551234567",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = TelegramCollectorSettings(
                credentials=credentials,
                database_path=root / "market.sqlite3",
                session_path=root / "private" / "market-reader",
            )
            output = StringIO()
            with patch.dict(
                sys.modules,
                {"telethon": fake_telethon},
            ), redirect_stdout(output):
                result = asyncio.run(
                    collect_telegram_market(
                        settings,
                        sources=(
                            SOURCES_BY_CODE["MELTED_AGGREGATE"],
                        ),
                        days=2,
                        resume_from_checkpoint=False,
                        as_of=datetime(
                            2026,
                            7,
                            23,
                            12,
                            0,
                            tzinfo=timezone.utc,
                        ),
                    )
                )
            connection = sqlite3.connect(settings.database_path)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM price_events"
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(result["MELTED_AGGREGATE"]["events"], 1)
        self.assertEqual(count, 1)
        self.assertNotIn("80,000,000", output.getvalue())
        progress = [
            json.loads(line)
            for line in output.getvalue().splitlines()
        ]
        completed = next(
            row
            for row in progress
            if row["event"] == "channel_complete"
        )
        self.assertEqual(completed["channel"], "@abshdh")
        self.assertEqual(
            completed["source_date_tehran"],
            "2026-07-23",
        )


if __name__ == "__main__":
    unittest.main()
