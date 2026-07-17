import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_link_token_service import TelegramLinkTokenError
from bot.handlers.start import handle_start_without_token
from bot.handlers.start import handle_start_with_token


class BotStartWithoutTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_start_with_valid_link_token_prompts_for_contact(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.load_pending_telegram_link_token_user_for_update",
            new=AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())),
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args="link_raw-token"), state=state, user=None)

        self.assertIn("شماره موبایل همین حساب", message.answer.await_args.args[0])
        state.update_data.assert_awaited_once()
        state.set_state.assert_awaited_once()
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_with_recent_link_token_waits_for_sync_race(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        raw_token = "A" * 43
        loader = AsyncMock(
            side_effect=[
                TelegramLinkTokenError("invalid"),
                (SimpleNamespace(), SimpleNamespace(), SimpleNamespace()),
            ]
        )

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.load_pending_telegram_link_token_user_for_update",
            new=loader,
        ), patch("bot.handlers.start.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args=f"link_{raw_token}"), state=state, user=None)

        self.assertEqual(loader.await_count, 2)
        sleep_mock.assert_awaited_once()
        self.assertIn("شماره موبایل همین حساب", message.answer.await_args.args[0])
        state.update_data.assert_awaited_once()
        state.set_state.assert_awaited_once()
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_with_terminal_link_token_status_does_not_retry(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
        raw_token = "B" * 43
        loader = AsyncMock(side_effect=TelegramLinkTokenError("used"))

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.load_pending_telegram_link_token_user_for_update",
            new=loader,
        ), patch("bot.handlers.start.asyncio.sleep", new=AsyncMock()) as sleep_mock, patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args=f"link_{raw_token}"), state=state, user=None)

        loader.assert_awaited_once()
        sleep_mock.assert_not_awaited()
        self.assertIn("لینک اتصال آماده نیست", message.answer.await_args.args[0])
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_repeated_start_shows_links_without_welcome_for_registered_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        user = SimpleNamespace(id=44, full_name="Ali", account_name="ali_44", role="standard")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.start.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
        ) as menu_mock, patch(
            "bot.handlers.link_account.build_channel_access_text",
            new=AsyncMock(return_value="🔗 کانال معاملات:\nhttps://t.me/+start_token"),
        ), patch(
            "bot.handlers.link_account.build_webapp_plain_link_line",
            return_value="🌐 ورود به وب اپ:\nhttps://app.example",
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_without_token(message, state=SimpleNamespace(), user=user)

        delete_anchor.assert_awaited_once()
        menu_mock.assert_awaited_once()
        self.assertEqual(
            message.answer.await_args.args[0],
            "حساب شما فعال است. از لینک‌های زیر برای ورود استفاده کنید:\n\n"
            "🔗 کانال معاملات:\nhttps://t.me/+start_token\n\n"
            "🌐 ورود به وب اپ:\nhttps://app.example\n\n"
            "برای دسترسی به سایر امکانات، از دکمه‌های منو استفاده کنید.",
        )
        self.assertNotIn("خوش آمدید", message.answer.await_args.args[0])
        self.assertIsNone(message.answer.await_args.kwargs.get("parse_mode"))
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_with_link_token_for_registered_user_sends_channel_join_link(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        user = SimpleNamespace(id=45, full_name="Reza", account_name="reza_45", role="standard")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
        ), patch(
            "bot.handlers.link_account.build_channel_access_text",
            new=AsyncMock(return_value="🔗 کانال معاملات:\nhttps://t.me/+deep_token"),
        ), patch(
            "bot.handlers.link_account.build_webapp_plain_link_line",
            return_value="🌐 ورود به وب اپ:\nhttps://app.example",
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(message, SimpleNamespace(args="link_raw-token"), state=SimpleNamespace(), user=user)

        self.assertIn("https://t.me/+deep_token", message.answer.await_args.args[0])
        self.assertIn("https://app.example", message.answer.await_args.args[0])
        self.assertNotIn("خوش آمدید", message.answer.await_args.args[0])
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "menu")
        self.assertIsNone(message.answer.await_args.kwargs.get("parse_mode"))
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_reopened_invitation_for_registered_user_uses_returning_links(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10, type="private"),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=55)),
        )
        user = SimpleNamespace(id=46, full_name="Sara", account_name="sara_46", role="standard")

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()), patch(
            "bot.handlers.start._direct_registration_runtime_ready", return_value=False
        ), patch(
            "bot.handlers.start.build_persistent_navigation_keyboard",
            new=AsyncMock(return_value="menu"),
        ), patch(
            "bot.handlers.link_account.build_channel_access_text",
            new=AsyncMock(return_value="🔗 کانال معاملات:\nhttps://t.me/+invite_again"),
        ), patch(
            "bot.handlers.link_account.build_webapp_plain_link_line",
            return_value="🌐 ورود به وب اپ:\nhttps://app.example",
        ), patch("bot.handlers.start.set_anchor") as set_anchor:
            await handle_start_with_token(
                message,
                SimpleNamespace(args="INV-existing-token"),
                state=SimpleNamespace(),
                user=user,
            )

        self.assertIn("حساب شما فعال است", message.answer.await_args.args[0])
        self.assertIn("https://t.me/+invite_again", message.answer.await_args.args[0])
        self.assertIn("https://app.example", message.answer.await_args.args[0])
        self.assertNotIn("خوش آمدید", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 55)

    async def test_handle_start_without_token_returns_neutral_fallback_for_unknown_user(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=10),
            answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
        )

        with patch("bot.handlers.start.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.start.set_anchor"
        ) as set_anchor:
            await handle_start_without_token(message, state=SimpleNamespace(), user=None)

        delete_anchor.assert_awaited_once()
        self.assertIn("ثبت‌نام را در سامانه کامل کنید", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 77)


if __name__ == "__main__":
    unittest.main()
