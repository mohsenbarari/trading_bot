"""Characterization tests for trade_create handle_repeat_offer_button entrypoint.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-REPEAT-OFFER-BUTTON-L2969
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import handle_repeat_offer_button
from bot.repeat_offer import BotRepeatOfferCandidate
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_STALE_BUTTON,
    TELEGRAM_OFFER_REPEAT_STALE_BUTTON_TEXT,
)


class TestTradeCreateRepeatOfferButtonCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard(self) -> None:
        message = SimpleNamespace(
            text="🔁 خرید آبشده - ۵ مثقال",
            message_id=987,
            chat=SimpleNamespace(id=456),
            answer=mock.AsyncMock(),
        )
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
        ) as mock_session_local, mock.patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new_callable=mock.AsyncMock,
        ) as mock_resolve, mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare, mock.patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new_callable=mock.AsyncMock,
        ) as mock_enqueue, mock.patch(
            "bot.handlers.trade_create._assert_legacy_repeat_offer_delivery_owner",
        ) as mock_legacy_owner, mock.patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new_callable=mock.AsyncMock,
        ) as mock_build_keyboard:
            result = await handle_repeat_offer_button(message, state=state, user=None)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])
        mock_session_local.assert_not_called()
        mock_resolve.assert_not_called()
        mock_prepare.assert_not_called()
        mock_enqueue.assert_not_called()
        mock_legacy_owner.assert_not_called()
        mock_build_keyboard.assert_not_called()
        message.answer.assert_not_called()

    async def test_authenticated_stale_candidate_queue_owned(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message_text = "🔁 خرید آبشده - ۵ مثقال"
        message = SimpleNamespace(
            text=message_text,
            message_id=987,
            chat=SimpleNamespace(id=456),
            answer=mock.AsyncMock(),
        )
        state = mock.AsyncMock()

        sentinel_session = object()
        mock_session_cm = mock.AsyncMock()
        mock_session_cm.__aenter__.return_value = sentinel_session
        mock_session_cm.__aexit__.return_value = None
        mock_session_local = mock.MagicMock(return_value=mock_session_cm)

        queue_result = SimpleNamespace(enqueued=True)

        with mock.patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            new=mock_session_local,
        ), mock.patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new_callable=mock.AsyncMock,
            return_value=(None, True, "stale_button"),
        ) as mock_resolve, mock.patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new_callable=mock.AsyncMock,
            return_value=queue_result,
        ) as mock_enqueue, mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare, mock.patch(
            "bot.handlers.trade_create._assert_legacy_repeat_offer_delivery_owner",
        ) as mock_legacy_owner, mock.patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new_callable=mock.AsyncMock,
        ) as mock_build_keyboard:
            result = await handle_repeat_offer_button(message, state=state, user=principal)

        self.assertIsNone(result)
        mock_session_local.assert_called_once_with()
        mock_session_cm.__aenter__.assert_awaited_once_with()
        mock_session_cm.__aexit__.assert_awaited_once()

        mock_resolve.assert_awaited_once_with(
            sentinel_session,
            owner_user_id=37,
            button_text=message_text,
        )
        self.assertIs(mock_resolve.await_args.args[0], sentinel_session)
        self.assertEqual(mock_resolve.await_args.kwargs["owner_user_id"], 37)
        self.assertEqual(mock_resolve.await_args.kwargs["button_text"], message_text)

        state.clear.assert_awaited_once_with()
        state.update_data.assert_not_called()

        mock_enqueue.assert_awaited_once_with(
            chat_id=456,
            user=principal,
            source_id="stale-button:987",
            response_kind=TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_STALE_BUTTON,
            text=TELEGRAM_OFFER_REPEAT_STALE_BUTTON_TEXT,
        )
        self.assertEqual(
            mock_enqueue.await_args.kwargs["response_kind"],
            "stale_button",
        )
        self.assertEqual(
            mock_enqueue.await_args.kwargs["text"],
            "متن این دکمه قدیمی است. منو با آخرین وضعیت به‌روزرسانی شد؛ دکمه جدید را بزنید.",
        )

        mock_prepare.assert_not_called()
        mock_legacy_owner.assert_not_called()
        mock_build_keyboard.assert_not_called()
        message.answer.assert_not_called()

    async def test_authenticated_resolved_candidate_prepared(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message_text = "🔁 خرید آبشده - ۵ مثقال"
        message = SimpleNamespace(
            text=message_text,
            message_id=987,
            chat=SimpleNamespace(id=456),
            answer=mock.AsyncMock(),
        )
        state = mock.AsyncMock()

        candidate = BotRepeatOfferCandidate(
            source_offer_id=101,
            source_offer_public_id="ofr_stage10_00000000000000000101",
            draft_text="خرید آبشده ۵ مثقال نقدی",
            button_text=message_text,
        )

        sentinel_session = object()
        mock_session_cm = mock.AsyncMock()
        mock_session_cm.__aenter__.return_value = sentinel_session
        mock_session_cm.__aexit__.return_value = None
        mock_session_local = mock.MagicMock(return_value=mock_session_cm)

        fixed_uuid = SimpleNamespace(hex="11223344556677889900aabbccddeeff")

        call_order: list[str] = []

        async def fake_clear():
            call_order.append("state.clear")

        async def fake_prepare(msg, st, usr, draft, *, edit_response, wizard_source):
            call_order.append("_prepare_text_offer")
            return True

        async def fake_update_data(**kwargs):
            call_order.append("state.update_data")

        state.clear.side_effect = fake_clear
        state.update_data.side_effect = fake_update_data

        with mock.patch(
            "bot.handlers.trade_create.AsyncSessionLocal",
            new=mock_session_local,
        ), mock.patch(
            "bot.handlers.trade_create.resolve_bot_repeat_offer_button_candidate",
            new_callable=mock.AsyncMock,
            return_value=(candidate, False, None),
        ) as mock_resolve, mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=fake_prepare,
        ) as mock_prepare, mock.patch(
            "bot.handlers.trade_create.uuid4",
            return_value=fixed_uuid,
        ), mock.patch(
            "bot.handlers.trade_create.enqueue_repeat_offer_response_if_queue_owner",
            new_callable=mock.AsyncMock,
        ) as mock_enqueue, mock.patch(
            "bot.handlers.trade_create._assert_legacy_repeat_offer_delivery_owner",
        ) as mock_legacy_owner, mock.patch(
            "bot.handlers.trade_create.build_persistent_navigation_keyboard",
            new_callable=mock.AsyncMock,
        ) as mock_build_keyboard:
            result = await handle_repeat_offer_button(message, state=state, user=principal)

        self.assertIsNone(result)
        mock_session_local.assert_called_once_with()
        mock_session_cm.__aenter__.assert_awaited_once_with()
        mock_session_cm.__aexit__.assert_awaited_once()

        mock_resolve.assert_awaited_once_with(
            sentinel_session,
            owner_user_id=37,
            button_text=message_text,
        )
        self.assertIs(mock_resolve.await_args.args[0], sentinel_session)
        self.assertEqual(mock_resolve.await_args.kwargs["owner_user_id"], 37)
        self.assertEqual(mock_resolve.await_args.kwargs["button_text"], message_text)

        self.assertEqual(
            call_order,
            ["state.clear", "_prepare_text_offer", "state.update_data"],
        )

        state.clear.assert_awaited_once_with()
        mock_prepare.assert_awaited_once_with(
            message,
            state,
            principal,
            "خرید آبشده ۵ مثقال نقدی",
            edit_response=False,
            wizard_source=False,
        )
        state.update_data.assert_awaited_once_with(
            republished_from_offer_public_id="ofr_stage10_00000000000000000101",
            republished_from_offer_id=101,
            republish_idempotency_key="bot-repeat:11223344556677889900aabbccddeeff",
        )

        mock_enqueue.assert_not_called()
        mock_legacy_owner.assert_not_called()
        mock_build_keyboard.assert_not_called()
        message.answer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
