"""Hermetic P1-00b characterization tests for offers router accountant entry denials batch."""

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
    OfferCreate,
    create_offer,
    get_active_offer_page,
)
from core.services.accountant_relation_service import EffectiveOwnerActor


class OffersAccountantEntryDenialsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
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

        self.offer_data = OfferCreate(
            offer_type="buy",
            settlement_type="cash",
            commodity_id=1,
            quantity=10,
            price=1000000,
            is_wholesale=True,
            lot_sizes=None,
            notes=None,
            republished_from_id=None,
            republished_from_public_id=None,
            warning_acknowledged=False,
            commodity_inference=None,
            idempotency_key="idemp:offer:characterization:001",
        )

    async def test_create_offer_denies_accountant_before_creation_work(self) -> None:
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        build_creation_command_mock = Mock(
            side_effect=AssertionError(
                "_build_webapp_offer_creation_command was unexpectedly called"
            )
        )
        load_replay_mock = AsyncMock(
            side_effect=AssertionError(
                "_load_offer_idempotency_replay was unexpectedly awaited"
            )
        )
        revalidate_inference_mock = AsyncMock(
            side_effect=AssertionError(
                "_revalidate_webapp_commodity_inference was unexpectedly awaited"
            )
        )

        with patch(
            "api.routers.offers._build_webapp_offer_creation_command",
            build_creation_command_mock,
        ), patch(
            "api.routers.offers._load_offer_idempotency_replay",
            load_replay_mock,
        ), patch(
            "api.routers.offers._revalidate_webapp_commodity_inference",
            revalidate_inference_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await create_offer(
                    offer_data=self.offer_data,
                    db=self.db,
                    current_user=self.current_user,
                    context=self.context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        build_creation_command_mock.assert_not_called()
        load_replay_mock.assert_not_called()
        revalidate_inference_mock.assert_not_called()
        self.db_execute_mock.assert_not_called()
        self.db_commit_mock.assert_not_called()
        self.db_rollback_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_active_offer_page_denies_accountant_before_query_build(self) -> None:
        fence = tests.test_p1_00_characterization_network_isolation._FENCE
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        filter_signature_mock = Mock(
            side_effect=AssertionError(
                "_active_offer_filter_signature was unexpectedly called"
            )
        )
        build_page_query_mock = Mock(
            side_effect=AssertionError(
                "_build_active_offer_page_query was unexpectedly called"
            )
        )

        with patch(
            "api.routers.offers._active_offer_filter_signature",
            filter_signature_mock,
        ), patch(
            "api.routers.offers._build_active_offer_page_query",
            build_page_query_mock,
        ):
            with self.assertRaises(HTTPException) as cm:
                await get_active_offer_page(
                    offer_type=None,
                    settlement_type=None,
                    commodity_id=None,
                    own_only=False,
                    cursor=None,
                    limit=50,
                    db=self.db,
                    current_user=self.current_user,
                    context=self.context,
                )

        self.assertEqual(cm.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(cm.exception.detail, ACCOUNTANT_MARKET_BLOCKED_DETAIL)
        self.assertEqual(ACCOUNTANT_MARKET_BLOCKED_DETAIL, "حسابدار دسترسی به بازار ندارد.")

        filter_signature_mock.assert_not_called()
        build_page_query_mock.assert_not_called()
        self.db_execute_mock.assert_not_called()
        self.db_commit_mock.assert_not_called()
        self.db_rollback_mock.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
