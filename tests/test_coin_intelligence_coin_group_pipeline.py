"""Integration tests for explicit staging → validation → trade → Market Store flow."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_group_pipeline import process_coin_group_staging
from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingMessage,
    connect_coin_group_staging,
    initialize_coin_group_staging,
    stage_coin_group_message,
)
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class CoinGroupPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.staging = connect_coin_group_staging(root / "private-staging.sqlite3")
        self.market = connect_market_store(root / "market.sqlite3")
        initialize_coin_group_staging(self.staging)
        initialize_market_store(self.market)

    def tearDown(self) -> None:
        self.staging.close()
        self.market.close()
        self.tempdir.cleanup()

    def _stage(self, message_id: int, text: str, *, sender: str, reply: int | None = None, at: str = "2026-08-04T10:00:00Z") -> None:
        stage_coin_group_message(
            self.staging,
            CoinGroupStagingMessage(
                group_number=1,
                message_id=message_id,
                event_time_utc=at,
                available_at_utc=at,
                text=text,
                reply_to_message_id=reply,
                sender_identity=sender,
            ),
        )

    def _anchor(self, event_id: int, price: int, at: str) -> None:
        upsert_observation(
            self.market,
            MarketObservation(
                event_key=derive_event_key("pipeline-anchor", event_id),
                source_code="TEST_ANCHOR",
                source_family="MANUAL_REVIEW",
                event_time_utc=at,
                available_at_utc=at,
                instrument="COIN_IMAM",
                market_label="TEST_COIN_IMAM",
                settlement_term="TOMORROW",
                trade_form="PHYSICAL",
                event_type="TRADE",
                side="SELL",
                price=Decimal(price),
                price_unit="PROJECT_THOUSAND_TOMAN",
                quantity=1,
                quantity_unit="COIN_COUNT",
            ),
        )

    def test_pipeline_projects_validated_offer_and_negotiated_confirmed_trade_idempotently(self) -> None:
        self._anchor(1, 186_700, "2026-08-04T09:50:00Z")
        self._anchor(2, 186_800, "2026-08-04T09:55:00Z")
        self.market.commit()
        self._stage(1, "امام فروش فردا 186,900 / 5 تا", sender="offerer", at="2026-08-04T10:00:00Z")
        self._stage(2, "ب5 تا186800", sender="buyer", reply=1, at="2026-08-04T10:00:02Z")
        self._stage(3, "برکت", sender="offerer", reply=2, at="2026-08-04T10:00:04Z")
        self.staging.commit()

        report = process_coin_group_staging(
            self.staging, self.market, as_of_utc="2026-08-04T10:01:00Z"
        )
        self.market.commit()
        self.assertEqual((report.eligible_offers, report.eligible_trades), (1, 1))
        facts = self.market.execute(
            "SELECT event_type, price_num, quality_state, attributes_json FROM market_observations WHERE source_code = 'GROUP_1' ORDER BY event_type"
        ).fetchall()
        self.assertEqual([(row["event_type"], row["price_num"], row["quality_state"]) for row in facts], [("OFFER", 186_900.0, "ELIGIBLE"), ("TRADE", 186_800.0, "ELIGIBLE")])
        self.assertNotIn("offerer", facts[0]["attributes_json"])
        self.assertNotIn("message", facts[0]["attributes_json"])

        process_coin_group_staging(self.staging, self.market, as_of_utc="2026-08-04T10:01:30Z")
        self.market.commit()
        self.assertEqual(self.market.execute("SELECT COUNT(*) FROM market_observations WHERE source_code = 'GROUP_1'").fetchone()[0], 2)

    def test_without_prior_anchors_offer_and_trade_stay_out_of_model(self) -> None:
        self._stage(1, "امام فروش فردا 186,900 / 5 تا", sender="offerer")
        self._stage(2, "ب5 تا186800", sender="buyer", reply=1, at="2026-08-04T10:00:02Z")
        self._stage(3, "برکت", sender="offerer", reply=2, at="2026-08-04T10:00:04Z")
        self.staging.commit()

        report = process_coin_group_staging(
            self.staging, self.market, as_of_utc="2026-08-04T10:01:00Z"
        )
        self.market.commit()
        self.assertEqual((report.eligible_offers, report.trade_facts_upserted), (0, 0))
        row = self.market.execute("SELECT quality_state FROM market_observations WHERE source_code = 'GROUP_1'").fetchone()
        self.assertEqual(row["quality_state"], "PENDING_REVIEW")

    def test_non_integral_project_price_cannot_be_coerced_into_an_anchor(self) -> None:
        self._anchor(1, 186_700, "2026-08-04T09:50:00Z")
        self.market.execute(
            "UPDATE market_observations SET price_num = 186800.5 WHERE source_code = 'TEST_ANCHOR'"
        )
        self.market.commit()
        self._stage(1, "امام فروش فردا 186,900 / 5 تا", sender="offerer")
        self.staging.commit()
        report = process_coin_group_staging(
            self.staging, self.market, as_of_utc="2026-08-04T10:01:00Z"
        )
        self.assertEqual(report.eligible_offers, 0)


if __name__ == "__main__":
    unittest.main()
