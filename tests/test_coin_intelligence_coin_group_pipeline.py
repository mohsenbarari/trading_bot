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
from core.market_intelligence.research_archive import research_contexts_for_rows


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
                sender_telegram_id=("7001" if sender == "offerer" else "7002"),
                sender_display_name=(
                    "Offerer User" if sender == "offerer" else "Buyer User"
                ),
            ),
        )

    def _anchor(
        self,
        event_id: int,
        price: int,
        at: str,
        *,
        commodity: str = "IMAM",
    ) -> None:
        upsert_observation(
            self.market,
            MarketObservation(
                event_key=derive_event_key("pipeline-anchor", event_id),
                source_code="TEST_ANCHOR",
                source_family="MANUAL_REVIEW",
                event_time_utc=at,
                available_at_utc=at,
                instrument="COIN_" + commodity,
                market_label="TEST_COIN_" + commodity,
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
        contexts = self.staging.execute(
            "SELECT event_key,root_message_id,requester_message_id "
            "FROM coin_group_fact_research_context"
        ).fetchall()
        event_types = {
            bytes(row["event_key"]): str(row["event_type"])
            for row in self.market.execute(
                "SELECT event_key,event_type FROM market_observations "
                "WHERE source_code='GROUP_1'"
            ).fetchall()
        }
        self.assertEqual(
            sorted(
                (
                    event_types[bytes(row["event_key"])],
                    int(row["root_message_id"]),
                    (
                        int(row["requester_message_id"])
                        if row["requester_message_id"] is not None
                        else None
                    ),
                )
                for row in contexts
            ),
            [("OFFER", 1, None), ("TRADE", 1, 2)],
        )
        research = research_contexts_for_rows(
            self.staging,
            self.market.execute(
                "SELECT event_key,source_code FROM market_observations "
                "WHERE source_code='GROUP_1'"
            ).fetchall(),
        )
        by_type = {
            event_types[key]: value for key, value in research.items()
        }
        self.assertEqual(by_type["OFFER"].raw_text, "امام فروش فردا 186,900 / 5 تا")
        self.assertEqual(by_type["OFFER"].actors["OFFERER"].telegram_id, "7001")
        self.assertEqual(by_type["TRADE"].actors["REQUESTER"].telegram_id, "7002")

        first_timestamps = self.market.execute(
            """
            SELECT event_type, available_at_utc, inserted_at_utc
            FROM market_observations
            WHERE source_code = 'GROUP_1'
            ORDER BY event_type
            """
        ).fetchall()
        self.assertEqual(
            [row["available_at_utc"] for row in first_timestamps],
            ["2026-08-04T10:01:00Z", "2026-08-04T10:01:00Z"],
        )

        process_coin_group_staging(self.staging, self.market, as_of_utc="2026-08-04T10:01:30Z")
        self.market.commit()
        self.assertEqual(self.market.execute("SELECT COUNT(*) FROM market_observations WHERE source_code = 'GROUP_1'").fetchone()[0], 2)
        replay_timestamps = self.market.execute(
            """
            SELECT event_type, available_at_utc, inserted_at_utc
            FROM market_observations
            WHERE source_code = 'GROUP_1'
            ORDER BY event_type
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in replay_timestamps],
            [tuple(row) for row in first_timestamps],
        )

        self.market.execute(
            "UPDATE market_observations SET parser_version='previous-parser-release' "
            "WHERE source_code='GROUP_1'"
        )
        self.market.commit()
        process_coin_group_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T10:02:00Z",
        )
        parser_rollout_timestamps = self.market.execute(
            """
            SELECT event_type,available_at_utc
            FROM market_observations
            WHERE source_code='GROUP_1'
            ORDER BY event_type
            """
        ).fetchall()
        self.assertEqual(
            [row["available_at_utc"] for row in parser_rollout_timestamps],
            ["2026-08-04T10:01:00Z", "2026-08-04T10:01:00Z"],
        )

    def test_complete_explicit_offer_and_trade_do_not_wait_for_prior_anchors(self) -> None:
        self._stage(1, "امام فروش فردا 186,900 / 5 تا", sender="offerer")
        self._stage(2, "ب5 تا186800", sender="buyer", reply=1, at="2026-08-04T10:00:02Z")
        self._stage(3, "برکت", sender="offerer", reply=2, at="2026-08-04T10:00:04Z")
        self.staging.commit()

        report = process_coin_group_staging(
            self.staging, self.market, as_of_utc="2026-08-04T10:01:00Z"
        )
        self.market.commit()
        self.assertEqual(
            (
                report.eligible_offers,
                report.trade_facts_upserted,
                report.eligible_trades,
                report.pending_or_rejected_trades,
            ),
            (1, 1, 1, 0),
        )
        rows = self.market.execute(
            "SELECT event_type,quality_state FROM market_observations WHERE source_code = 'GROUP_1' ORDER BY event_type"
        ).fetchall()
        self.assertEqual(
            [(row["event_type"], row["quality_state"]) for row in rows],
            [("OFFER", "ELIGIBLE"), ("TRADE", "ELIGIBLE")],
        )

    def test_explicit_cluster_is_eligible_from_its_first_complete_offer(self) -> None:
        for message_id, sender, price, second in (
            (1, "offerer-a", 188_700, 0),
            (2, "offerer-b", 188_800, 10),
            (3, "offerer-a", 188_900, 20),
            (4, "offerer-c", 188_850, 30),
        ):
            self._stage(
                message_id,
                f"امام فروش فردا {price} / 5 تا",
                sender=sender,
                at=f"2026-08-04T10:00:{second:02d}Z",
            )
        self.staging.commit()

        report = process_coin_group_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T10:01:00Z",
        )
        states = self.market.execute(
            "SELECT event_time_utc,quality_state FROM market_observations WHERE source_code='GROUP_1' ORDER BY event_time_utc"
        ).fetchall()
        self.assertEqual(report.eligible_offers, 4)
        self.assertEqual(
            [row["quality_state"] for row in states],
            ["ELIGIBLE", "ELIGIBLE", "ELIGIBLE", "ELIGIBLE"],
        )

    def test_conditional_explicit_cluster_can_disambiguate_later_plain_offer(self) -> None:
        self._anchor(
            1,
            186_700,
            "2026-08-04T09:50:00Z",
            commodity="BAHAR",
        )
        self._anchor(
            2,
            186_800,
            "2026-08-04T09:55:00Z",
            commodity="BAHAR",
        )
        self.market.commit()
        for message_id, sender, second in (
            (1, "offerer-a", 0),
            (2, "offerer-b", 10),
            (3, "offerer-c", 20),
        ):
            suffix = "حساب شب" if message_id != 3 else ""
            self._stage(
                message_id,
                f"امام فروش 189000 / 5 تا {suffix}",
                sender=sender,
                at=f"2026-08-04T10:00:{second:02d}Z",
            )
        self._stage(
            4,
            "فروش 188900 / 5 تا",
            sender="offerer-d",
            at="2026-08-04T10:00:30Z",
        )
        self._stage(
            5,
            "خرید 188950 / 5 تا",
            sender="offerer-e",
            at="2026-08-04T10:01:00Z",
        )
        self._stage(
            6,
            "فروش 189000 / 5 تا",
            sender="offerer-f",
            at="2026-08-04T10:40:00Z",
        )
        self.staging.commit()

        report = process_coin_group_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T10:41:00Z",
        )
        rows = self.market.execute(
            """
            SELECT instrument,settlement_term,quality_state
            FROM market_observations
            WHERE source_code='GROUP_1'
              AND event_time_utc IN ('2026-08-04T10:00:30Z','2026-08-04T10:01:00Z','2026-08-04T10:40:00Z')
            ORDER BY event_time_utc
            """
        ).fetchall()
        self.assertEqual(report.eligible_offers, 3)
        self.assertEqual(
            [
                (row["instrument"], row["settlement_term"], row["quality_state"])
                for row in rows
            ],
            [("COIN_IMAM", "TOMORROW", "ELIGIBLE")] * 3,
        )

    def test_edit_that_removes_offer_retracts_offer_and_linked_trade(self) -> None:
        self._anchor(1, 186_700, "2026-08-04T09:50:00Z")
        self._anchor(2, 186_800, "2026-08-04T09:55:00Z")
        self.market.commit()
        self._stage(1, "امام فروش فردا 186,900 / 5 تا", sender="offerer")
        self._stage(2, "ب5 تا186800", sender="buyer", reply=1, at="2026-08-04T10:00:02Z")
        self._stage(3, "برکت", sender="offerer", reply=2, at="2026-08-04T10:00:04Z")
        self.staging.commit()
        process_coin_group_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T10:01:00Z",
        )
        self.market.commit()

        self._stage(1, "پیام غیر آفر", sender="offerer")
        self.staging.commit()
        report = process_coin_group_staging(
            self.staging,
            self.market,
            as_of_utc="2026-08-04T10:02:00Z",
        )
        states = self.market.execute(
            "SELECT event_type,quality_state,attributes_json FROM market_observations WHERE source_code='GROUP_1' ORDER BY event_type"
        ).fetchall()
        self.assertEqual(report.retracted_facts, 2)
        self.assertEqual([row["quality_state"] for row in states], ["REJECTED", "REJECTED"])
        self.assertTrue(
            all("NO_LONGER_PRESENT" in row["attributes_json"] for row in states)
        )

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
        row = self.market.execute(
            "SELECT quality_state,attributes_json FROM market_observations "
            "WHERE source_code='GROUP_1' AND event_type='OFFER'"
        ).fetchone()
        self.assertEqual(report.eligible_offers, 1)
        self.assertEqual(row["quality_state"], "ELIGIBLE")
        self.assertIn('"anchor_count":0', row["attributes_json"])


if __name__ == "__main__":
    unittest.main()
