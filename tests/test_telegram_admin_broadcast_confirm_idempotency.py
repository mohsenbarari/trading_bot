import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import IntegrityError

from bot.handlers import admin_broadcast
from core.enums import UserRole
from core.services import telegram_admin_broadcast_service as service
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastContentKind,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastStatus,
)
from tests.test_bot_admin_broadcast_interaction_queue import (
    _SessionContext,
    _callback,
    _state,
    _user,
)
from tests.test_telegram_admin_broadcast_service import FakeQueueDB


CREATION_KEY = "opaque-broadcast-key-01"


def _confirm_data(**overrides):
    data = {
        "content": "پیام معتبر",
        "audience_type": "all",
        "target_groups": [],
        "selected_user_ids": [],
        "content_kind": TelegramAdminBroadcastContentKind.TEXT.value,
        "creation_key": CREATION_KEY,
        "confirm_completed": False,
    }
    data.update(overrides)
    return data


def _queue_result(*, broadcast_id=41, receipt_count=2):
    return SimpleNamespace(
        broadcast=SimpleNamespace(id=broadcast_id, creation_key=CREATION_KEY),
        receipt_count=receipt_count,
    )


class _LookupDB:
    def __init__(self, existing=None, receipt_count=0):
        self.existing = existing
        self.receipt_count = receipt_count
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(scalar_one_or_none=lambda: self.existing)
        return SimpleNamespace(scalar_one=lambda: self.receipt_count)


class _IntegrityThenReplayDB(FakeQueueDB):
    def __init__(self, existing, receipt_count):
        super().__init__()
        self.existing = existing
        self.receipt_count = receipt_count
        self.rollback = AsyncMock()
        self._flushed = False
        self._replay_stage = 0

    async def execute(self, _statement):
        if not self._flushed:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        if self._replay_stage == 0:
            self._replay_stage = 1
            return SimpleNamespace(scalar_one_or_none=lambda: self.existing)
        return SimpleNamespace(scalar_one=lambda: self.receipt_count)

    async def flush(self):
        self.flush_count += 1
        if not self._flushed:
            self._flushed = True
            raise IntegrityError(
                "INSERT",
                {},
                Exception("ux_telegram_admin_broadcasts_creation_key"),
            )
        await super().flush()


class TelegramAdminBroadcastConfirmIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_replays_existing_creation_key_without_new_receipts(self):
        existing = TelegramAdminBroadcast(
            content="پیام معتبر",
            content_kind=TelegramAdminBroadcastContentKind.TEXT,
            created_by_id=1,
            audience_type=TelegramAdminBroadcastAudienceType.ALL,
            target_groups=[],
            recipient_count=2,
            status=TelegramAdminBroadcastStatus.QUEUED,
            creation_key=CREATION_KEY,
        )
        existing.id = 55
        db = _LookupDB(existing=existing, receipt_count=2)

        result = await service.create_telegram_admin_broadcast(
            db,
            actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            content="پیام معتبر",
            audience_type=TelegramAdminBroadcastAudienceType.ALL,
            creation_key=CREATION_KEY,
        )

        self.assertIs(result.broadcast, existing)
        self.assertEqual(result.receipt_count, 2)

    async def test_integrity_error_after_flush_replays_existing_broadcast(self):
        existing = TelegramAdminBroadcast(
            content="پیام معتبر",
            content_kind=TelegramAdminBroadcastContentKind.TEXT,
            created_by_id=1,
            audience_type=TelegramAdminBroadcastAudienceType.ALL,
            target_groups=[],
            recipient_count=2,
            status=TelegramAdminBroadcastStatus.QUEUED,
            creation_key=CREATION_KEY,
        )
        existing.id = 61
        db = _IntegrityThenReplayDB(existing, receipt_count=2)

        with patch(
            "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
            new=AsyncMock(
                return_value=(
                    service.TelegramAdminBroadcastRecipient(
                        user_id=7, telegram_id=9007, account_name="user7"
                    ),
                )
            ),
        ):
            result = await service.create_telegram_admin_broadcast(
                db,
                actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                content="پیام معتبر",
                audience_type=TelegramAdminBroadcastAudienceType.ALL,
                creation_key=CREATION_KEY,
            )

        self.assertIs(result.broadcast, existing)
        self.assertEqual(result.receipt_count, 2)
        db.rollback.assert_awaited()
        self.assertFalse(
            [obj for obj in db.added if isinstance(obj, TelegramAdminBroadcastReceipt)]
        )

    async def test_zero_recipient_create_is_idempotent_for_text_and_video(self):
        for kind, kwargs in (
            (TelegramAdminBroadcastContentKind.TEXT, {}),
            (
                TelegramAdminBroadcastContentKind.VIDEO,
                {
                    "telegram_media_file_id": "AgAC-central-bot-file",
                    "telegram_media_file_unique_id": "AQADunique01",
                },
            ),
        ):
            with self.subTest(content_kind=kind.value):
                existing = TelegramAdminBroadcast(
                    content="آموزش امکانات بات" if kind is TelegramAdminBroadcastContentKind.VIDEO else "متن",
                    content_kind=kind,
                    created_by_id=1,
                    audience_type=TelegramAdminBroadcastAudienceType.ALL,
                    target_groups=[],
                    recipient_count=0,
                    status=TelegramAdminBroadcastStatus.COMPLETED,
                    creation_key=CREATION_KEY,
                )
                existing.id = 70
                db = _LookupDB(existing=existing, receipt_count=0)
                result = await service.create_telegram_admin_broadcast(
                    db,
                    actor=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    content=existing.content,
                    audience_type=TelegramAdminBroadcastAudienceType.ALL,
                    content_kind=kind,
                    creation_key=CREATION_KEY,
                    **kwargs,
                )
                self.assertEqual(result.receipt_count, 0)
                self.assertEqual(result.broadcast.id, 70)

    async def test_concurrent_confirms_create_once(self):
        callback = _callback("tgb:confirm")
        state = _state(_confirm_data())
        session = SimpleNamespace(commit=AsyncMock())
        create = AsyncMock(return_value=_queue_result())
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(session),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=create,
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ),
        ):
            await asyncio.gather(
                admin_broadcast.confirm_telegram_admin_broadcast(
                    callback, state, _user()
                ),
                admin_broadcast.confirm_telegram_admin_broadcast(
                    callback, state, _user()
                ),
            )
        self.assertEqual(create.await_count, 1)
        self.assertEqual(create.await_args.kwargs["creation_key"], CREATION_KEY)
        state.clear.assert_awaited()

    async def test_repeat_confirm_after_success_and_restart_does_not_duplicate(self):
        queued = _queue_result(broadcast_id=88, receipt_count=3)
        first_state = _state(_confirm_data())
        restart_state = _state(_confirm_data())
        create = AsyncMock(return_value=queued)
        session = SimpleNamespace(commit=AsyncMock())
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(session),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=create,
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ),
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                _callback("tgb:confirm"),
                first_state,
                _user(),
            )
            await admin_broadcast.confirm_telegram_admin_broadcast(
                _callback("tgb:confirm"),
                first_state,
                _user(),
            )
            await admin_broadcast.confirm_telegram_admin_broadcast(
                _callback("tgb:confirm"),
                restart_state,
                _user(),
            )
        self.assertEqual(create.await_count, 2)
        self.assertEqual(
            {call.kwargs["creation_key"] for call in create.await_args_list},
            {CREATION_KEY},
        )

    async def test_failure_before_commit_keeps_retryable_state(self):
        callback = _callback("tgb:confirm")
        state = _state(_confirm_data())
        session = SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("db down")))
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(session),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=AsyncMock(return_value=_queue_result()),
            ),
            patch.object(
                admin_broadcast,
                "_load_broadcast_by_creation_key",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as edit,
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                state,
                _user(),
            )
        self.assertEqual(
            edit.await_args.kwargs["source_key"],
            "admin-broadcast-confirm-error",
        )
        self.assertEqual(
            edit.await_args.args[2],
            "ارسال پیام انجام نشد. دوباره تلاش کنید.",
        )
        state.clear.assert_not_awaited()
        callback.answer.assert_awaited()

    async def test_failure_before_flush_keeps_video_state_retryable(self):
        callback = _callback("tgb:confirm")
        state = _state(
            _confirm_data(
                content="آموزش امکانات بات",
                content_kind=TelegramAdminBroadcastContentKind.VIDEO.value,
            )
        )
        with (
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=AsyncMock(side_effect=RuntimeError("flush failed")),
            ),
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(SimpleNamespace(commit=AsyncMock())),
            ),
            patch.object(
                admin_broadcast,
                "_load_broadcast_by_creation_key",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as edit,
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                state,
                _user(),
            )
        self.assertEqual(
            edit.await_args.args[2],
            "ارسال ویدئو انجام نشد. دوباره تلاش کنید.",
        )
        state.clear.assert_not_awaited()

    async def test_ambiguous_commit_retries_existing_broadcast(self):
        callback = _callback("tgb:confirm")
        state = _state(_confirm_data())
        existing = _queue_result(broadcast_id=99, receipt_count=4)
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(
                    SimpleNamespace(commit=AsyncMock(side_effect=RuntimeError("commit lost")))
                ),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=AsyncMock(return_value=existing),
            ),
            patch.object(
                admin_broadcast,
                "_load_broadcast_by_creation_key",
                new=AsyncMock(return_value=existing),
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(),
            ) as edit,
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                state,
                _user(),
            )
        self.assertEqual(
            edit.await_args.kwargs["source_key"],
            "admin-broadcast-confirm-queued",
        )
        self.assertIn("شناسه: 99", edit.await_args.args[2])
        state.clear.assert_awaited()

    async def test_telegram_reply_failure_after_commit_keeps_state_for_retry(self):
        callback = _callback("tgb:confirm")
        state = _state(_confirm_data())
        with (
            patch.object(
                admin_broadcast,
                "AsyncSessionLocal",
                return_value=_SessionContext(SimpleNamespace(commit=AsyncMock())),
            ),
            patch.object(
                admin_broadcast,
                "create_telegram_admin_broadcast",
                new=AsyncMock(return_value=_queue_result(broadcast_id=12, receipt_count=1)),
            ),
            patch.object(
                admin_broadcast,
                "edit_callback_message_via_runtime",
                new=AsyncMock(side_effect=RuntimeError("telegram edit failed")),
            ),
        ):
            await admin_broadcast.confirm_telegram_admin_broadcast(
                callback,
                state,
                _user(),
            )
        state.clear.assert_not_awaited()
        callback.answer.assert_awaited()
        self.assertNotIn(CREATION_KEY, str(callback.answer.await_args))


if __name__ == "__main__":
    unittest.main()
