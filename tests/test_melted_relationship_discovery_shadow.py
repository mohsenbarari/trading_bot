from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import runpy
import tempfile
import unittest

from core.market_intelligence.market_contracts import (
    MarketObservation,
    derive_event_key,
)
from core.market_intelligence.market_store import (
    archive_observations_older_than,
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "discover_melted_market_relationships_shadow.py"
)
MODULE = runpy.run_path(str(SCRIPT))
TargetPoint = MODULE["TargetPoint"]
SupportQuoteEvent = MODULE["SupportQuoteEvent"]
_build_support_quote_features = MODULE["_build_support_quote_features"]
_target_samples = MODULE["_target_samples"]
load_canonical_market_data = MODULE["load_canonical_market_data"]
load_canonical_confirmed_coin_trade_targets = MODULE[
    "load_canonical_confirmed_coin_trade_targets"
]


def observation(
    key: str,
    *,
    instrument: str,
    market_label: str,
    event_type: str,
    trade_form: str,
    price: int,
    price_unit: str,
    event_time: datetime,
    available_at: datetime,
    settlement: str = "TOMORROW",
) -> MarketObservation:
    return MarketObservation(
        event_key=derive_event_key("relationship-test", key),
        source_code="TEST_SOURCE",
        source_family="MANUAL_REVIEW",
        event_time_utc=event_time,
        available_at_utc=available_at,
        instrument=instrument,
        market_label=market_label,
        settlement_term=settlement,
        trade_form=trade_form,
        event_type=event_type,
        side="BUY",
        price=Decimal(price),
        price_unit=price_unit,
        currency="TOMAN",
        quantity=Decimal(1),
        quantity_unit="COIN_COUNT" if instrument.startswith("COIN_") else "MESGHAL",
        parse_confidence=1.0,
        parser_version="relationship-test-v1",
        quality_state="ELIGIBLE",
        quality_policy_version="relationship-test-v1",
        is_conditional=False,
        attributes={},
    )


class MeltedRelationshipDiscoveryTests(unittest.TestCase):
    def test_canonical_loader_uses_archive_and_availability_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "market-store.sqlite3"
            connection = connect_market_store(store_path)
            initialize_market_store(connection)
            event_time = NOW - timedelta(days=9)
            for item in (
                observation(
                    "private",
                    instrument="MELTED_GOLD_PRIVATE",
                    market_label="PRIVATE_GOLD_PAPER_REVERSE",
                    event_type="TRADE",
                    trade_form="PAPER_REVERSE",
                    price=80_000_000,
                    price_unit="TOMAN_PER_MESGHAL_750",
                    event_time=event_time,
                    available_at=NOW,
                ),
                observation(
                    "public-flow",
                    instrument="MELTED_GOLD_FLOW",
                    market_label="MELTED_PAPER_FLOW",
                    event_type="OFFER",
                    trade_form="PAPER_NORMAL",
                    price=80_100_000,
                    price_unit="TOMAN_PER_MESGHAL_750",
                    event_time=event_time,
                    available_at=NOW,
                ),
                observation(
                    "coin-offer",
                    instrument="COIN_IMAM",
                    market_label="GROUP_COIN_IMAM",
                    event_type="OFFER",
                    trade_form="PHYSICAL",
                    price=180_000,
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    event_time=event_time,
                    available_at=NOW,
                    settlement="CASH",
                ),
                observation(
                    "coin-trade",
                    instrument="COIN_IMAM",
                    market_label="GROUP_COIN_IMAM",
                    event_type="TRADE",
                    trade_form="PHYSICAL",
                    price=181_000,
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    event_time=event_time,
                    available_at=NOW,
                    settlement="CASH",
                ),
            ):
                upsert_observation(connection, item)
            connection.commit()
            archive_observations_older_than(connection)
            connection.close()

            melted, _, _, _ = load_canonical_market_data(
                store_path,
                include_coin_reference=False,
                include_conditional=False,
                since_utc=None,
                until_utc=None,
            )
            targets, trades, metadata = (
                load_canonical_confirmed_coin_trade_targets(
                    store_path,
                    since_utc=None,
                    until_utc=None,
                )
            )

        self.assertIn("PAPER:TOMORROW:REVERSE", melted)
        self.assertIn("PUBLIC_FLOW:PAPER:TOMORROW:NORMAL", melted)
        self.assertEqual(
            melted["PAPER:TOMORROW:REVERSE"][0].observed_at_utc,
            NOW,
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].commodity, "IMAM")
        self.assertEqual(targets["COIN_TRADE:IMAM:CASH:PHYSICAL"][0].price, 181_000)
        self.assertEqual(metadata["target_rows"], 1)

    def test_target_realization_is_strictly_later_than_cutoff(self):
        points = [
            TargetPoint("PAPER:TOMORROW:NORMAL", NOW, 100.0),
            TargetPoint(
                "PAPER:TOMORROW:NORMAL", NOW + timedelta(minutes=5), 101.0
            ),
        ]
        samples = list(
            _target_samples(
                points,
                horizon=timedelta(minutes=5),
                max_target_age=timedelta(minutes=1),
                step=timedelta(minutes=1),
            )
        )
        self.assertEqual(len(samples), 1)
        available_at, _, realized_at, target_return = samples[0]
        self.assertGreater(realized_at, available_at)
        self.assertAlmostEqual(target_return, 100.0)

    def test_support_quote_features_never_include_later_quote(self):
        events = [
            SupportQuoteEvent(NOW - timedelta(minutes=2), "USD_HERAT:PAPER:TODAY", 100.0),
            SupportQuoteEvent(NOW - timedelta(minutes=1), "USD_HERAT:PAPER:TODAY", 101.0),
            SupportQuoteEvent(NOW + timedelta(minutes=1), "USD_HERAT:PAPER:TODAY", 120.0),
        ]
        features = _build_support_quote_features(
            events,
            [event.observed_at_utc for event in events],
            as_of_utc=NOW,
            window=timedelta(minutes=5),
        )
        self.assertEqual(features["quote_count"], 2)
        self.assertAlmostEqual(features["quote_return_bps"], 100.0)
        self.assertEqual(features["quote_staleness_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
