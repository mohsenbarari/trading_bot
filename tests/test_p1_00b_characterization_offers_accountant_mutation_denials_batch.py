"""Hermetic P1-00b characterization tests for offers router accountant mutation denials batch."""

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
    cancel_all_active_offers,
    expire_offer,
)
from core.services.accountant_relation_service import EffectiveOwnerActor


class OffersAccountantMutationDenialsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner_user = SimpleNamespace(id=101, username="owner_trader")
        self.actor_user = SimpleNamespace(id=102, username="accountant_actor")

        self.assertIsNot(self.owner_user, self.actor_user)

        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,  # type: ignore[arg-type]
            actor_user=self.actor_user,  # type: ignore[arg-type]
            relation=None,
            is_accountant_context=True,
        )

        self.db_get_mock = AsyncMock(
            side_effect=AssertionError("db.get was unexpectedly called")
        )
        self.db_execute_mock = AsyncMock(
            side_effect=AssertionError("db.execute was unexpectedly called")
        )
        self.db = SimpleNamespace(
            get=self.db_get_mock,
            execute=self.db_execute_mock,
        )

    async def test_expire_offer_denies_accountant_before_settings_gate_or_database_access(self) -> None:
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        get_trading_settings_mock = Mock(
            side_effect=AssertionError("get_trading_settings was unexpectedly called")
        )
        try_acquire_gate_mock = AsyncMock(
            side_effect=AssertionError("try_acquire_offer_expiry_gate was unexpectedly called")
        )

        with patch(
            "api.routers.offers.get_trading_settings",
            get_trading_settings_mock,
        ), patch(
            "api.routers.offers.try_acquire_offer_expiry_gate",
            try_acquire_gate_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await expire_offer(
                    offer_id=9001,
                    db=self.db,  # type: ignore[arg-type]
                    context=self.context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        get_trading_settings_mock.assert_not_called()
        try_acquire_gate_mock.assert_not_called()
        self.db_get_mock.assert_not_called()
        self.db_execute_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_cancel_all_denies_accountant_before_service_or_side_effects(self) -> None:
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        cancel_all_authoritative_mock = AsyncMock(
            side_effect=AssertionError("cancel_all_active_offers_authoritatively was unexpectedly called")
        )
        expire_side_effects_mock = AsyncMock(
            side_effect=AssertionError("_expire_offer_side_effects was unexpectedly called")
        )
        set_active_count_mock = AsyncMock(
            side_effect=AssertionError("_set_active_offer_count_safely was unexpectedly called")
        )

        with patch(
            "api.routers.offers.cancel_all_active_offers_authoritatively",
            cancel_all_authoritative_mock,
        ), patch(
            "api.routers.offers._expire_offer_side_effects",
            expire_side_effects_mock,
        ), patch(
            "api.routers.offers._set_active_offer_count_safely",
            set_active_count_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await cancel_all_active_offers(
                    db=self.db,  # type: ignore[arg-type]
                    context=self.context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        cancel_all_authoritative_mock.assert_not_called()
        expire_side_effects_mock.assert_not_called()
        set_active_count_mock.assert_not_called()
        self.db_get_mock.assert_not_called()
        self.db_execute_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
