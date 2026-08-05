"""Non-fatal product wiring for P7 accepted-selection telemetry."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from api.routers.offers import _record_webapp_inference_accepted_selection
from bot.handlers.trade_create import _record_bot_inference_accepted_selection
from core.market_intelligence.coin_catalog import CatalogCoinCommodityCandidate


def candidate() -> CatalogCoinCommodityCandidate:
    return CatalogCoinCommodityCandidate(
        commodity_id=71,
        commodity_code="IMAM",
        commodity_name="امام",
        center_project_price=186_900,
        lower_project_price=185_500,
        upper_project_price=188_300,
        confidence="HIGH",
        distance_to_center_relative=0.0,
    )


class _DB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class CoinInferenceOutcomeWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_webapp_writes_only_after_revalidation_and_keeps_failure_nonfatal(self) -> None:
        db = _DB()
        offer_data = SimpleNamespace(
            commodity_inference=SimpleNamespace(decision_key="a" * 64),
        )
        revalidation = SimpleNamespace(candidate=candidate())
        with patch(
            "api.routers.offers.append_coin_inference_accepted_selection",
            new=AsyncMock(),
        ) as append:
            await _record_webapp_inference_accepted_selection(
                db,
                revalidation=revalidation,
                offer_data=offer_data,
            )
        self.assertEqual((db.commits, db.rollbacks), (1, 0))
        self.assertEqual(append.await_args.args[1].source_surface, "WEBAPP")

        with patch(
            "api.routers.offers.append_coin_inference_accepted_selection",
            new=AsyncMock(side_effect=RuntimeError("ledger down")),
        ):
            await _record_webapp_inference_accepted_selection(
                db,
                revalidation=revalidation,
                offer_data=offer_data,
            )
        self.assertEqual((db.commits, db.rollbacks), (1, 1))

    async def test_bot_records_after_acceptance_and_telemetry_failure_is_nonfatal(self) -> None:
        session = _DB()
        revalidation = SimpleNamespace(candidate=candidate())
        with (
            patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=_SessionContext(session),
            ),
            patch(
                "bot.handlers.trade_create.append_coin_inference_accepted_selection",
                new=AsyncMock(),
            ) as append,
        ):
            await _record_bot_inference_accepted_selection(
                data={"coin_inference_decision_key": "a" * 64},
                revalidation=revalidation,
            )
        self.assertEqual(session.commits, 1)
        self.assertEqual(append.await_args.args[1].source_surface, "TELEGRAM_BOT")

        with (
            patch(
                "bot.handlers.trade_create.AsyncSessionLocal",
                return_value=_SessionContext(session),
            ),
            patch(
                "bot.handlers.trade_create.append_coin_inference_accepted_selection",
                new=AsyncMock(side_effect=RuntimeError("ledger down")),
            ),
        ):
            await _record_bot_inference_accepted_selection(
                data={"coin_inference_decision_key": "a" * 64},
                revalidation=revalidation,
            )
        self.assertEqual(session.commits, 1)


if __name__ == "__main__":
    unittest.main()
