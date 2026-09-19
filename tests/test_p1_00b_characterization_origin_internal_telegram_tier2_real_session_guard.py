"""P1-00b characterization test: origin INTERNAL Telegram TIER_2 denial before offer lookup.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Scenario: ORIGIN-INTERNAL__TELEGRAM_BOT__POLICY-SNAPSHOT

Outcome:
Single TIER_2 denial-before-offer-lookup outcome when handling a public channel
trade callback for an INTERNAL origin offer using a genuine AsyncSession.

Explicit exclusions:
Explicit exclusions: success, double-click confirmation, database mutation,
substitute session adapters, ordered result queues, generic owner/None fallbacks,
tables, notification, retry, reconciliation, origin-cell closure, product changes,
production/operational data, network access, deploy/DNS/Writer work, and Stage closure.

Hermetic and deterministic under APP_ENV_FILE=config/unit-test.env.example.
"""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Canonical network fence must be installed before repository application imports.
install_p1_00_network_fence()

from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from aiogram import Bot, types
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from bot.callbacks import ChannelTradePublicCallback
from bot.handlers.trade_execute import handle_channel_trade_public
from core.enums import UserRole
from core.services.bot_access_policy import (
    BOT_ACCESS_REASON_CUSTOMER_TIER2,
    bot_access_denial_message,
)
from models.customer_relation import (
    CustomerRelation,
    CustomerRelationStatus,
    CustomerTier,
)
from tests.test_p1_00b_characterization_origin_interactions import (
    _make_offer,
    _make_owner,
)
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


class P100bOriginInternalTelegramTier2RealSessionGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize Telegram TIER_2 denial before offer lookup using genuine AsyncSession."""

    async def test_internal_offer_telegram_tier2_denied_before_offer_lookup_with_real_async_session(self) -> None:
        # 1. Construct canonical owner and INTERNAL offer
        owner = _make_owner(has_bot_access=True, role=UserRole.STANDARD)
        offer = _make_offer("INTERNAL")
        self.assertEqual(offer.offer_public_id, "ofr_internal_7")

        # 2. Construct callback data and callback query exclusively from offer identity and quantity
        callback_data = ChannelTradePublicCallback(
            offer_public_id=offer.offer_public_id,
            amount=offer.quantity,
        )
        callback = MagicMock(spec=types.CallbackQuery)
        callback.data = callback_data.pack()
        self.assertEqual(callback.data, f"ct2:{offer.offer_public_id}:{offer.quantity}")

        # 3. Construct unpersisted active non-deleted CustomerRelation with TIER_2
        customer_relation = CustomerRelation(
            owner_user_id=owner.id,
            customer_user_id=owner.id,
            invitation_token="inv_internal_tier2_projection_token",
            management_name="canonical_internal_tier2_mgmt",
            customer_tier=CustomerTier.TIER_2,
            status=CustomerRelationStatus.ACTIVE,
            deleted_at=None,
        )
        self.assertEqual(customer_relation.customer_tier, CustomerTier.TIER_2)
        self.assertEqual(customer_relation.status, CustomerRelationStatus.ACTIVE)
        self.assertIsNone(customer_relation.deleted_at)

        # 4. Engine setup: hermetic in-memory StaticPool SQLite engine without tables
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        executed_statements: list[str] = []

        def _record_statement(conn, cursor, statement, parameters, context, executemany):
            executed_statements.append(statement)

        event.listen(engine, "before_cursor_execute", _record_statement)

        try:
            # 5. Patch bounded seams needed to observe the path
            mock_is_accountant = AsyncMock(return_value=False)
            mock_get_relation = AsyncMock(return_value=customer_relation)
            mock_answer = AsyncMock()
            mock_load_offer = AsyncMock()
            mock_check_limits = MagicMock(return_value=(True, None))

            bot = AsyncMock(spec=Bot)

            with patch("bot.handlers.trade_execute.AsyncSessionLocal", side_effect=lambda: real_async_session(engine)), \
                 patch("bot.handlers.trade_execute.check_user_limits", mock_check_limits), \
                 patch("bot.handlers.trade_execute.answer_callback_query_via_runtime", mock_answer), \
                 patch("core.services.bot_access_policy.is_user_accountant", mock_is_accountant), \
                 patch("core.services.bot_access_policy.get_active_customer_relation_for_user", mock_get_relation), \
                 patch("bot.handlers.trade_execute._load_callback_offer", mock_load_offer):

                await handle_channel_trade_public(
                    callback=callback,
                    callback_data=callback_data,
                    user=owner,
                    bot=bot,
                )

            # 6. Assertions
            # Both policy I/O seams awaited exactly once with same captured real AsyncSession and owner.id
            mock_is_accountant.assert_awaited_once()
            session_accountant, user_id_accountant = mock_is_accountant.call_args[0]
            self.assertEqual(user_id_accountant, owner.id)

            mock_get_relation.assert_awaited_once()
            session_relation, user_id_relation = mock_get_relation.call_args[0]
            self.assertEqual(user_id_relation, owner.id)

            self.assertIs(session_accountant, session_relation)
            self.assertIsInstance(session_accountant, AsyncSession)

            # Assert _load_callback_offer was not awaited
            mock_load_offer.assert_not_awaited()
            self.assertEqual(mock_load_offer.await_count, 0)

            # Expected source-backed TIER_2 denial text
            expected_denial_text = f"⚠️ دسترسی این حساب به ربات تلگرام فعال نیست.\n\nبرای استفاده از بازار فقط از وب{chr(0x200c)}اپ استفاده کنید."
            self.assertEqual(expected_denial_text, bot_access_denial_message(BOT_ACCESS_REASON_CUSTOMER_TIER2))

            # Callback answered exactly once with expected denial text and show_alert=True
            mock_answer.assert_awaited_once_with(
                callback,
                expected_denial_text,
                show_alert=True,
            )

            # Zero database queries performed by the genuine AsyncSession
            self.assertEqual(executed_statements, [])
        finally:
            try:
                event.remove(engine, "before_cursor_execute", _record_statement)
            except Exception:
                pass
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
