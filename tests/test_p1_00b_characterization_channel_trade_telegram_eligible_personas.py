from __future__ import annotations

import tempfile
from pathlib import Path

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import sqlite3
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot

from bot.callbacks import ChannelTradePublicCallback
from bot.handlers.trade_execute import handle_channel_trade_public
from bot.utils.redis_helpers import _memory_fallback
from core.config import settings
from core.enums import UserRole
from core.server_routing import SERVER_FOREIGN
from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
from models.offer_request import OfferRequestSourceSurface, OfferRequestStatus
from tests.test_p1_00b_characterization_channel_trade_telegram_offer_success import _SessionWrapper
from tests.test_p1_00b_characterization_origin_interactions import (
    OriginSession,
    _make_offer,
    _make_owner,
    _request_patches,
    _sqlite_counts,
    _sqlite_requests,
)


class _SessionScope:
    def __init__(self, wrapper: _SessionWrapper):
        self._wrapper = wrapper

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._wrapper

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


class P100bTelegramEligiblePersonaCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _memory_fallback["confirmations"].clear()

    def tearDown(self):
        _memory_fallback["confirmations"].clear()

    async def _execute_eligible_persona_double_click(
        self,
        *,
        role: UserRole,
        relation: CustomerRelation | None,
    ):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "test_eligible_persona.db")
            engine, Session = _sqlite_requests(db_path)

            offer = _make_offer("TELEGRAM_BOT")
            buyer = _make_owner(id=5, role=role)

            if relation is not None:
                relation.customer_user_id = buyer.id
                relation.owner_user_id = offer.user_id

            sync_session = Session()
            origin_session = OriginSession(sync_session, offer=offer, owner=buyer)
            wrapper = _SessionWrapper(origin_session)

            callback_data = ChannelTradePublicCallback(
                offer_public_id=offer.offer_public_id,
                amount=offer.quantity,
            )
            callback = SimpleNamespace(
                id="cb_eligible_test_1",
                data=f"ct2:{offer.offer_public_id}:{offer.quantity}",
                from_user=SimpleNamespace(id=buyer.telegram_id or 555, username="eligible_buyer"),
                message=SimpleNamespace(
                    chat=SimpleNamespace(id=getattr(settings, "channel_id", 0) or 0),
                    message_id=777,
                ),
                answer=AsyncMock(),
            )
            bot = AsyncMock(spec=Bot)

            mock_is_user_accountant = AsyncMock(return_value=False)
            mock_get_relation = AsyncMock(return_value=relation)
            mock_answer = AsyncMock()
            mock_forward = AsyncMock()

            with ExitStack() as stack:
                for p in _request_patches(SERVER_FOREIGN, relation=relation):
                    stack.enter_context(p)

                stack.enter_context(
                    patch(
                        "bot.handlers.trade_execute.AsyncSessionLocal",
                        new=_SessionScope(wrapper),
                    )
                )
                stack.enter_context(
                    patch(
                        "bot.utils.redis_helpers.get_redis_client",
                        new=AsyncMock(return_value=None),
                    )
                )
                stack.enter_context(
                    patch(
                        "core.services.bot_access_policy.is_user_accountant",
                        new=mock_is_user_accountant,
                    )
                )
                stack.enter_context(
                    patch(
                        "core.services.accountant_relation_service.is_user_accountant",
                        new=mock_is_user_accountant,
                    )
                )
                stack.enter_context(
                    patch(
                        "core.services.bot_access_policy.get_active_customer_relation_for_user",
                        new=mock_get_relation,
                    )
                )
                stack.enter_context(
                    patch(
                        "core.services.customer_relation_service.get_active_customer_relation_for_user",
                        new=mock_get_relation,
                    )
                )
                stack.enter_context(
                    patch(
                        "bot.handlers.trade_execute.answer_callback_query_via_runtime",
                        new=mock_answer,
                    )
                )
                stack.enter_context(
                    patch(
                        "bot.handlers.trade_execute.forward_trade_to_home_server",
                        new=mock_forward,
                    )
                )

                # Click 1: First click must not persist trade/request, must record confirmation marker
                await handle_channel_trade_public(
                    callback=callback,
                    callback_data=callback_data,
                    user=buyer,
                    bot=bot,
                )

                counts_1 = _sqlite_counts(db_path)
                self.assertEqual(counts_1["trades"], 0)
                self.assertEqual(counts_1["offer_requests"], 0)

                expected_confirm_key = f"confirm:{buyer.id}:{offer.id}:{offer.quantity}"
                self.assertIn(expected_confirm_key, _memory_fallback["confirmations"])
                mock_answer.assert_awaited_once_with(
                    callback,
                    "برای تایید دوباره روی همان دکمه بزنید ☑️",
                    show_alert=False,
                )

                # Click 2: Second click must persist exactly one Trade and one completed OfferRequest
                await handle_channel_trade_public(
                    callback=callback,
                    callback_data=callback_data,
                    user=buyer,
                    bot=bot,
                )

                counts_2 = _sqlite_counts(db_path)
                self.assertEqual(counts_2["trades"], 1)
                self.assertEqual(counts_2["offer_requests"], 1)
                self.assertIsNotNone(counts_2["trade_number"])
                self.assertIsNotNone(counts_2["resulting_trade_id"])

                self.assertEqual(mock_answer.await_count, 2)
                mock_answer.assert_awaited_with(
                    callback,
                    "معامله ثبت شد ✅",
                    show_alert=False,
                )
                self.assertNotIn(expected_confirm_key, _memory_fallback["confirmations"])

                # Inspect sqlite rows directly for provenance and completion
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    trade_row = conn.execute("SELECT * FROM trades").fetchone()
                    req_row = conn.execute("SELECT * FROM offer_requests").fetchone()

                self.assertIsNotNone(trade_row)
                self.assertIsNotNone(req_row)
                self.assertIsNotNone(trade_row["id"])
                self.assertIsNotNone(trade_row["trade_number"])
                self.assertEqual(req_row["resulting_trade_id"], trade_row["id"])
                self.assertEqual(
                    req_row["request_source_surface"],
                    OfferRequestSourceSurface.TELEGRAM_BOT.value,
                )
                self.assertEqual(
                    req_row["result_status"],
                    OfferRequestStatus.COMPLETED_TRADE.value,
                )
                self.assertEqual(req_row["request_home_server"], SERVER_FOREIGN)
                self.assertEqual(req_row["request_source_server"], SERVER_FOREIGN)
                self.assertEqual(req_row["offer_public_id"], offer.offer_public_id)

                # Assert forward_trade_to_home_server was never called
                mock_forward.assert_not_called()

                # Assert callback offer lookup used offer_public_id rather than offers.id
                self.assertGreater(len(wrapper.recorded_offer_statements), 0)
                for stmt in wrapper.recorded_offer_statements:
                    stmt_str = str(stmt).lower()
                    self.assertIn("offer_public_id", stmt_str)
                    self.assertNotIn("offers.id ==", stmt_str)
                    self.assertNotIn("offers.id =", stmt_str)

                # Policy lookups assertion: active _SessionWrapper and user id on both clicks
                self.assertEqual(mock_is_user_accountant.call_count, 2)
                self.assertEqual(mock_get_relation.call_count, 2)
                for call_args in mock_is_user_accountant.call_args_list:
                    self.assertIs(call_args[0][0], wrapper)
                    self.assertEqual(call_args[0][1], buyer.id)
                for call_args in mock_get_relation.call_args_list:
                    self.assertIs(call_args[0][0], wrapper)
                    self.assertEqual(call_args[0][1], buyer.id)

                # Confirm persona was never denied
                for call_args in mock_answer.call_args_list:
                    msg = call_args[0][1]
                    self.assertNotIn("مسدود", msg)
                    self.assertNotIn("غیرفعال", msg)
                    self.assertNotIn("حسابدار", msg)
                    self.assertNotIn("مجاز نیست", msg)

            sync_session.close()
            engine.dispose()

    async def test_telegram_offer_telegram_linked_tier1_double_click_persists_once(self):
        relation = CustomerRelation(
            id=101,
            owner_user_id=9,
            customer_user_id=5,
            created_by_user_id=9,
            invitation_token="inv_token_tier1_valid",
            management_name="Tier1 Management",
            customer_tier=CustomerTier.TIER_1,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
            sync_version=1,
        )
        await self._execute_eligible_persona_double_click(
            role=UserRole.STANDARD,
            relation=relation,
        )

    async def test_telegram_offer_telegram_middle_manager_double_click_persists_once(self):
        await self._execute_eligible_persona_double_click(
            role=UserRole.MIDDLE_MANAGER,
            relation=None,
        )

    async def test_telegram_offer_telegram_police_double_click_persists_once(self):
        await self._execute_eligible_persona_double_click(
            role=UserRole.POLICE,
            relation=None,
        )
