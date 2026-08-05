from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
import unittest
from unittest.mock import patch

from telegram_price_collector.db import initialize, upsert_external_observations
from telegram_price_collector.external_collectors import (
    _fetch_ime_financial_snapshot,
    _fetch_ime_long_poll_items,
    _fetch_ime_sse_items,
    _ime_http_session,
    fetch_wallex_history,
    parse_ime_items,
)
from telegram_price_collector.normalization import (
    ime_coin_irr_per_coin_to_irt_per_coin,
    ime_gold_bar_irr_per_certificate_to_irt_per_mesghal_750,
    imam_intrinsic_coefficient,
)


class NormalizationTests(unittest.TestCase):
    def test_gold_bar_to_common_750_mesghal(self) -> None:
        raw = Decimal("25000000")
        expected = raw * Decimal("750") / Decimal("995") * Decimal("4.3318")
        self.assertEqual(
            ime_gold_bar_irr_per_certificate_to_irt_per_mesghal_750(raw),
            expected,
        )

    def test_coin_irr_to_irt(self) -> None:
        self.assertEqual(
            ime_coin_irr_per_coin_to_irt_per_coin(Decimal("1825000000")),
            Decimal("182500000"),
        )

    def test_imam_coefficient_matches_domain_formula(self) -> None:
        self.assertEqual(imam_intrinsic_coefficient(), Decimal("2.253"))


class ImeParserTests(unittest.TestCase):
    def test_ime_session_bootstrap_helper_is_callable(self) -> None:
        self.assertTrue(callable(_ime_http_session))

    @patch("telegram_price_collector.external_collectors._ime_http_session")
    @patch("telegram_price_collector.external_collectors._http_json")
    def test_financial_snapshot_uses_official_gavahi_endpoint(
        self, http_json, session
    ) -> None:
        opener = object()
        session.return_value = (opener, object())
        http_json.return_value = [
            {"ContractCode": "GoldBar", "LastTradedPrice": 24000000},
            {"ContractCode": "GoldCoin", "LastTradedPrice": 1800000000},
        ]

        rows = _fetch_ime_financial_snapshot(timeout=12)

        self.assertEqual(len(rows), 2)
        call = http_json.call_args
        self.assertTrue(call.args[0].endswith("/getFinancialMarketData"))
        self.assertEqual(call.kwargs["params"], {"param": "gavahi"})
        self.assertIs(call.kwargs["opener"], opener)

    @patch("telegram_price_collector.external_collectors._http_json")
    def test_long_poll_matches_official_signalr_post_contract(
        self, http_json
    ) -> None:
        http_json.side_effect = [
            {"C": "cursor-1"},
            {"Response": "started"},
            {"M": [{"ContractCode": "GoldBar"}]},
        ]

        rows = _fetch_ime_long_poll_items(
            opener=object(),
            negotiate={"ConnectionToken": "opaque-token"},
            connection_data='[{"name":"marketshub"}]',
            timeout=12,
        )

        self.assertTrue(rows)
        connect = http_json.call_args_list[0]
        self.assertEqual(connect.kwargs["form"], {})
        self.assertNotIn("tid", connect.kwargs["params"])
        start = http_json.call_args_list[1]
        self.assertNotIn("form", start.kwargs)
        poll = http_json.call_args_list[2]
        self.assertEqual(poll.kwargs["form"], {"messageId": "cursor-1"})
        self.assertNotIn("messageId", poll.kwargs["params"])

    @patch("telegram_price_collector.external_collectors._http_json")
    def test_sse_waits_for_init_then_starts_connection(self, http_json) -> None:
        class Response:
            def __init__(self) -> None:
                self.lines = iter(
                    (
                        b"data: initialized\n",
                        b'data: {\"S\":1,\"M\":[]}\n',
                        b'data: {\"M\":[{\"ContractCode\":\"GoldBar\"}]}\n',
                    )
                )

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def readline(self):
                return next(self.lines, b"")

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        http_json.return_value = {"Response": "started"}
        rows = _fetch_ime_sse_items(
            opener=Opener(),
            negotiate={"ConnectionToken": "opaque-token"},
            connection_data='[{"name":"marketshub"}]',
            timeout=12,
        )

        self.assertTrue(rows)
        start = http_json.call_args
        self.assertTrue(start.args[0].endswith("/realTimeServer/start"))
        self.assertEqual(start.kwargs["params"]["transport"], "serverSentEvents")
        self.assertNotIn("tid", start.kwargs["params"])

    def test_parses_both_contracts_and_derives_bubble(self) -> None:
        observed = datetime(2026, 7, 21, 8, 30, tzinfo=timezone.utc)
        rows = parse_ime_items(
            [
                {"ContractCode": "CD1GOB0001", "LastTradedPrice": "25,000,000"},
                {"ContractCode": "CD1GOC0001", "LastTradedPrice": "1,825,000,000"},
            ],
            observed_at=observed,
        )

        self.assertEqual(len(rows), 3)
        by_instrument = {row.instrument: row for row in rows}
        self.assertEqual(
            by_instrument["IME_GOLD_BAR"].normalized_unit,
            "IRT_PER_MESGHAL_750",
        )
        self.assertEqual(
            by_instrument["IME_GOLD_COIN_IMAM"].normalized_price,
            Decimal("182500000"),
        )
        self.assertEqual(
            by_instrument["IME_GOLD_COIN_IMAM_BUBBLE"].normalized_unit,
            "IRT_BUBBLE_PER_COIN",
        )

    def test_parses_live_cdc_aliases_all_quote_kinds_and_tehran_time(self) -> None:
        rows = parse_ime_items(
            [
                {
                    "ContractCode": "GoldBar",
                    "LastSettlementPrice": 24235830,
                    "FirstTradedPrice": 24199870,
                    "HighTradedPrice": 24350000,
                    "LowTradedPrice": 23830080,
                    "LastTradedPrice": 24308950,
                    "BidPrice1": 24308910,
                    "AskPrice1": 24308950,
                    "LastUpdate": "2026-07-21T17:00:08.17",
                },
                {
                    "ContractCode": "GoldCoin",
                    "LastSettlementPrice": 1835445666,
                    "FirstTradedPrice": 1824999900,
                    "HighTradedPrice": 1845000000,
                    "LowTradedPrice": 1820800000,
                    "LastTradedPrice": 1845000000,
                    "BidPrice1": 1842000000,
                    "AskPrice1": 1846999600,
                    "LastUpdate": "2026-07-21T17:00:08.17",
                },
            ]
        )

        self.assertEqual(len(rows), 21)
        for instrument in (
            "IME_GOLD_BAR",
            "IME_GOLD_COIN_IMAM",
            "IME_GOLD_COIN_IMAM_BUBBLE",
        ):
            selected = [row for row in rows if row.instrument == instrument]
            self.assertEqual(
                {row.quote_kind for row in selected},
                {"OPEN", "HIGH", "LOW", "CLOSE", "LAST", "BID", "ASK"},
            )
            self.assertEqual(
                {row.observed_at_utc for row in selected},
                {"2026-07-21T13:30:08Z"},
            )
        coin_last = next(
            row
            for row in rows
            if row.instrument == "IME_GOLD_COIN_IMAM" and row.quote_kind == "LAST"
        )
        self.assertEqual(coin_last.normalized_price, Decimal("184500000"))
        bubble_high = next(
            row
            for row in rows
            if row.instrument == "IME_GOLD_COIN_IMAM_BUBBLE"
            and row.quote_kind == "HIGH"
        )
        bubble_low = next(
            row
            for row in rows
            if row.instrument == "IME_GOLD_COIN_IMAM_BUBBLE"
            and row.quote_kind == "LOW"
        )
        self.assertGreater(bubble_high.normalized_price, bubble_low.normalized_price)

    def test_compact_storage_keeps_definitions_once(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        initialize(connection)
        rows = parse_ime_items(
            [{"ContractCode": "CD1GOB0001", "LastTradedPrice": "25,000,000"}],
            observed_at=datetime(2026, 7, 21, 8, 30, tzinfo=timezone.utc),
        )
        upsert_external_observations(connection, rows)
        upsert_external_observations(connection, rows)

        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM external_instruments").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM external_market_observations"
            ).fetchone()[0],
            1,
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(external_market_observations)")
        }
        self.assertNotIn("raw_payload_json", columns)
        self.assertNotIn("metadata_json", columns)
        connection.close()


class WallexHistoryTests(unittest.TestCase):
    @patch("telegram_price_collector.external_collectors._http_json")
    def test_zero_volume_carried_candle_is_not_observed(self, http_json) -> None:
        http_json.return_value = {
            "s": "ok",
            "t": [1784592000, 1784592060],
            "o": [188000, 188100],
            "h": [188000, 188100],
            "l": [188000, 188100],
            "c": [188000, 188100],
            "v": [0, 12.5],
        }
        rows = fetch_wallex_history(
            start=datetime.fromtimestamp(1784592000, tz=timezone.utc),
            end=datetime.fromtimestamp(1784592120, tz=timezone.utc),
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].raw_price, Decimal("188100"))
        self.assertEqual(rows[0].volume, Decimal("12.5"))


if __name__ == "__main__":
    unittest.main()
