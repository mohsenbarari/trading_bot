import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import telegram_pre_auth_interaction as adapter
from core.telegram_delivery_queue_contract import TelegramDestinationClass
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


def _runtime(mode):
    return SimpleNamespace(mode=mode)


def _message(*, chat_id=7007, message_id=91):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="private"),
        from_user=SimpleNamespace(id=chat_id),
        message_id=message_id,
        answer=AsyncMock(return_value=SimpleNamespace(message_id=92)),
        edit_text=AsyncMock(return_value=SimpleNamespace(message_id=message_id)),
    )


class TelegramPreAuthInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_answer_preserves_aiogram_call(self):
        message = _message()
        markup = object()
        with patch.object(
            adapter,
            "configured_telegram_delivery_runtime",
            return_value=_runtime(TelegramDeliveryRuntimeMode.LEGACY),
        ):
            result = await adapter.answer_pre_auth_message_via_runtime(
                message,
                "ادامه ثبت‌نام",
                parse_mode="Markdown",
                reply_markup=markup,
            )
        self.assertEqual(result.message_id, 92)
        message.answer.assert_awaited_once_with(
            "ادامه ثبت‌نام",
            parse_mode="Markdown",
            reply_markup=markup,
        )

    async def test_queue_answer_persists_private_route_without_raw_event_id(self):
        message = _message()
        session = SimpleNamespace(commit=AsyncMock())
        expected = object()
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(adapter, "current_server", return_value="foreign"),
            patch.object(
                adapter,
                "enqueue_pre_auth_interaction_once",
                new=AsyncMock(return_value=expected),
            ) as enqueue,
        ):
            result = await adapter.answer_pre_auth_message_via_runtime(
                message,
                "ادامه ثبت‌نام",
                session=session,
            )
        self.assertIs(result, expected)
        message.answer.assert_not_awaited()
        self.assertEqual(enqueue.await_args.kwargs["chat_id"], 7007)
        source_id = enqueue.await_args.kwargs["source_id"]
        self.assertNotIn("7007", source_id)
        self.assertNotIn("91", source_id)
        session.commit.assert_awaited_once()

    async def test_queue_edit_rejects_non_private_or_cross_user_route(self):
        for chat_type, actor_id, reason in (
            ("group", 7007, "private_chat_required"),
            ("private", 8008, "private_route_mismatch"),
        ):
            message = _message()
            message.chat.type = chat_type
            message.from_user.id = actor_id
            callback = SimpleNamespace(
                id="raw-callback-secret",
                from_user=message.from_user,
                message=message,
            )
            with patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ):
                with self.subTest(reason=reason), self.assertRaisesRegex(
                    adapter.TelegramPreAuthInteractionRouteError,
                    reason,
                ):
                    await adapter.edit_pre_auth_callback_message_via_runtime(
                        callback,
                        "ویرایش",
                        session=SimpleNamespace(commit=AsyncMock()),
                    )

    async def test_non_private_security_reply_uses_durable_shared_chat_route(self):
        message = _message(chat_id=-1007007)
        message.chat.type = "supergroup"
        message.from_user.id = 7007
        session = SimpleNamespace(commit=AsyncMock())
        expected = object()
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(adapter, "current_server", return_value="foreign"),
            patch.object(
                adapter,
                "enqueue_pre_auth_interaction_once",
                new=AsyncMock(return_value=expected),
            ) as enqueue,
        ):
            result = await adapter.answer_pre_auth_non_private_message_via_runtime(
                message,
                "ثبت‌نام فقط در گفت‌وگوی خصوصی",
                source_key="registration-non-private-rejected",
                session=session,
            )

        self.assertIs(result, expected)
        message.answer.assert_not_awaited()
        self.assertEqual(enqueue.await_args.kwargs["chat_id"], -1007007)
        self.assertEqual(
            enqueue.await_args.kwargs["destination_class"],
            TelegramDestinationClass.CHANNEL,
        )
        source_id = enqueue.await_args.kwargs["source_id"]
        self.assertNotIn("1007007", source_id)
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
