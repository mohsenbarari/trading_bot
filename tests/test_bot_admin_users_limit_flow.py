import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin_users import handle_limit_cancel, handle_limit_confirm, process_limit_value
from core.enums import NotificationCategory, NotificationLevel, UserRole


def consume_task(coro):
    coro.close()
    return None


class FakeScalarOneResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, value):
        self.value = value
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        return FakeScalarOneResult(self.value)


class BotAdminUsersLimitFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit_confirm_and_cancel_cover_missing_and_protected_targets(self):
        callback = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={
            "limit_target_user_id": 9,
            "limit_expire_at": datetime(2026, 1, 1, 12, 0, 0),
            "limit_max_trades": 1,
            "limit_max_commodities": None,
            "limit_max_requests": None,
        }))
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)):
            await handle_limit_confirm(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        callback.answer.assert_awaited_once_with("❌ کاربر یافت نشد.", show_alert=True)

        protected_user = SimpleNamespace(
            id=9,
            role=UserRole.SUPER_ADMIN,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
            trades_count=0,
            commodities_traded_count=0,
            channel_messages_count=0,
            trading_restricted_until=None,
            telegram_id=123,
        )
        callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()))
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(protected_user)):
            await handle_limit_confirm(callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER), state=state)
        callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

        cancel_callback = SimpleNamespace(data="limit_cancel_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(None)
        ):
            await handle_limit_cancel(cancel_callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=SimpleNamespace())
        cancel_callback.answer.assert_awaited_once_with("عملیات لغو شد.")

        cancel_callback = SimpleNamespace(data="limit_cancel_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(SimpleNamespace(
                id=9,
                role=UserRole.SUPER_ADMIN,
                trading_restricted_until=None,
                max_daily_trades=None,
                max_active_commodities=None,
                max_daily_requests=None,
                can_block_users=True,
                max_blocked_users=5,
            ))
        ):
            await handle_limit_cancel(cancel_callback, user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER), state=SimpleNamespace())
        cancel_callback.answer.assert_awaited_once_with("❌ شما مجاز به مدیریت این کاربر نیستید.", show_alert=True)

    async def test_process_limit_value_handles_invalid_and_valid_inputs(self):
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(text="5")
        await process_limit_value(message, state=state, user=None)
        state.clear.assert_awaited_once()

        message = SimpleNamespace(
            text="bad",
            answer=AsyncMock(return_value=SimpleNamespace(message_id=41)),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = SimpleNamespace(get_data=AsyncMock(return_value={"limit_target_user_id": 9}))
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task
        ), patch("bot.handlers.admin_users.safe_delete_message", new=AsyncMock()):
            await process_limit_value(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("عدد صحیح معتبر", message.answer.await_args.args[0])

        negative_msg = SimpleNamespace(message_id=43)
        message = SimpleNamespace(
            text="-1",
            answer=AsyncMock(return_value=negative_msg),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
            "bot.handlers.admin_users.asyncio.create_task", side_effect=consume_task
        ), patch("bot.handlers.admin_users.safe_delete_message", new=AsyncMock()):
            await process_limit_value(message, state=SimpleNamespace(get_data=AsyncMock(return_value={"limit_target_user_id": 9})), user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertIn("عدد صحیح معتبر", message.answer.await_args.args[0])

        for editing, expected_kwargs in [
            ("trades", {"limit_max_trades": 5}),
            ("commodities", {"limit_max_commodities": 5}),
            ("requests", {"limit_max_requests": 5}),
        ]:
            msg = SimpleNamespace(message_id=42)
            message = SimpleNamespace(
                text="5",
                answer=AsyncMock(return_value=msg),
                bot=SimpleNamespace(),
                chat=SimpleNamespace(id=1),
            )
            state = SimpleNamespace(
                get_data=AsyncMock(side_effect=[{"limit_editing": editing, "limit_target_user_id": 9}, {**expected_kwargs, "limit_target_user_id": 9}]),
                update_data=AsyncMock(),
                set_state=AsyncMock(),
            )
            with patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()), patch(
                "bot.keyboards.get_limit_settings_keyboard", return_value="KB"
            ) as keyboard_mock, patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()) as anchor_mock:
                await process_limit_value(message, state=state, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            state.update_data.assert_awaited_once_with(**expected_kwargs)
            state.set_state.assert_awaited_once_with(None)
            keyboard_mock.assert_called_once()
            anchor_mock.assert_awaited_once_with(state, 42, message.bot, 1)

    async def test_handle_limit_confirm_and_cancel_cover_key_branches(self):
        callback = SimpleNamespace(answer=AsyncMock())
        await handle_limit_confirm(callback, user=None, state=SimpleNamespace())
        callback.answer.assert_not_awaited()

        callback = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={
            "limit_target_user_id": 9,
            "limit_expire_at": datetime(2026, 1, 1, 12, 0, 0),
            "limit_max_trades": None,
            "limit_max_commodities": None,
            "limit_max_requests": None,
        }))
        await handle_limit_confirm(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        callback.answer.assert_awaited_once_with("⚠️ لطفاً حداقل یک محدودیت تنظیم کنید.", show_alert=True)

        target_user = SimpleNamespace(
            id=9,
            telegram_id=123,
            trading_restricted_until=datetime(2100, 1, 1, 0, 0, 0),
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
            limitations_expire_at=None,
            trades_count=7,
            commodities_traded_count=8,
            channel_messages_count=9,
        )
        session = FakeSession(target_user)
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={
            "limit_target_user_id": 9,
            "limit_expire_at": datetime(2026, 1, 1, 12, 0, 0),
            "limit_max_trades": 1,
            "limit_max_commodities": 2,
            "limit_max_requests": 3,
        }))
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=session
        ), patch(
            "bot.handlers.admin_users.create_user_notification", new=AsyncMock()
        ) as notify_mock, patch("bot.handlers.admin_users.send_telegram_notification", new=AsyncMock()) as telegram_mock, patch(
            "bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()
        ) as clear_mock, patch("bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")), patch(
            "bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="KB") as keyboard_mock:
            await handle_limit_confirm(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=state)
        session.commit.assert_awaited_once()
        self.assertEqual(target_user.max_daily_trades, 1)
        self.assertEqual(target_user.max_active_commodities, 2)
        self.assertEqual(target_user.trades_count, 0)
        self.assertEqual(notify_mock.await_args.kwargs["level"], NotificationLevel.WARNING)
        self.assertEqual(notify_mock.await_args.kwargs["category"], NotificationCategory.SYSTEM)
        self.assertIn("مجموع ارسال لفظ در کانال: 3", notify_mock.await_args.args[2])
        telegram_mock.assert_awaited_once()
        clear_mock.assert_awaited_once_with(state)
        keyboard_mock.assert_called_once_with(user_id=9, is_restricted=True, has_limitations=True)
        callback.answer.assert_awaited_once_with("✅ محدودیت‌ها اعمال شد.", show_alert=True)

        cancel_callback = SimpleNamespace(data="limit_cancel_9", answer=AsyncMock())
        await handle_limit_cancel(cancel_callback, user=None, state=SimpleNamespace())
        cancel_callback.answer.assert_not_awaited()

        target_user = SimpleNamespace(
            id=9,
            trading_restricted_until=datetime(2100, 1, 1, 0, 0, 0),
            max_daily_trades=1,
            max_active_commodities=None,
            max_daily_requests=None,
            can_block_users=True,
            max_blocked_users=5,
        )
        callback = SimpleNamespace(data="limit_cancel_9", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()) as clear_mock, patch(
            "bot.handlers.admin_users.AsyncSessionLocal", return_value=FakeSession(target_user)
        ), patch("bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="PROFILE")), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard", return_value="KB") as keyboard_mock:
            await handle_limit_cancel(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=SimpleNamespace())
        clear_mock.assert_awaited_once()
        keyboard_mock.assert_called_once_with(9, is_restricted=True, has_limitations=True, can_block=True, max_blocked=5, can_edit_role=True)
        callback.answer.assert_awaited_once_with("عملیات لغو شد.")


if __name__ == "__main__":
    unittest.main()
