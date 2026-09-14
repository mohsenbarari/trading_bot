"""Stage P1-00b characterization tests for bot default fallback handlers.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Seeds:
- BOT-TG-CALLBACK-QUERY-DEFAULT-ACKNOWLEDGE-NOOP-CALLBACK-L17
- BOT-TG-CALLBACK-QUERY-DEFAULT-HANDLE-UNMATCHED-CALLBACK-L24
- BOT-TG-MESSAGE-DEFAULT-HANDLE-UNAUTHORIZED-MESSAGES-L43

This bounded evidence neither reconciles inventory/counts nor closes the Stage.
"""

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.default import (
    STALE_PANEL_BUTTON_MESSAGE,
    acknowledge_noop_callback,
    handle_unauthorized_messages,
    handle_unmatched_callback,
)

EXPECTED_STALE_PANEL_BUTTON_MESSAGE = (
    "این دکمه دیگر فعال نیست. پنل را دوباره باز کنید."
)

EXPECTED_UNAUTHORIZED_MESSAGE = (
    "⛔️ بات برای شما هنوز فعال نشده است. "
    "اگر حساب شما قبلاً در وب یا با خط فرمان ساخته شده، دستور /link را بزنید و شماره همان حساب را ارسال کنید. "
    f"در غیر این صورت با لینک دعوت معتبر ثبت{chr(0x200C)}نام کنید."
)


class P100bDefaultFallbackHandlersCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for default fallback handlers under P1-00b."""

    def test_stale_panel_button_message_constant_matches_expected(self):
        self.assertEqual(STALE_PANEL_BUTTON_MESSAGE, EXPECTED_STALE_PANEL_BUTTON_MESSAGE)

    async def test_acknowledge_noop_callback_awaits_answer_without_copy_or_side_effects(self):
        callback = SimpleNamespace(id="cb_noop_1001", data="noop")
        with (
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
            patch("bot.handlers.default.logger") as mock_logger,
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
        ):
            await acknowledge_noop_callback(callback)

            self.assertEqual(mock_answer_cb.await_count, 1)
            self.assertEqual(len(mock_answer_cb.await_args.args), 1)
            self.assertIs(mock_answer_cb.await_args.args[0], callback)
            self.assertEqual(mock_answer_cb.await_args.kwargs, {})

            mock_logger.info.assert_not_called()
            mock_is_deleted.assert_not_called()
            mock_answer_pre_auth.assert_not_called()

    async def test_handle_unmatched_callback_authenticated_user_with_long_prefix(self):
        prefix = "p" * 40
        callback_data = f"{prefix}:subaction_99"
        expected_logged_prefix = prefix[:32]
        callback = SimpleNamespace(id="cb_unmatched_auth_101", data=callback_data)
        user = SimpleNamespace(id=5001)

        with (
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
            patch("bot.handlers.default.logger") as mock_logger,
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
        ):
            await handle_unmatched_callback(callback, user=user)

            self.assertEqual(mock_answer_cb.await_count, 1)
            self.assertEqual(len(mock_answer_cb.await_args.args), 2)
            self.assertIs(mock_answer_cb.await_args.args[0], callback)
            self.assertEqual(mock_answer_cb.await_args.args[1], STALE_PANEL_BUTTON_MESSAGE)
            self.assertEqual(mock_answer_cb.await_args.kwargs, {"show_alert": True})

            mock_logger.info.assert_called_once_with(
                "Rejected unmatched Telegram callback",
                extra={
                    "event": "telegram.callback_unmatched",
                    "callback_prefix": expected_logged_prefix,
                    "authenticated": True,
                },
            )
            mock_is_deleted.assert_not_called()
            mock_answer_pre_auth.assert_not_called()

    async def test_handle_unmatched_callback_unauthenticated_user_none_data_none(self):
        callback = SimpleNamespace(id="cb_unmatched_unauth_102", data=None)

        with (
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
            patch("bot.handlers.default.logger") as mock_logger,
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
        ):
            await handle_unmatched_callback(callback, user=None)

            self.assertEqual(mock_answer_cb.await_count, 1)
            self.assertEqual(len(mock_answer_cb.await_args.args), 2)
            self.assertIs(mock_answer_cb.await_args.args[0], callback)
            self.assertEqual(mock_answer_cb.await_args.args[1], STALE_PANEL_BUTTON_MESSAGE)
            self.assertEqual(mock_answer_cb.await_args.kwargs, {"show_alert": True})

            mock_logger.info.assert_called_once_with(
                "Rejected unmatched Telegram callback",
                extra={
                    "event": "telegram.callback_unmatched",
                    "callback_prefix": None,
                    "authenticated": False,
                },
            )
            mock_is_deleted.assert_not_called()
            mock_answer_pre_auth.assert_not_called()

    async def test_handle_unauthorized_messages_authenticated_user_ignored(self):
        message = SimpleNamespace(message_id=201, from_user=SimpleNamespace(id=2001), text="ping")
        user = SimpleNamespace(id=2001)

        with (
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
        ):
            await handle_unauthorized_messages(message, user=user)

            mock_is_deleted.assert_not_called()
            mock_answer_pre_auth.assert_not_called()
            mock_answer_cb.assert_not_called()

    async def test_handle_unauthorized_messages_deleted_telegram_user_suppressed(self):
        message = SimpleNamespace(message_id=202, from_user=SimpleNamespace(id=10), text="start")

        with (
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
        ):
            mock_is_deleted.return_value = True
            await handle_unauthorized_messages(message, user=None)

            self.assertEqual(mock_is_deleted.await_count, 1)
            mock_is_deleted.assert_awaited_once_with(10)
            mock_answer_pre_auth.assert_not_called()
            mock_answer_cb.assert_not_called()

    async def test_handle_unauthorized_messages_unknown_user_answers_pre_auth(self):
        message = SimpleNamespace(message_id=203, from_user=SimpleNamespace(id=11), text="help")

        with (
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
        ):
            mock_is_deleted.return_value = False
            await handle_unauthorized_messages(message, user=None)

            self.assertEqual(mock_is_deleted.await_count, 1)
            mock_is_deleted.assert_awaited_once_with(11)

            self.assertEqual(mock_answer_pre_auth.await_count, 1)
            self.assertEqual(len(mock_answer_pre_auth.await_args.args), 2)
            self.assertIs(mock_answer_pre_auth.await_args.args[0], message)
            self.assertEqual(mock_answer_pre_auth.await_args.args[1], EXPECTED_UNAUTHORIZED_MESSAGE)
            self.assertEqual(mock_answer_pre_auth.await_args.kwargs, {})
            mock_answer_cb.assert_not_called()

    async def test_handle_unauthorized_messages_missing_from_user_answers_pre_auth_without_lookup(self):
        message = SimpleNamespace(message_id=204, from_user=None, text="channel_post")

        with (
            patch("bot.handlers.default.is_deleted_telegram_user", new_callable=AsyncMock) as mock_is_deleted,
            patch("bot.handlers.default.answer_pre_auth_message_via_runtime", new_callable=AsyncMock) as mock_answer_pre_auth,
            patch("bot.handlers.default.answer_callback_query_via_runtime", new_callable=AsyncMock) as mock_answer_cb,
        ):
            await handle_unauthorized_messages(message, user=None)

            mock_is_deleted.assert_not_called()

            self.assertEqual(mock_answer_pre_auth.await_count, 1)
            self.assertEqual(len(mock_answer_pre_auth.await_args.args), 2)
            self.assertIs(mock_answer_pre_auth.await_args.args[0], message)
            self.assertEqual(mock_answer_pre_auth.await_args.args[1], EXPECTED_UNAUTHORIZED_MESSAGE)
            self.assertEqual(mock_answer_pre_auth.await_args.kwargs, {})
            mock_answer_cb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
