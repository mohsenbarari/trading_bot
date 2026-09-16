"""Hermetic P1-00b characterization test for get_market_offer_history accountant denial."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import tests.test_p1_00_characterization_network_isolation
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()
install_p1_00_network_fence()

from fastapi import HTTPException, status

from api.routers.offers import (
    ACCOUNTANT_MARKET_BLOCKED_DETAIL,
    _resolve_offer_owner_context,
    get_market_offer_history,
)
from core.services.accountant_relation_service import EffectiveOwnerActor


class OffersMarketHistoryAccountantDeniedCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_accountant_context_precedes_current_user_and_denies_before_query(self):
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        owner_user = SimpleNamespace(id=101, username="owner_trader")
        actor_user = SimpleNamespace(id=102, username="accountant_actor")
        current_user = SimpleNamespace(id=103, username="standalone_user")

        self.assertIsNot(owner_user, actor_user)
        self.assertIsNot(current_user, owner_user)
        self.assertIsNot(current_user, actor_user)

        context = EffectiveOwnerActor(
            owner_user=owner_user,
            actor_user=actor_user,
            relation=None,
            is_accountant_context=True,
        )

        fallback_context = _resolve_offer_owner_context(None, current_user)
        self.assertFalse(fallback_context.is_accountant_context)
        self.assertIs(fallback_context.owner_user, current_user)
        self.assertIs(fallback_context.actor_user, current_user)

        db_execute_mock = AsyncMock(side_effect=AssertionError("db.execute was unexpectedly called"))
        db = SimpleNamespace(execute=db_execute_mock)

        utc_now_mock = Mock(side_effect=AssertionError("utc_now_naive was unexpectedly called"))
        trading_settings_mock = AsyncMock(side_effect=AssertionError("get_trading_settings_async was unexpectedly called"))

        with patch("api.routers.offers.utc_now_naive", utc_now_mock), patch(
            "core.trading_settings.get_trading_settings_async", trading_settings_mock
        ):
            with self.assertRaises(HTTPException) as cm:
                await get_market_offer_history(
                    skip=0,
                    limit=25,
                    since_hours=48,
                    db=db,
                    current_user=current_user,
                    context=context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        utc_now_mock.assert_not_called()
        trading_settings_mock.assert_not_called()
        db_execute_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
