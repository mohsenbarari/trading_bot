import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot import telegram_interaction_message as adapter
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultRequirement,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


def _runtime(mode):
    return SimpleNamespace(mode=mode)


def _message(*, chat_id=7007, message_id=91):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        message_id=message_id,
        answer=AsyncMock(return_value=SimpleNamespace(message_id=92)),
    )


def _user(*, telegram_id=7007):
    return SimpleNamespace(id=7, telegram_id=telegram_id, sync_version=4)


def _callback(*, callback_id="callback-raw-secret", chat_id=7007, message_id=91):
    return SimpleNamespace(
        id=callback_id,
        message=_message(chat_id=chat_id, message_id=message_id),
    )


class TelegramInteractionMessageAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_mode_preserves_exact_aiogram_call_and_result(self):
        message = _message()
        markup = object()
        with patch.object(
            adapter,
            "configured_telegram_delivery_runtime",
            return_value=_runtime(TelegramDeliveryRuntimeMode.LEGACY),
        ):
            result = await adapter.answer_incoming_message_via_runtime(
                message,
                _user(),
                "پاسخ",
                source_key="block-search-short",
                parse_mode="Markdown",
                reply_markup=markup,
            )

        self.assertEqual(result.message_id, 92)
        message.answer.assert_awaited_once_with(
            "پاسخ",
            parse_mode="Markdown",
            reply_markup=markup,
        )

    async def test_queue_mode_persists_non_anchor_reply_without_aiogram_send(self):
        message = _message()
        user = _user()
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
                "enqueue_private_interaction_once",
                new=AsyncMock(return_value=expected),
            ) as enqueue,
        ):
            result = await adapter.answer_incoming_message_via_runtime(
                message,
                user,
                "کاربری یافت نشد",
                source_key="block-search-empty",
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                reply_markup={"inline_keyboard": []},
                session=session,
            )

        self.assertIs(result, expected)
        message.answer.assert_not_awaited()
        session.commit.assert_awaited_once()
        kwargs = enqueue.await_args.kwargs
        self.assertEqual(kwargs["source_id"], "interaction:block-search-empty:7:91")
        self.assertEqual(kwargs["logical_message_key"], "private:7:block-search-empty:91")
        self.assertEqual(
            kwargs["result_requirement"],
            TelegramInteractionResultRequirement.NONE,
        )
        self.assertEqual(
            kwargs["anchor_effect"],
            TelegramInteractionAnchorEffect.PRESERVE_CURRENT,
        )

    async def test_queue_mode_serializes_markup_and_requests_persistent_anchor(self):
        message = _message()
        session = SimpleNamespace(commit=AsyncMock())
        markup = SimpleNamespace(
            model_dump=lambda **_kwargs: {
                "keyboard": [[{"text": "منوی اصلی"}]],
                "resize_keyboard": True,
            }
        )
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(adapter, "current_server", return_value="foreign"),
            patch.object(
                adapter,
                "enqueue_private_interaction_once",
                new=AsyncMock(return_value=object()),
            ) as enqueue,
        ):
            await adapter.answer_incoming_message_via_runtime(
                message,
                _user(),
                "منوی اصلی",
                source_key="panel-main",
                reply_markup=markup,
                set_persistent_anchor=True,
                session=session,
            )

        kwargs = enqueue.await_args.kwargs
        self.assertEqual(
            kwargs["result_requirement"],
            TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID,
        )
        self.assertEqual(
            kwargs["anchor_effect"],
            TelegramInteractionAnchorEffect.SET_CURRENT,
        )
        self.assertEqual(kwargs["reply_markup"]["keyboard"][0][0]["text"], "منوی اصلی")

    async def test_queue_mode_rejects_route_mismatch_before_persistence(self):
        message = _message(chat_id=8008)
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(
                adapter,
                "enqueue_private_interaction_once",
                new=AsyncMock(),
            ) as enqueue,
        ):
            with self.assertRaisesRegex(
                adapter.TelegramInteractionMessageRouteError,
                "route_mismatch",
            ):
                await adapter.answer_incoming_message_via_runtime(
                    message,
                    _user(),
                    "پاسخ",
                    source_key="block-search-short",
                    session=session,
                )

        enqueue.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_callback_legacy_mode_preserves_exact_aiogram_call(self):
        callback = _callback()
        markup = object()
        with patch.object(
            adapter,
            "configured_telegram_delivery_runtime",
            return_value=_runtime(TelegramDeliveryRuntimeMode.LEGACY),
        ):
            result = await adapter.answer_callback_message_via_runtime(
                callback,
                _user(),
                "پاسخ",
                source_key="history-excel-empty",
                parse_mode="Markdown",
                reply_markup=markup,
            )

        self.assertEqual(result.message_id, 92)
        callback.message.answer.assert_awaited_once_with(
            "پاسخ",
            parse_mode="Markdown",
            reply_markup=markup,
        )

    async def test_callback_queue_identity_is_hashed_and_update_specific(self):
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(adapter, "current_server", return_value="foreign"),
            patch.object(
                adapter,
                "enqueue_private_interaction_once",
                new=AsyncMock(return_value=object()),
            ) as enqueue,
        ):
            for callback_id in ("callback-raw-secret-a", "callback-raw-secret-b"):
                await adapter.answer_callback_message_via_runtime(
                    _callback(callback_id=callback_id),
                    _user(),
                    "پاسخ",
                    source_key="history-excel-empty",
                    action=TelegramDeliveryAction.TRADE_NONCRITICAL,
                    session=session,
                )

        source_ids = [call.kwargs["source_id"] for call in enqueue.await_args_list]
        logical_keys = [
            call.kwargs["logical_message_key"] for call in enqueue.await_args_list
        ]
        self.assertEqual(len(set(source_ids)), 2)
        self.assertEqual(len(set(logical_keys)), 2)
        self.assertTrue(all(":cb-" in value for value in source_ids))
        self.assertTrue(
            all("callback-raw-secret" not in value for value in source_ids)
        )
        self.assertTrue(
            all(
                call.kwargs["action"] == TelegramDeliveryAction.TRADE_NONCRITICAL
                for call in enqueue.await_args_list
            )
        )
        self.assertEqual(session.commit.await_count, 2)

    async def test_callback_queue_rejects_missing_callback_identity(self):
        callback = _callback(callback_id="")
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(
                adapter,
                "configured_telegram_delivery_runtime",
                return_value=_runtime(TelegramDeliveryRuntimeMode.QUEUE_V1),
            ),
            patch.object(
                adapter,
                "enqueue_private_interaction_once",
                new=AsyncMock(),
            ) as enqueue,
        ):
            with self.assertRaisesRegex(
                adapter.TelegramInteractionMessageRouteError,
                "callback_identity_invalid",
            ):
                await adapter.answer_callback_message_via_runtime(
                    callback,
                    _user(),
                    "پاسخ",
                    source_key="history-excel-empty",
                    session=session,
                )

        enqueue.assert_not_awaited()
        session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
