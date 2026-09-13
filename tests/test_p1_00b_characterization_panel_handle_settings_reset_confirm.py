"""Direct-characterization tests for bot.handlers.panel.handle_settings_reset_confirm.

This characterization alone does not update the seed registry,
reduce the canonical blocker, promote evidence, or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    handle_settings_reset_confirm,
)
from core.enums import UserRole  # noqa: E402

EXPECTED_DEFAULT_SETTINGS_PAYLOAD: dict[str, object] = {
    "invitation_expiry_days": 2,
    "offer_expiry_minutes": 2,
    "offer_min_quantity": 5,
    "offer_max_quantity": 50,
    "max_active_offers": 4,
    "competitive_price_validation_enabled": False,
    "offer_price_warning_enabled": True,
    "offer_expire_rate_per_minute": 2,
    "offer_expire_daily_limit_after_threshold": 10,
    "anti_abuse_daily_base": 2,
    "anti_abuse_weekly_base": 5,
    "anti_abuse_monthly_base": 7,
    "market_schedule_enabled": False,
    "market_timezone": "Asia/Tehran",
    "market_open_time_local": "09:00",
    "market_close_time_local": "18:00",
    "market_closed_weekdays": [],
}


class CharacterizePanelHandleSettingsResetConfirmTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_reset_confirm."""

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_confirm_unauthenticated_denied(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_refresh_cache: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_reject: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_confirm", id="cb_unauth")

        result = await handle_settings_reset_confirm(callback, None)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_cache.assert_not_called()
        mock_refresh_cache.assert_not_awaited()
        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_confirm_non_super_admin_denied(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_refresh_cache: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_reject: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_confirm", id="cb_non_super_admin")
        user = SimpleNamespace(id=42, role=UserRole.STANDARD, username="standard_user")

        result = await handle_settings_reset_confirm(callback, user)

        self.assertIsNone(result)
        mock_answer_callback.assert_awaited_once_with(callback)
        self.assertEqual(mock_answer_callback.await_args.args, (callback,))
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_reject.assert_not_called()
        mock_reject.assert_not_awaited()
        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_cache.assert_not_called()
        mock_refresh_cache.assert_not_awaited()
        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_confirm_super_admin_non_authoritative_short_circuit(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_refresh_cache: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_reject: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(data="settings_reset_confirm", id="cb_non_auth")
        user = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, username="super_admin_user")
        mock_reject.return_value = True

        result = await handle_settings_reset_confirm(callback, user)

        self.assertIsNone(result)
        mock_reject.assert_awaited_once_with(callback, "reset")
        self.assertEqual(mock_reject.await_args.args, (callback, "reset"))
        self.assertEqual(mock_reject.await_args.kwargs, {})

        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_cache.assert_not_called()
        mock_refresh_cache.assert_not_awaited()
        mock_answer_callback.assert_not_called()
        mock_answer_callback.assert_not_awaited()
        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()
        mock_edit_callback.assert_not_called()
        mock_edit_callback.assert_not_awaited()

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_confirm_super_admin_authorized_success(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_refresh_cache: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_reject: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        sentinel_callback = SimpleNamespace(data="settings_reset_confirm", id="cb_auth_101")
        sentinel_user = SimpleNamespace(id=101, role=UserRole.SUPER_ADMIN, username="super_admin_101")
        sentinel_text = "SENTINEL_RENDERED_SETTINGS_TEXT_SUCCESS"
        sentinel_keyboard = SimpleNamespace(inline_keyboard=[["sentinel_keyboard_success"]])

        call_order: list[str] = []

        async def record_reject(cb: object, op: str) -> bool:
            call_order.append("authority_check")
            return False

        async def record_save(payload: object) -> bool:
            call_order.append("save_trading_settings_async")
            return True

        async def record_refresh() -> None:
            call_order.append("refresh_settings_cache_async")

        async def record_answer(*args: object, **kwargs: object) -> None:
            ack_text = args[1] if len(args) > 1 else ""
            call_order.append(f"answer_callback_query_via_runtime:{ack_text}")

        async def record_get_text() -> str:
            call_order.append("get_settings_text")
            return sentinel_text

        def record_get_keyboard() -> object:
            call_order.append("get_settings_keyboard")
            return sentinel_keyboard

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        mock_reject.side_effect = record_reject
        mock_save_settings.side_effect = record_save
        mock_refresh_cache.side_effect = record_refresh
        mock_answer_callback.side_effect = record_answer
        mock_get_text.side_effect = record_get_text
        mock_get_keyboard.side_effect = record_get_keyboard
        mock_edit_callback.side_effect = record_edit_callback

        result = await handle_settings_reset_confirm(sentinel_callback, sentinel_user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "authority_check",
                "save_trading_settings_async",
                "refresh_settings_cache_async",
                "answer_callback_query_via_runtime:✅ تنظیمات بازنشانی شد",
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
            ],
        )

        mock_reject.assert_awaited_once_with(sentinel_callback, "reset")
        self.assertEqual(mock_reject.await_args.args, (sentinel_callback, "reset"))
        self.assertEqual(mock_reject.await_args.kwargs, {})

        mock_save_settings.assert_awaited_once()
        self.assertEqual(len(mock_save_settings.await_args.args), 1)
        self.assertEqual(mock_save_settings.await_args.kwargs, {})
        self.assertEqual(mock_save_settings.await_args.args[0], EXPECTED_DEFAULT_SETTINGS_PAYLOAD)

        mock_refresh_cache.assert_awaited_once_with()
        self.assertEqual(mock_refresh_cache.await_args.args, ())
        self.assertEqual(mock_refresh_cache.await_args.kwargs, {})

        mock_answer_callback.assert_awaited_once_with(sentinel_callback, "✅ تنظیمات بازنشانی شد")
        self.assertEqual(
            mock_answer_callback.await_args.args,
            (sentinel_callback, "✅ تنظیمات بازنشانی شد"),
        )
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_get_text.assert_awaited_once_with()
        self.assertEqual(mock_get_text.await_args.args, ())
        self.assertEqual(mock_get_text.await_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with()
        self.assertEqual(mock_get_keyboard.call_args.args, ())
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_callback)
        self.assertIs(call_args[1], sentinel_user)
        self.assertIs(call_args[2], sentinel_text)
        self.assertEqual(
            call_kwargs,
            {"parse_mode": "Markdown", "reply_markup": sentinel_keyboard},
        )

    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._reject_settings_callback_if_not_authoritative",
        new_callable=AsyncMock,
    )
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_settings_reset_confirm_super_admin_authorized_save_failure(
        self,
        mock_edit_callback: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_get_text: AsyncMock,
        mock_refresh_cache: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_reject: AsyncMock,
        mock_answer_callback: AsyncMock,
    ) -> None:
        sentinel_callback = SimpleNamespace(data="settings_reset_confirm", id="cb_auth_err")
        sentinel_user = SimpleNamespace(id=102, role=UserRole.SUPER_ADMIN, username="super_admin_102")
        sentinel_text = "SENTINEL_RENDERED_SETTINGS_TEXT_FAILURE"
        sentinel_keyboard = SimpleNamespace(inline_keyboard=[["sentinel_keyboard_failure"]])

        call_order: list[str] = []

        async def record_reject(cb: object, op: str) -> bool:
            call_order.append("authority_check")
            return False

        async def record_save(payload: object) -> bool:
            call_order.append("save_trading_settings_async")
            return False

        async def record_refresh() -> None:
            call_order.append("refresh_settings_cache_async")

        async def record_answer(*args: object, **kwargs: object) -> None:
            ack_text = args[1] if len(args) > 1 else ""
            call_order.append(f"answer_callback_query_via_runtime:{ack_text}")

        async def record_get_text() -> str:
            call_order.append("get_settings_text")
            return sentinel_text

        def record_get_keyboard() -> object:
            call_order.append("get_settings_keyboard")
            return sentinel_keyboard

        async def record_edit_callback(*args: object, **kwargs: object) -> None:
            call_order.append("edit_callback_message_via_runtime")

        mock_reject.side_effect = record_reject
        mock_save_settings.side_effect = record_save
        mock_refresh_cache.side_effect = record_refresh
        mock_answer_callback.side_effect = record_answer
        mock_get_text.side_effect = record_get_text
        mock_get_keyboard.side_effect = record_get_keyboard
        mock_edit_callback.side_effect = record_edit_callback

        result = await handle_settings_reset_confirm(sentinel_callback, sentinel_user)

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            [
                "authority_check",
                "save_trading_settings_async",
                "answer_callback_query_via_runtime:❌ خطا در بازنشانی",
                "get_settings_text",
                "get_settings_keyboard",
                "edit_callback_message_via_runtime",
            ],
        )

        mock_reject.assert_awaited_once_with(sentinel_callback, "reset")
        self.assertEqual(mock_reject.await_args.args, (sentinel_callback, "reset"))
        self.assertEqual(mock_reject.await_args.kwargs, {})

        mock_save_settings.assert_awaited_once()
        self.assertEqual(len(mock_save_settings.await_args.args), 1)
        self.assertEqual(mock_save_settings.await_args.kwargs, {})
        self.assertEqual(mock_save_settings.await_args.args[0], EXPECTED_DEFAULT_SETTINGS_PAYLOAD)

        mock_refresh_cache.assert_not_called()
        mock_refresh_cache.assert_not_awaited()

        mock_answer_callback.assert_awaited_once_with(sentinel_callback, "❌ خطا در بازنشانی")
        self.assertEqual(
            mock_answer_callback.await_args.args,
            (sentinel_callback, "❌ خطا در بازنشانی"),
        )
        self.assertEqual(mock_answer_callback.await_args.kwargs, {})

        mock_get_text.assert_awaited_once_with()
        self.assertEqual(mock_get_text.await_args.args, ())
        self.assertEqual(mock_get_text.await_args.kwargs, {})

        mock_get_keyboard.assert_called_once_with()
        self.assertEqual(mock_get_keyboard.call_args.args, ())
        self.assertEqual(mock_get_keyboard.call_args.kwargs, {})

        mock_edit_callback.assert_awaited_once()
        call_args, call_kwargs = mock_edit_callback.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_callback)
        self.assertIs(call_args[1], sentinel_user)
        self.assertIs(call_args[2], sentinel_text)
        self.assertEqual(
            call_kwargs,
            {"parse_mode": "Markdown", "reply_markup": sentinel_keyboard},
        )


if __name__ == "__main__":
    unittest.main()
