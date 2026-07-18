import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import panel
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class _SessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotPanelPersistentAnchorQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_mode_persists_panel_menus_without_deleting_active_anchor(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=7007),
            message_id=91,
            answer=AsyncMock(),
        )
        standard_user = SimpleNamespace(
            id=7,
            telegram_id=7007,
            sync_version=4,
            role=panel.UserRole.STANDARD,
            account_status=None,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
        )
        profile_user = SimpleNamespace(
            **standard_user.__dict__,
            account_name="customer",
            full_name="Customer",
        )
        admin_user = SimpleNamespace(
            id=8,
            telegram_id=7007,
            sync_version=5,
            role=panel.UserRole.SUPER_ADMIN,
        )
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)

        with (
            patch(
                "bot.handlers.panel.configured_telegram_delivery_runtime",
                return_value=runtime,
            ),
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new=AsyncMock(return_value=SimpleNamespace()),
            ) as enqueue,
            patch(
                "bot.handlers.panel.delete_previous_anchor",
                new=AsyncMock(),
            ) as delete_anchor,
            patch("bot.handlers.panel.set_anchor") as set_anchor,
            patch(
                "bot.handlers.panel.AsyncSessionLocal",
                return_value=_SessionContext(),
            ),
            patch(
                "bot.handlers.panel.build_user_panel_navigation_keyboard",
                new=AsyncMock(return_value={"keyboard": [[{"text": "user"}]]}),
            ),
            patch(
                "bot.handlers.panel.build_admin_panel_navigation_keyboard",
                new=AsyncMock(return_value={"keyboard": [[{"text": "admin"}]]}),
            ),
            patch(
                "bot.handlers.panel.attach_customer_management_names",
                new=AsyncMock(),
            ),
            patch(
                "bot.handlers.panel.settings",
                SimpleNamespace(bot_username="botname"),
            ),
        ):
            with patch(
                "bot.handlers.panel._can_use_customer_panel",
                new=AsyncMock(return_value=True),
            ):
                await panel.show_my_profile_and_change_keyboard(
                    message,
                    SimpleNamespace(),
                    standard_user,
                )
            with patch(
                "bot.handlers.panel._can_use_customer_panel",
                new=AsyncMock(return_value=False),
            ):
                await panel.show_my_profile_and_change_keyboard(
                    message,
                    SimpleNamespace(),
                    profile_user,
                )
            await panel.show_admin_panel_and_change_keyboard(
                message,
                SimpleNamespace(),
                admin_user,
            )

        self.assertEqual(
            [call.kwargs["source_key"] for call in enqueue.await_args_list],
            [
                "panel-user-main-menu",
                "panel-user-profile-menu",
                "panel-admin-main-menu",
            ],
        )
        self.assertTrue(
            all(
                call.kwargs["set_persistent_anchor"]
                for call in enqueue.await_args_list
            )
        )
        delete_anchor.assert_not_awaited()
        set_anchor.assert_not_called()
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
