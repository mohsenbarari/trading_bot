"""Contract tests for causal reconciliation of private melted-gold events."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.private_gold_staging import (
    PrivateGoldStagingError,
    PrivateGoldStagingOffer,
    PrivateGoldStagingTradeUpdate,
    assert_private_gold_staging_path_outside_repository,
    connect_private_gold_staging,
    initialize_private_gold_staging,
    list_current_private_gold_staging,
    promote_private_gold_staging,
    purge_expired_private_gold_staging,
    stage_private_gold_offer,
    stage_private_gold_trade_update,
)


class PrivateGoldStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.staging = connect_private_gold_staging(root / "private" / "gold-staging.sqlite3")
        self.market = connect_market_store(root / "market.sqlite3")
        initialize_private_gold_staging(self.staging)
        initialize_market_store(self.market)

    def tearDown(self) -> None:
        self.staging.close()
        self.market.close()
        self.tempdir.cleanup()

    @staticmethod
    def offer(**changes: object) -> PrivateGoldStagingOffer:
        values: dict[str, object] = {
            "source_message_id": "101",
            "event_time_utc": "2026-08-04T12:00:00Z",
            "available_at_utc": "2026-08-04T12:00:05Z",
            "text": "80,300,000 فروش 5 تا با حواله",
        }
        values.update(changes)
        return PrivateGoldStagingOffer(**values)  # type: ignore[arg-type]

    @staticmethod
    def trade(**changes: object) -> PrivateGoldStagingTradeUpdate:
        values: dict[str, object] = {
            "source_message_id": "101",
            "available_at_utc": "2026-08-04T12:02:00Z",
            "trade_status": "FULL",
            "traded_quantity": 5,
            "trade_detected_at_utc": "2026-08-04T12:01:00Z",
            "telegram_edit_datetime": "2026-08-04T12:01:10Z",
        }
        values.update(changes)
        return PrivateGoldStagingTradeUpdate(**values)  # type: ignore[arg-type]

    def _promote(self) -> object:
        report = promote_private_gold_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T12:03:00Z",
        )
        self.staging.commit()
        self.market.commit()
        return report

    def _facts(self) -> list[object]:
        return self.market.execute(
            """
            SELECT event_type, event_time_utc, available_at_utc, price_num,
                   quantity_num, settlement_term, trade_form
            FROM market_observations
            WHERE source_code = 'PRIVATE_GOLD_CHANNEL'
            ORDER BY event_type
            """
        ).fetchall()

    def test_offer_then_verifier_update_emits_two_causally_correct_facts(self) -> None:
        self.assertTrue(stage_private_gold_offer(self.staging, self.offer()))
        self.assertTrue(stage_private_gold_trade_update(self.staging, self.trade()))
        report = self._promote()

        self.assertEqual((report.offer_facts_upserted, report.trade_facts_upserted), (1, 1))
        facts = self._facts()
        self.assertEqual(len(facts), 2)
        offer, trade = facts
        self.assertEqual((offer["event_type"], offer["available_at_utc"]), ("OFFER", "2026-08-04T12:00:05Z"))
        self.assertEqual(
            (trade["event_type"], trade["event_time_utc"], trade["available_at_utc"]),
            ("TRADE", "2026-08-04T12:01:10Z", "2026-08-04T12:02:00Z"),
        )
        self.assertEqual((trade["price_num"], trade["quantity_num"]), (80_300_000.0, 5.0))
        self.assertEqual((trade["trade_form"], trade["settlement_term"]), ("PAPER_NORMAL", "TOMORROW"))

    def test_verifier_update_before_offer_waits_then_reconciles(self) -> None:
        self.assertTrue(stage_private_gold_trade_update(self.staging, self.trade()))
        first = self._promote()
        self.assertEqual((first.offer_facts_upserted, first.trade_facts_upserted), (0, 0))
        self.assertEqual(first.unparseable_or_incomplete_rows, 1)

        self.assertTrue(stage_private_gold_offer(self.staging, self.offer()))
        second = self._promote()
        self.assertEqual((second.offer_facts_upserted, second.trade_facts_upserted), (1, 1))
        self.assertEqual(len(self._facts()), 2)

    def test_partial_without_quantity_never_invents_a_trade(self) -> None:
        stage_private_gold_offer(self.staging, self.offer())
        stage_private_gold_trade_update(
            self.staging,
            self.trade(trade_status="PARTIAL", traded_quantity=None),
        )
        report = self._promote()

        self.assertEqual((report.offer_facts_upserted, report.trade_facts_upserted), (1, 0))
        self.assertEqual([row["event_type"] for row in self._facts()], ["OFFER"])

    def test_offer_edit_waits_for_explicit_verifier(self) -> None:
        stage_private_gold_offer(
            self.staging,
            self.offer(edited_at_utc="2026-08-04T12:01:10Z"),
        )
        report = self._promote()

        self.assertEqual((report.offer_facts_upserted, report.trade_facts_upserted), (1, 0))
        self.assertEqual([row["event_type"] for row in self._facts()], ["OFFER"])

    def test_staging_expiry_deletes_text_but_not_normalized_market_facts(self) -> None:
        stage_private_gold_offer(self.staging, self.offer())
        self._promote()
        self.assertEqual(
            purge_expired_private_gold_staging(
                self.staging,
                as_of_utc="2026-08-07T12:00:05Z",
            ),
            1,
        )
        self.staging.commit()
        self.assertEqual(
            self.staging.execute("SELECT COUNT(*) FROM private_gold_staged_offers").fetchone()[0],
            0,
        )
        self.assertEqual(len(self._facts()), 1)

    def test_idempotency_and_repository_raw_path_guard(self) -> None:
        self.assertTrue(stage_private_gold_offer(self.staging, self.offer()))
        self.assertFalse(stage_private_gold_offer(self.staging, self.offer()))
        self.assertTrue(stage_private_gold_trade_update(self.staging, self.trade()))
        self.assertFalse(stage_private_gold_trade_update(self.staging, self.trade()))
        with tempfile.TemporaryDirectory() as repository:
            with self.assertRaisesRegex(PrivateGoldStagingError, "inside_repository"):
                assert_private_gold_staging_path_outside_repository(
                    Path(repository) / "private-gold.sqlite3",
                    repository_root=Path(repository),
                )


if __name__ == "__main__":
    unittest.main()
