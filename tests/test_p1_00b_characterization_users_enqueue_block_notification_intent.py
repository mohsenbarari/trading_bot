"""Characterization tests for api.routers.users._enqueue_block_notification_intent.

This module characterizes only the block-intent helper and does not complete P1-00b or establish Offer/Request-origin interaction evidence; preserve current arguments and fallback behavior without calling runtime services.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import _enqueue_block_notification_intent
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)


class TestUsersEnqueueBlockNotificationIntent(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_block_notification_intent_representative(self):
        user = SimpleNamespace(id=101, telegram_id=987654321, sync_version=5)
        db_sentinel = object()
        message = "⛔ *اخطار مسدودیت حساب*\n\nحساب کاربری شما موقتاً مسدود شده است."

        with patch(
            "api.routers.users.enqueue_account_restriction_telegram_notification_once",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            await _enqueue_block_notification_intent(
                db_sentinel,  # type: ignore[arg-type]
                user=user,  # type: ignore[arg-type]
                message=message,
            )

            mock_enqueue.assert_awaited_once_with(
                db_sentinel,
                recipient=TelegramNotificationRecipient(
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                ),
                source_id=f"restriction-block:{user.id}:{int(user.sync_version)}",
                text=message,
                user=user,
                restriction_kind="block",
                user_sync_version=int(user.sync_version),
            )
            call_args = mock_enqueue.await_args
            self.assertIs(call_args.args[0], db_sentinel)
            self.assertIs(call_args.kwargs["user"], user)
            self.assertNotIn("parse_mode", call_args.kwargs)

    async def test_enqueue_block_notification_intent_fallback_sync_version(self):
        boundary_cases = [
            ("missing_sync_version", SimpleNamespace(id=202, telegram_id=123456789)),
            (
                "none_sync_version",
                SimpleNamespace(id=202, telegram_id=123456789, sync_version=None),
            ),
            (
                "zero_sync_version",
                SimpleNamespace(id=202, telegram_id=123456789, sync_version=0),
            ),
        ]
        for name, user in boundary_cases:
            with self.subTest(case=name):
                db_sentinel = object()
                message = "⛔ *اخطار مسدودیت حساب*\n\nتست فالبک نسخه همگامسازی."

                with patch(
                    "api.routers.users.enqueue_account_restriction_telegram_notification_once",
                    new_callable=AsyncMock,
                ) as mock_enqueue:
                    await _enqueue_block_notification_intent(
                        db_sentinel,  # type: ignore[arg-type]
                        user=user,  # type: ignore[arg-type]
                        message=message,
                    )

                    mock_enqueue.assert_awaited_once()
                    call_args = mock_enqueue.await_args
                    self.assertIs(call_args.args[0], db_sentinel)
                    self.assertEqual(
                        call_args.kwargs["recipient"],
                        TelegramNotificationRecipient(
                            user_id=int(user.id),
                            telegram_id=int(user.telegram_id),
                        ),
                    )
                    self.assertEqual(
                        call_args.kwargs["source_id"],
                        f"restriction-block:{user.id}:0",
                    )
                    self.assertTrue(call_args.kwargs["source_id"].endswith(":0"))
                    self.assertEqual(call_args.kwargs["text"], message)
                    self.assertIs(call_args.kwargs["user"], user)
                    self.assertEqual(
                        call_args.kwargs["restriction_kind"], "block"
                    )
                    self.assertEqual(call_args.kwargs["user_sync_version"], 0)
                    self.assertNotIn("parse_mode", call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
