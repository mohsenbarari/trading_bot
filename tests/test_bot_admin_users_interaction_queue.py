import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import admin_users
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class BotAdminUsersInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_back_to_admin_persists_menu_before_old_anchor_cleanup(self):
        message = SimpleNamespace(
            answer=AsyncMock(),
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=7007),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={"anchor_id": 11, "users_menu_id": 12}
            ),
            clear=AsyncMock(),
        )
        user = SimpleNamespace(id=7, role=admin_users.UserRole.SUPER_ADMIN)
        runtime = SimpleNamespace(mode=TelegramDeliveryRuntimeMode.QUEUE_V1)

        with (
            patch(
                "bot.handlers.admin_users.configured_telegram_delivery_runtime",
                return_value=runtime,
            ),
            patch(
                "bot.handlers.admin_users.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
            patch(
                "bot.handlers.admin_users.build_admin_panel_navigation_keyboard",
                new=AsyncMock(
                    return_value={"keyboard": [[{"text": "admin"}]]}
                ),
            ),
            patch(
                "bot.handlers.admin_users.delete_user_message",
                new=AsyncMock(),
            ) as delete_user_message,
            patch("bot.handlers.admin_users.asyncio.create_task") as create_task,
        ):
            await admin_users.handle_back_to_admin(message, user, state)

        delete_user_message.assert_awaited_once_with(message)
        state.clear.assert_awaited_once_with()
        create_task.assert_not_called()
        enqueue.assert_awaited_once()
        self.assertEqual(
            enqueue.await_args.kwargs["source_key"],
            "admin-users-back-to-panel",
        )
        self.assertTrue(enqueue.await_args.kwargs["set_persistent_anchor"])
        message.answer.assert_not_awaited()

    async def test_non_authoritative_message_rejection_uses_interaction_queue(self):
        message = SimpleNamespace(answer=AsyncMock())
        user = SimpleNamespace(id=7, role=admin_users.UserRole.SUPER_ADMIN)

        with (
            patch(
                "bot.handlers.admin_users._users_admin_write_decision",
                return_value=SimpleNamespace(ok=False),
            ),
            patch(
                "bot.handlers.admin_users.admin_write_rejection_message",
                return_value="سرور فعلی مرجع نیست",
            ),
            patch(
                "bot.handlers.admin_users.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
        ):
            rejected = await admin_users._reject_users_message_if_not_authoritative(
                message,
                user,
                "max_block_update",
            )

        self.assertTrue(rejected)
        enqueue.assert_awaited_once_with(
            message,
            user,
            "❌ سرور فعلی مرجع نیست",
            source_key="admin-users-not-authoritative",
        )
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
