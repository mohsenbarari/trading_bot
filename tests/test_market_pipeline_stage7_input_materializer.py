from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.market_intelligence.external_quote_capture import (
    BINANCE_BOOK_TICKER_URL,
    WALLEX_DEPTH_URL,
    DurableExternalQuoteSpool,
    ExternalQuoteCaptureError,
    Quote,
    decode_quote_event,
    fetch_paxg_quote,
    fetch_wallex_quotes,
    quote_event,
)
from core.market_intelligence.market_input_materializer import (
    build_input_components,
    materialize_input_snapshot,
    record_inference_use,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)

ESTIMATOR_ROOT = Path(__file__).resolve().parents[1] / "apps" / "coin_rate_estimator"
if str(ESTIMATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(ESTIMATOR_ROOT))
from coin_estimator import select_live_xauusd_average, select_usdt_average  # noqa: E402


UTC = timezone.utc


def quote(
    *,
    source: str,
    instrument: str,
    kind: str,
    price: str,
    at: datetime,
) -> Quote:
    return Quote(
        source_code=source,
        instrument=instrument,
        quote_kind=kind,
        price_value=price,
        price_unit=(
            "TOMAN_PER_USDT" if instrument == "USDT_IRT" else "USD_PER_TROY_OUNCE"
        ),
        currency="TOMAN" if instrument == "USDT_IRT" else "USD",
        observed_at_utc=at.isoformat().replace("+00:00", "Z"),
        available_at_utc=(at + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        provenance={"method": "TEST"},
    )


class MarketPipelineStage7InputMaterializerTests(unittest.TestCase):
    def _store(self, root: Path) -> sqlite3.Connection:
        connection = connect_market_store(root / "market.sqlite3")
        initialize_market_store(connection)
        return connection

    def _insert(self, connection: sqlite3.Connection, item: Quote) -> None:
        _event_id, observation = decode_quote_event(quote_event(item))
        upsert_observation(connection, observation)

    def test_wallex_and_paxg_network_payloads_are_minimized_and_checked(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)

        def response(url: str, **_kwargs: object) -> object:
            if url == WALLEX_DEPTH_URL:
                return {
                    "result": {
                        "ask": [{"price": "100.4", "private": "discard"}],
                        "bid": [{"price": "100.0", "private": "discard"}],
                    }
                }
            if url == BINANCE_BOOK_TICKER_URL:
                symbol = str(_kwargs["params"]["symbol"])  # type: ignore[index]
                return {
                    "symbol": symbol,
                    "bidPrice": "4630",
                    "askPrice": "4632",
                    "private": "discard",
                }
            raise AssertionError(url)

        with patch(
            "core.market_intelligence.external_quote_capture._http_json",
            side_effect=response,
        ):
            wallex = fetch_wallex_quotes(observed_at=at)
            paxg = fetch_paxg_quote(observed_at=at)
        self.assertEqual(
            [(item.quote_kind, item.price_value) for item in wallex],
            [("BID", "100"), ("ASK", "100.4"), ("MID", "100.2")],
        )
        self.assertEqual(paxg[0].price_value, "4631")
        encoded = json.dumps(quote_event(wallex[-1]), sort_keys=True)
        self.assertNotIn("private", encoded)
        self.assertNotIn("http", encoded.lower())

    def test_durable_outbox_restart_and_duplicate_are_lossless(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.sqlite3"
            spool = root / "spool"
            item = quote_event(
                quote(
                    source="WALLEX_PUBLIC_API",
                    instrument="USDT_IRT",
                    kind="MID",
                    price="100.2",
                    at=at,
                )
            )
            first = DurableExternalQuoteSpool(state, spool)
            try:
                self.assertEqual(first.stage(item), "staged")
            finally:
                first.close()
            restarted = DurableExternalQuoteSpool(state, spool)
            try:
                self.assertEqual(restarted.drain(), 1)
                self.assertEqual(restarted.stage(item), "duplicate")
                self.assertEqual(restarted.drain(), 0)
                self.assertEqual(
                    restarted.connection.execute(
                        "SELECT COUNT(*) FROM external_capture_outbox"
                    ).fetchone()[0],
                    0,
                )
            finally:
                restarted.close()
            lines = next(spool.glob("events-*.jsonl")).read_text().splitlines()
            self.assertEqual(len(lines), 1)

    def test_exact_point_mean_and_quiet_cycles_reuse_snapshot(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            connection = self._store(Path(directory))
            try:
                for seconds, value in ((-80, "185100.1"), (-20, "185100.2")):
                    self._insert(
                        connection,
                        quote(
                            source="WALLEX_PUBLIC_API",
                            instrument="USDT_IRT",
                            kind="MID",
                            price=value,
                            at=at + timedelta(seconds=seconds),
                        ),
                    )
                for seconds, value in ((-70, "4630.1"), (-10, "4631.2")):
                    self._insert(
                        connection,
                        quote(
                            source="BINANCE_PAXG_PUBLIC_API",
                            instrument="PAXG_USD_PROXY",
                            kind="MID",
                            price=value,
                            at=at + timedelta(seconds=seconds),
                        ),
                    )
                connection.commit()
                first = materialize_input_snapshot(connection, as_of_utc=at)
                connection.commit()
                second = materialize_input_snapshot(
                    connection, as_of_utc=at + timedelta(seconds=5)
                )
                components = {item.feature_role: item for item in first.components}
                self.assertEqual(components["USDT_IRT_90S_POINT"].consumed_value, "185100.2")
                self.assertEqual(components["USDT_IRT_90S_MEAN"].consumed_value, "185100.15")
                self.assertEqual(components["XAUUSD_90S_POINT"].consumed_value, "4631.2")
                self.assertTrue(components["XAUUSD_90S_POINT"].provenance["is_proxy"])
                self.assertTrue(first.inserted)
                self.assertFalse(second.inserted)
                self.assertEqual(first.input_snapshot_hash, second.input_snapshot_hash)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM input_snapshots").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
                    4,
                )
            finally:
                connection.close()

    def test_same_timestamp_values_are_equal_to_current_model_selection(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            connection = self._store(Path(directory))
            legacy = sqlite3.connect(":memory:")
            legacy.row_factory = sqlite3.Row
            legacy.executescript(
                """
                CREATE TABLE external_market_observations(
                  id INTEGER PRIMARY KEY,
                  instrument_code TEXT,
                  quote_kind TEXT,
                  observed_at_utc TEXT,
                  normalized_price_num REAL,
                  interval_seconds INTEGER,
                  volume_value TEXT
                );
                CREATE TABLE price_events(
                  id INTEGER PRIMARY KEY,
                  instrument TEXT,
                  market_label TEXT,
                  settlement_term TEXT,
                  trade_form TEXT,
                  event_type TEXT,
                  side TEXT,
                  event_time_utc TEXT,
                  price_num REAL
                );
                """
            )
            try:
                for identifier, seconds, value in (
                    (1, -80, "185100"),
                    (2, -20, "185200"),
                ):
                    moment = at + timedelta(seconds=seconds)
                    self._insert(
                        connection,
                        quote(
                            source="WALLEX_PUBLIC_API",
                            instrument="USDT_IRT",
                            kind="MID",
                            price=value,
                            at=moment,
                        ),
                    )
                    legacy.execute(
                        "INSERT INTO external_market_observations VALUES(?,?,?,?,?,?,?)",
                        (
                            identifier,
                            "USDT_IRT",
                            "MID",
                            moment.isoformat().replace("+00:00", "Z"),
                            float(value),
                            0,
                            None,
                        ),
                    )
                from core.market_intelligence.market_contracts import (
                    MarketObservation,
                    derive_event_key,
                )

                for identifier, seconds, value in (
                    (1, -70, "4630.1"),
                    (2, -10, "4631.2"),
                ):
                    moment = at + timedelta(seconds=seconds)
                    upsert_observation(
                        connection,
                        MarketObservation(
                            event_key=derive_event_key("direct-xau", identifier),
                            source_code="XAUUSD",
                            source_family="TELEGRAM_PUBLIC",
                            event_time_utc=moment,
                            available_at_utc=moment + timedelta(seconds=1),
                            instrument="XAUUSD",
                            market_label="XAUUSD_SPOT",
                            settlement_term="SPOT",
                            trade_form="NOT_APPLICABLE",
                            event_type="QUOTE",
                            side="MID",
                            price=value,
                            price_unit="USD_PER_TROY_OUNCE",
                            currency="USD",
                        ),
                    )
                    legacy.execute(
                        "INSERT INTO price_events VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            "XAUUSD",
                            "اونس جهانی",
                            "SPOT",
                            "NOT_APPLICABLE",
                            "QUOTE",
                            "MID",
                            moment.isoformat().replace("+00:00", "Z"),
                            float(value),
                        ),
                    )
                connection.commit()
                legacy.commit()
                components = {
                    item.feature_role: item
                    for item in build_input_components(connection, as_of_utc=at)
                }
                old_usdt = select_usdt_average(legacy, at, seconds=90)
                old_xau = select_live_xauusd_average(legacy, at, seconds=90)
                self.assertEqual(
                    Decimal(components["USDT_IRT_90S_POINT"].consumed_value or "0"),
                    Decimal(str(old_usdt["point_price"])),
                )
                self.assertEqual(
                    Decimal(components["USDT_IRT_90S_MEAN"].consumed_value or "0"),
                    Decimal(str(old_usdt["average_price"])),
                )
                self.assertEqual(
                    Decimal(components["XAUUSD_90S_POINT"].consumed_value or "0"),
                    Decimal(str(old_xau["point_price"])),
                )
                self.assertEqual(
                    Decimal(components["XAUUSD_90S_MEAN"].consumed_value or "0"),
                    Decimal(str(old_xau["average_price"])),
                )
            finally:
                legacy.close()
                connection.close()

    def test_direct_xau_wins_and_bad_proxy_fails_closed(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            connection = self._store(Path(directory))
            try:
                # Recent direct evidence is outside the 2% proxy band, but too
                # old for the direct 90-second point.
                direct = Quote(
                    source_code="BINANCE_PAXG_PUBLIC_API",
                    instrument="PAXG_USD_PROXY",
                    quote_kind="MID",
                    price_value="5000",
                    price_unit="USD_PER_TROY_OUNCE",
                    currency="USD",
                    observed_at_utc=(at - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                    available_at_utc=(at - timedelta(seconds=9)).isoformat().replace("+00:00", "Z"),
                    provenance={"method": "TEST"},
                )
                self._insert(connection, direct)
                # Telegram direct XAU uses its native parser/observation path.
                from core.market_intelligence.market_contracts import MarketObservation, derive_event_key

                upsert_observation(
                    connection,
                    MarketObservation(
                        event_key=derive_event_key("direct-xau"),
                        source_code="XAUUSD",
                        source_family="TELEGRAM_PUBLIC",
                        event_time_utc=at - timedelta(seconds=120),
                        available_at_utc=at - timedelta(seconds=119),
                        instrument="XAUUSD",
                        market_label="XAUUSD_SPOT",
                        settlement_term="SPOT",
                        trade_form="NOT_APPLICABLE",
                        event_type="QUOTE",
                        side="MID",
                        price="4630",
                        price_unit="USD_PER_TROY_OUNCE",
                        currency="USD",
                    ),
                )
                connection.commit()
                components = {
                    item.feature_role: item
                    for item in build_input_components(connection, as_of_utc=at)
                }
                self.assertIsNone(components["XAUUSD_90S_POINT"].consumed_value)
                self.assertEqual(
                    components["XAUUSD_90S_POINT"].provenance["fallback"],
                    "PAXG_PROXY_OUTSIDE_RECENT_XAU_BAND",
                )
            finally:
                connection.close()

    def test_optional_roles_are_materialized_only_when_invoked(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            connection = self._store(Path(directory))
            try:
                for offset, value in (
                    (-300, "184900"),
                    (-120, "185000"),
                    (-10, "185100"),
                ):
                    self._insert(
                        connection,
                        quote(
                            source="WALLEX_PUBLIC_API",
                            instrument="USDT_IRT",
                            kind="MID",
                            price=value,
                            at=at + timedelta(seconds=offset),
                        ),
                    )
                connection.commit()
                base = build_input_components(connection, as_of_utc=at)
                invoked = build_input_components(
                    connection,
                    as_of_utc=at,
                    include_usdt_trend=True,
                    include_regime=True,
                )
                self.assertEqual(len(base), 4)
                self.assertEqual(len(invoked), 8)
                roles = {item.feature_role for item in invoked}
                self.assertIn("USDT_IRT_TREND_PREVIOUS_180S", roles)
                self.assertIn("USDT_IRT_REGIME_600S_MEAN", roles)
                snapshot = materialize_input_snapshot(connection, as_of_utc=at)
                connection.commit()
                inference_id = bytes.fromhex("11" * 32)
                self.assertTrue(
                    record_inference_use(
                        connection,
                        inference_id=inference_id,
                        input_snapshot_hash=snapshot.input_snapshot_hash,
                        model_version="shadow-v1",
                        settlement="CASH",
                        inferred_at_utc=at,
                    )
                )
                self.assertFalse(
                    record_inference_use(
                        connection,
                        inference_id=inference_id,
                        input_snapshot_hash=snapshot.input_snapshot_hash,
                        model_version="shadow-v1",
                        settlement="CASH",
                        inferred_at_utc=at,
                    )
                )
            finally:
                connection.close()

    def test_external_contract_rejects_dimension_confusion(self) -> None:
        at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        document = quote_event(
            quote(
                source="WALLEX_PUBLIC_API",
                instrument="USDT_IRT",
                kind="MID",
                price="100",
                at=at,
            )
        )
        document["quote"]["price_unit"] = "USD_PER_TROY_OUNCE"
        with self.assertRaisesRegex(
            ExternalQuoteCaptureError, "external_quote_dimensions_invalid"
        ):
            decode_quote_event(document)


if __name__ == "__main__":
    unittest.main()
