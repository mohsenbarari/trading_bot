"""Offline integration tests for private-gold payload → quote orchestration."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.private_gold_payloads import PrivateGoldPayloadEnvelope
from core.market_intelligence.private_gold_pipeline import process_private_gold_payloads
from core.market_intelligence.private_gold_staging import (
    connect_private_gold_staging,
    initialize_private_gold_staging,
)


def _offer(*, available_at_utc: str = "2026-08-04T12:01:00Z") -> PrivateGoldPayloadEnvelope:
    return PrivateGoldPayloadEnvelope(
        stream="OFFER",
        available_at_utc=available_at_utc,
        payload_text=json.dumps(
            {
                "schema_version": "1.0",
                "event_type": "message_created",
                "source": {"market": "gold", "source_key": "account1_channel"},
                "gold": {
                    "message_type": "offer",
                    "message_id": "101",
                    "telegram_datetime": "2026-08-04T12:00:00Z",
                    "text": "80,300,000 فروش 5 تا با حواله",
                },
            },
            ensure_ascii=False,
        ),
    )


def _trade() -> PrivateGoldPayloadEnvelope:
    return PrivateGoldPayloadEnvelope(
        stream="TRADE",
        available_at_utc="2026-08-04T12:01:00Z",
        payload_text=json.dumps(
            {
                "schema_version": "1.0",
                "event_type": "offer_verified",
                "source": {"market": "gold", "source_key": "account1_channel"},
                "gold": {
                    "message_id": "101",
                    "verification": {"state": "DONE"},
                    "trade": {
                        "status": "FULL",
                        "traded_quantity": 5,
                        "trade_detected_at": "2026-08-04T12:00:40Z",
                        "telegram_edit_datetime": "2026-08-04T12:00:45Z",
                    },
                },
            },
            ensure_ascii=False,
        ),
    )


class PrivateGoldPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.staging = connect_private_gold_staging(root / "private.sqlite3")
        self.market = connect_market_store(root / "market.sqlite3")
        initialize_private_gold_staging(self.staging)
        initialize_market_store(self.market)

    def tearDown(self) -> None:
        self.staging.close()
        self.market.close()
        self.tempdir.cleanup()

    def test_pipeline_reconciles_then_refreshes_closed_paper_minute_idempotently(self) -> None:
        report = process_private_gold_payloads(
            self.staging,
            self.market,
            (_trade(), _offer()),
            as_of_utc="2026-08-04T12:01:00Z",
        )
        self.staging.commit()
        self.market.commit()

        self.assertEqual(
            (
                report.decoded_offers,
                report.decoded_trade_updates,
                report.promotion.offer_facts_upserted,
                report.promotion.trade_facts_upserted,
                report.refreshed_paper_minutes,
            ),
            (1, 1, 1, 1, 1),
        )
        quote = self.market.execute(
            "SELECT price_num, attributes_json FROM market_observations WHERE source_code = 'PRIVATE_GOLD_PAPER_MINUTE'"
        ).fetchone()
        self.assertEqual(quote["price_num"], 80_300_000.0)
        self.assertIn('"trade_count":1', quote["attributes_json"])

        process_private_gold_payloads(
            self.staging,
            self.market,
            (_trade(), _offer()),
            as_of_utc="2026-08-04T12:01:30Z",
        )
        self.staging.commit()
        self.market.commit()
        self.assertEqual(
            self.market.execute("SELECT COUNT(*) FROM market_observations").fetchone()[0],
            3,
        )

    def test_current_unclosed_minute_stays_raw_until_closed(self) -> None:
        report = process_private_gold_payloads(
            self.staging,
            self.market,
            (_offer(available_at_utc="2026-08-04T12:00:30Z"),),
            as_of_utc="2026-08-04T12:00:30Z",
        )
        self.staging.commit()
        self.market.commit()

        self.assertEqual((report.promotion.offer_facts_upserted, report.refreshed_paper_minutes), (1, 0))
        self.assertEqual(
            self.market.execute("SELECT COUNT(*) FROM market_observations WHERE source_code = 'PRIVATE_GOLD_PAPER_MINUTE'").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
