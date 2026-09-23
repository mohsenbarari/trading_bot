"""
Characterization test for accountant-context entry denial at parse_offer_text.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact Seed: API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184 (api/routers/offers.py:parse_offer_text)
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import tests.test_p1_00_characterization_network_isolation
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

tests.test_p1_00_characterization_network_isolation.install_p1_00_network_fence()
install_p1_00_network_fence()
FENCE = install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, status
from api.routers.offers import (
    ACCOUNTANT_MARKET_BLOCKED_DETAIL,
    ParseOfferRequest,
    parse_offer_text,
)
from core.services.accountant_relation_service import EffectiveOwnerActor
from models.user import User


class StrictFakeAsyncSession:
    """Strict database fake that fails if any operation is called."""

    def __getattr__(self, name: str):
        raise AssertionError(
            f"StrictFakeAsyncSession unexpectedly accessed attribute: {name}"
        )

    async def execute(self, *args, **kwargs):
        raise AssertionError("StrictFakeAsyncSession.execute was unexpectedly called")

    async def commit(self, *args, **kwargs):
        raise AssertionError("StrictFakeAsyncSession.commit was unexpectedly called")

    async def rollback(self, *args, **kwargs):
        raise AssertionError("StrictFakeAsyncSession.rollback was unexpectedly called")

    async def close(self, *args, **kwargs):
        raise AssertionError("StrictFakeAsyncSession.close was unexpectedly called")

    async def flush(self, *args, **kwargs):
        raise AssertionError("StrictFakeAsyncSession.flush was unexpectedly called")


class ParseOfferTextAccountantDenialCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize accountant-context denial before parser, database, or network calls."""

    async def test_accountant_context_denied_before_parser_or_database(self) -> None:
        denied_connects_before = len(FENCE.get("denied_connects", []))
        live_redis_constructs_before = len(FENCE.get("live_redis_constructs", []))

        fake_owner = User(
            id=1001,
            account_name="test_owner_1001",
            mobile_number="09121111001",
            full_name="Owner Test User",
            address="Tehran, District 1",
        )
        fake_actor = User(
            id=1002,
            account_name="test_accountant_1002",
            mobile_number="09121111002",
            full_name="Accountant Test User",
            address="Tehran, District 2",
        )
        self.assertNotEqual(fake_owner.id, fake_actor.id)

        context = EffectiveOwnerActor(
            owner_user=fake_owner,
            actor_user=fake_actor,
            relation=None,
            is_accountant_context=True,
        )

        request = ParseOfferRequest(text="فروش 100 سکه تمام 50000000")
        strict_db = StrictFakeAsyncSession()

        with patch(
            "bot.utils.offer_parser.parse_offer_text",
            new_callable=AsyncMock,
        ) as mock_parser:
            with self.assertRaises(HTTPException) as exc_info:
                await parse_offer_text(
                    request=request,
                    context=context,
                    db=strict_db,  # type: ignore[arg-type]
                )

            self.assertEqual(exc_info.exception.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(
                exc_info.exception.detail,
                ACCOUNTANT_MARKET_BLOCKED_DETAIL,
            )
            self.assertEqual(
                exc_info.exception.detail,
                "حسابدار دسترسی به بازار ندارد.",
            )

            mock_parser.assert_not_called()
            mock_parser.assert_not_awaited()

        self.assertEqual(
            len(FENCE.get("denied_connects", [])),
            denied_connects_before,
        )
        self.assertEqual(
            len(FENCE.get("live_redis_constructs", [])),
            live_redis_constructs_before,
        )
