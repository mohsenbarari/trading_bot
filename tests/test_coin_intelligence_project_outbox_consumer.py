"""Delivery tests for the explicit product-outbox consumer.

The tests use only synthetic economic values and a temporary SQLite Market
Store.  They do not start a worker, contact a network service, or access a
runtime database path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.market_intelligence.market_store import connect_market_store
from core.market_intelligence.project_outbox_consumer import (
    ProjectMarketOutboxConsumer,
    claim_next_project_market_outbox,
    complete_project_market_outbox,
    write_project_outbox_observation,
)
from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox


class ProjectMarketOutboxConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        CoinIntelligenceMarketOutbox.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.tempdir = tempfile.TemporaryDirectory()
        self.market_store = Path(self.tempdir.name) / "market.sqlite3"
        self.now = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def _payload(self, **changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": 1,
            "instrument": "PROJECT_COMMODITY",
            "commodity_id": 17,
            "side": "SELL",
            "settlement_term": "CASH",
            "trade_form": "PHYSICAL",
            "event_type": "OFFER",
            "price": 186_900,
            "price_unit": "PROJECT_THOUSAND_TOMAN",
            "currency": "IRT",
            "quantity": 5,
            "remaining_quantity": 5,
            "status": "ACTIVE",
        }
        payload.update(changes)
        return payload

    def _append(self, *, payload: dict[str, object] | None = None) -> CoinIntelligenceMarketOutbox:
        row = CoinIntelligenceMarketOutbox(
            idempotency_key="a" * 64,
            event_kind="OFFER_OPENED",
            subject_kind="OFFER",
            subject_id=1,
            occurred_at_utc=self.now,
            payload=payload or self._payload(),
            status="PENDING",
            attempts=0,
            available_at_utc=self.now,
            model_eligible=True,
        )
        self.session.add(row)
        self.session.commit()
        return row

    def _stored_row(self) -> CoinIntelligenceMarketOutbox:
        return self.session.scalar(select(CoinIntelligenceMarketOutbox))

    def test_consumer_completes_only_after_local_store_write(self) -> None:
        self._append()

        result = ProjectMarketOutboxConsumer(
            market_store_path=self.market_store
        ).consume_one(self.session, now=self.now)

        self.assertEqual((result.status, result.outbox_id), ("COMPLETE", 1))
        self.assertEqual(self._stored_row().status, "COMPLETE")
        connection = connect_market_store(self.market_store)
        try:
            row = connection.execute(
                """
                SELECT source_family, instrument, settlement_term, trade_form,
                       event_type, side, price_num, quantity_num, attributes_json
                FROM market_observations
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            tuple(row[name] for name in row.keys()[:-1]),
            (
                "PROJECT",
                "PROJECT_COMMODITY",
                "CASH",
                "PHYSICAL",
                "OFFER",
                "SELL",
                186_900.0,
                5.0,
            ),
        )
        self.assertIn('"product_commodity_id":17', row["attributes_json"])
        self.assertNotIn("subject_id", row["attributes_json"])

    def test_same_claim_replay_is_one_market_fact(self) -> None:
        self._append()
        claim = claim_next_project_market_outbox(self.session, now=self.now)
        self.assertIsNotNone(claim)
        self.session.commit()
        assert claim is not None

        write_project_outbox_observation(
            claim,
            market_store_path=self.market_store,
            available_at_utc=self.now,
        )
        write_project_outbox_observation(
            claim,
            market_store_path=self.market_store,
            available_at_utc=self.now,
        )
        self.assertTrue(complete_project_market_outbox(self.session, claim=claim))
        self.session.commit()

        connection = connect_market_store(self.market_store)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM market_observations"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_invalid_payload_fails_closed_without_a_market_fact(self) -> None:
        self._append(payload=self._payload(price_unit="IRT_PER_COIN"))

        result = ProjectMarketOutboxConsumer(
            market_store_path=self.market_store
        ).consume_one(self.session, now=self.now)

        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.error_code, "project_outbox_payload_price_unit_invalid")
        self.assertEqual(self._stored_row().status, "FAILED")
        self.assertFalse(self.market_store.exists())

    def test_store_failure_is_retried_with_a_bounded_delay(self) -> None:
        self._append()
        result = ProjectMarketOutboxConsumer(
            market_store_path=Path(self.tempdir.name),
        ).consume_one(self.session, now=self.now)

        self.assertEqual(result.status, "RETRY_PENDING")
        self.assertEqual(result.error_code, "market_store_write_failed")
        self.assertEqual(self._stored_row().status, "PENDING")
        self.assertEqual(self._stored_row().attempts, 1)
        self.assertGreater(result.retry_at_utc, self.now)


if __name__ == "__main__":
    unittest.main()
