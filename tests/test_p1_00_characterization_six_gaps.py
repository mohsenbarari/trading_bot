"""Side-effect-free characterization of the six P1-00 empty test_ref surfaces."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.notifications import send_test_push_notification
from bot.handlers import admin_broadcast
from bot.handlers import offer_overtime_preference as overtime_handler
from bot.handlers.telegram_publisher_channel_callbacks import (
    build_publisher_channel_callback_router,
)
from bot.handlers.trade_create import (
    Trade,
    handle_back_to_lot_type,
    handle_back_to_price,
)
from bot.states import OfferOvertimePreference
from core.enums import UserRole
from core.offer_overtime_bot_copy import (
    M_NOT_AVAILABLE_MESSAGE,
    build_overtime_preference_value_prompt,
)
from core.services.telegram_admin_broadcast_service import (
    TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY,
    TelegramAdminBroadcastAudienceType,
)


def _superadmin():
    return SimpleNamespace(id=7, role=UserRole.SUPER_ADMIN)


def _standard_user():
    return SimpleNamespace(id=11, role=UserRole.STANDARD, offer_overtime_minutes=3)


def _callback(data="tgb:gt:ordinary"):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
    )


def _state(data=None, current=None):
    payload = dict(data or {})

    async def get_data():
        return dict(payload)

    async def update_data(**values):
        payload.update(values)

    async def get_state():
        return current

    async def set_state(value):
        payload["_state"] = value

    async def clear():
        payload.clear()

    return SimpleNamespace(
        get_data=get_data,
        update_data=AsyncMock(side_effect=update_data),
        get_state=AsyncMock(side_effect=get_state),
        set_state=AsyncMock(side_effect=set_state),
        clear=AsyncMock(side_effect=clear),
        _payload=payload,
    )


def _public_trade_callback():
    router = build_publisher_channel_callback_router()
    for observer in router.callback_query.handlers:
        callback = observer.callback
        if getattr(callback, "__name__", "") == "public_trade_callback":
            return callback
    raise AssertionError("public_trade_callback was not registered on the publisher router")


class P100AdminTestPushCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_test_push_notification_fails_closed_when_unconfigured(self):
        with patch(
            "api.routers.notifications.web_push_config_status",
            return_value={"enabled": False, "missing": ["WEB_PUSH"]},
        ), patch(
            "api.routers.notifications.send_web_push_to_user",
            new=AsyncMock(),
        ) as send_mock:
            with self.assertRaises(HTTPException) as raised:
                await send_test_push_notification(
                    current_user=SimpleNamespace(id=9),
                    db=SimpleNamespace(),
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Web Push is not configured")
        send_mock.assert_not_awaited()

    async def test_send_test_push_notification_delegates_to_local_web_push(self):
        expected = {"total": 1, "sent": 1, "failed": 0, "disabled": 0}
        with patch(
            "api.routers.notifications.web_push_config_status",
            return_value={"enabled": True},
        ), patch(
            "api.routers.notifications.build_push_payload",
            return_value={"title": "اعلان تست"},
        ) as payload_mock, patch(
            "api.routers.notifications.send_web_push_to_user",
            new=AsyncMock(return_value=expected),
        ) as send_mock:
            result = await send_test_push_notification(
                current_user=SimpleNamespace(id=9),
                db=SimpleNamespace(),
            )

        payload_mock.assert_called_once()
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args.args[1], 9)
        self.assertEqual(result, expected)


class P100AdminBroadcastGroupToggleCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_toggle_group_recipient_rejects_non_superadmin(self):
        callback = _callback()
        state = _state()
        with patch.object(
            admin_broadcast,
            "answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await admin_broadcast.toggle_group_recipient(
                callback,
                state,
                SimpleNamespace(role=UserRole.STANDARD),
            )

        answer.assert_awaited_once_with(callback, "عدم دسترسی", show_alert=True)
        state.update_data.assert_not_awaited()

    async def test_toggle_group_recipient_rejects_unknown_group(self):
        callback = _callback("tgb:gt:unknown")
        with patch.object(
            admin_broadcast,
            "answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer, patch.object(
            admin_broadcast,
            "edit_callback_reply_markup_via_runtime",
            new=AsyncMock(),
        ) as edit:
            await admin_broadcast.toggle_group_recipient(callback, _state(), _superadmin())

        answer.assert_awaited_once_with(callback, "گروه نامعتبر است.", show_alert=True)
        edit.assert_not_awaited()

    async def test_toggle_group_recipient_adds_and_removes_supported_group(self):
        user = _superadmin()
        callback = _callback(f"tgb:gt:{TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY}")
        state = _state({"target_groups": []})
        with patch.object(
            admin_broadcast,
            "edit_callback_reply_markup_via_runtime",
            new=AsyncMock(),
        ) as edit, patch.object(
            admin_broadcast,
            "answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await admin_broadcast.toggle_group_recipient(callback, state, user)

        state.update_data.assert_awaited_once_with(
            audience_type=TelegramAdminBroadcastAudienceType.GROUP.value,
            target_groups=[TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY],
        )
        edit.assert_awaited_once()
        answer.assert_awaited_once_with(callback)

        state = _state({"target_groups": [TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY]})
        with patch.object(
            admin_broadcast,
            "edit_callback_reply_markup_via_runtime",
            new=AsyncMock(),
        ), patch.object(
            admin_broadcast,
            "answer_callback_query_via_runtime",
            new=AsyncMock(),
        ):
            await admin_broadcast.toggle_group_recipient(callback, state, user)

        self.assertEqual(
            state.update_data.await_args.kwargs["target_groups"],
            [],
        )


class P100OvertimePreferenceEntryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_offer_overtime_preference_denies_ineligible_user(self):
        message = SimpleNamespace(text="⏳ وقت اضافه", answer=AsyncMock())
        state = _state()
        user = _standard_user()
        with patch.object(
            overtime_handler,
            "_overtime_preference_allowed",
            new=AsyncMock(return_value=(False, M_NOT_AVAILABLE_MESSAGE)),
        ), patch.object(
            overtime_handler,
            "answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await overtime_handler.start_offer_overtime_preference(message, state, user)

        answer.assert_awaited_once()
        self.assertEqual(answer.await_args.args[2], M_NOT_AVAILABLE_MESSAGE)
        self.assertEqual(answer.await_args.kwargs["source_key"], "overtime-preference-start-denied")
        state.clear.assert_not_awaited()
        state.set_state.assert_not_awaited()

    async def test_start_offer_overtime_preference_opens_value_prompt(self):
        message = SimpleNamespace(text="⏳ وقت اضافه", answer=AsyncMock())
        state = _state()
        user = _standard_user()
        with patch.object(
            overtime_handler,
            "_overtime_preference_allowed",
            new=AsyncMock(return_value=(True, None)),
        ), patch.object(
            overtime_handler,
            "read_persisted_overtime_minutes",
            return_value=3,
        ), patch.object(
            overtime_handler,
            "answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await overtime_handler.start_offer_overtime_preference(message, state, user)

        state.clear.assert_awaited_once()
        state.set_state.assert_awaited_once_with(OfferOvertimePreference.awaiting_value)
        self.assertEqual(
            answer.await_args.args[2],
            build_overtime_preference_value_prompt(3),
        )
        self.assertEqual(answer.await_args.kwargs["source_key"], "overtime-preference-value-prompt")
        self.assertTrue(answer.await_args.kwargs["temporary_context_keyboard"])


class P100PublisherPublicTradeCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_trade_callback_delegates_to_channel_trade_core(self):
        handler = _public_trade_callback()
        callback = SimpleNamespace(id="cb-1")
        callback_data = SimpleNamespace(offer_id=44)
        user = SimpleNamespace(id=3)
        bot = SimpleNamespace()
        with patch(
            "bot.handlers.telegram_publisher_channel_callbacks._handle_channel_trade",
            new=AsyncMock(),
        ) as core:
            await handler(
                callback,
                callback_data,
                user,
                bot,
                trade_contention_preconfirmed=True,
                trade_contention_pre_gated=False,
            )

        core.assert_awaited_once_with(
            callback,
            callback_data,
            user,
            bot,
            trade_contention_preconfirmed=True,
            trade_contention_pre_gated=False,
        )


class P100TradeWizardBackCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_back_to_lot_type_rejects_stale_wizard_state(self):
        callback = _callback()
        state = _state(current=Trade.awaiting_notes.state)
        with patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer, patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit:
            await handle_back_to_lot_type(callback, state, user=SimpleNamespace(id=1))

        answer.assert_awaited_once_with(callback, "این مرحله دیگر فعال نیست.", show_alert=True)
        edit.assert_not_awaited()
        state.set_state.assert_not_awaited()

    async def test_handle_back_to_lot_type_restores_lot_type_prompt(self):
        callback = _callback()
        state = _state(current=Trade.awaiting_price.state)
        with patch(
            "bot.handlers.trade_create.get_lot_type_keyboard",
            return_value="LOT-KB",
        ), patch(
            "bot.handlers.trade_create.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ) as edit, patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await handle_back_to_lot_type(callback, state, user=SimpleNamespace(id=1))

        edit.assert_awaited_once()
        self.assertEqual(edit.await_args.args[2], "نحوه معامله را انتخاب کنید:")
        self.assertEqual(edit.await_args.kwargs["reply_markup"], "LOT-KB")
        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        answer.assert_awaited_once_with(callback)

    async def test_handle_back_to_price_answers_only_when_user_missing(self):
        callback = _callback()
        state = _state()
        with patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer, patch(
            "bot.handlers.trade_create._show_price_prompt",
            new=AsyncMock(),
        ) as prompt:
            await handle_back_to_price(callback, state, user=None)

        answer.assert_awaited_once_with(callback)
        prompt.assert_not_awaited()

    async def test_handle_back_to_price_reopens_price_prompt(self):
        callback = _callback()
        state = _state()
        user = SimpleNamespace(id=1)
        with patch(
            "bot.handlers.trade_create._show_price_prompt",
            new=AsyncMock(),
        ) as prompt, patch(
            "bot.handlers.trade_create.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ) as answer:
            await handle_back_to_price(callback, state, user=user)

        prompt.assert_awaited_once_with(callback.message, state, edit=True, user=user)
        answer.assert_awaited_once_with(callback)


if __name__ == "__main__":
    unittest.main()
