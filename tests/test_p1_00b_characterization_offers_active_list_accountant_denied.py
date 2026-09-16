"""Hermetic P1-00b characterization tests for get_active_offers accountant entry denial."""

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
    get_active_offers,
)
from core.services.accountant_relation_service import EffectiveOwnerActor


class OffersActiveListAccountantDeniedCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.owner_user = SimpleNamespace(id=101, username="owner_trader")
        self.actor_user = SimpleNamespace(id=102, username="accountant_actor")
        self.current_user = SimpleNamespace(id=103, username="standalone_user")

        self.assertIsNot(self.owner_user, self.actor_user)
        self.assertIsNot(self.current_user, self.owner_user)
        self.assertIsNot(self.current_user, self.actor_user)

        self.context = EffectiveOwnerActor(
            owner_user=self.owner_user,
            actor_user=self.actor_user,
            relation=None,
            is_accountant_context=True,
        )

        self.db_execute_mock = AsyncMock(
            side_effect=AssertionError("db.execute was unexpectedly called")
        )
        self.db_commit_mock = AsyncMock(
            side_effect=AssertionError("db.commit was unexpectedly called")
        )
        self.db_rollback_mock = AsyncMock(
            side_effect=AssertionError("db.rollback was unexpectedly called")
        )
        self.db = SimpleNamespace(
            execute=self.db_execute_mock,
            commit=self.db_commit_mock,
            rollback=self.db_rollback_mock,
        )

    async def test_active_offers_denies_accountant_before_query_build(self) -> None:
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        build_read_options_mock = Mock(
            side_effect=AssertionError("build_offer_read_options was unexpectedly called")
        )
        select_mock = Mock(
            side_effect=AssertionError("select was unexpectedly called")
        )

        with patch(
            "api.routers.offers.build_offer_read_options",
            build_read_options_mock,
        ), patch(
            "api.routers.offers.select",
            select_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await get_active_offers(
                    offer_type=None,
                    commodity_id=None,
                    skip=0,
                    limit=50,
                    db=self.db,
                    current_user=self.current_user,
                    context=self.context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        build_read_options_mock.assert_not_called()
        select_mock.assert_not_called()
        self.db_execute_mock.assert_not_called()
        self.db_commit_mock.assert_not_called()
        self.db_rollback_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
