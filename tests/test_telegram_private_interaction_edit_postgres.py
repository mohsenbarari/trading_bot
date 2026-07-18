import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import UserAccountStatus
from core.services.telegram_delivery_queue_service import (
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_interaction_edit_once,
)
from core.services.telegram_notification_outbox_queue_feedback import (
    TelegramNotificationOutboxQueueLifecycleFeedback,
)
from core.services.telegram_notification_outbox_queue_service import (
    NOTIFICATION_OUTBOX_QUEUE_HANDOFF,
    handoff_next_due_telegram_notification_outbox,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)
from core.telegram_delivery_notification_action_freshness import (
    validate_notification_action_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessOutcome,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramPrivateInteractionEditPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        async with self.Session() as db:
            await db.execute(
                text(
                    "TRUNCATE TABLE telegram_delivery_runtime_gates, "
                    "telegram_notification_outbox, "
                    "telegram_delivery_jobs, users RESTART IDENTITY CASCADE"
                )
            )
            await db.execute(
                text(
                    "ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq "
                    "RESTART WITH 1"
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_and_handoff(self, *, target_message_id: int):
        async with self.Session() as db:
            user = User(
                account_name=f"private_edit_{target_message_id}",
                mobile_number=f"0912777{target_message_id:04d}"[-11:],
                full_name="private edit",
                address="test",
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                telegram_id=8_700_000 + target_message_id,
                sync_version=1,
                is_deleted=False,
                has_bot_access=True,
            )
            db.add(user)
            await db.flush()
            result = await enqueue_private_interaction_edit_once(
                db,
                current_server="foreign",
                recipient=TelegramNotificationRecipient(
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                ),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id=f"private-edit:{target_message_id}",
                logical_message_key=f"private:{user.id}:edit:{target_message_id}",
                target_message_id=target_message_id,
                text="متن ویرایش‌شده",
                user_sync_version=int(user.sync_version),
                parse_mode="Markdown",
                reply_markup={"inline_keyboard": []},
            )
            outbox_id = int(result.notification.outbox.id)
            await db.commit()

        async with self.Session() as db:
            handoff = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)

        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="private-edit-worker",
                request_timeout_seconds=1,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()
        self.assertIsNotNone(job)
        self.assertEqual(job.method, "editMessageText")
        self.assertEqual(job.payload["message_id"], target_message_id)
        return outbox_id, int(job.id)

    async def _dispatch_and_resolve(self, *, job_id: int, result: TelegramGatewayResult):
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, job_id)
            freshness = await validate_notification_action_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            may_send = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=job_id,
                worker_id="private-edit-worker",
                lease_token=int(job.lease_token),
                decision=freshness,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            self.assertTrue(may_send)
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=job_id,
                worker_id="private-edit-worker",
                lease_token=int(job.lease_token),
                dispatch_guard=feedback.assert_dispatchable,
                now=utc_now(),
            )
            self.assertTrue(marked)
            await db.commit()

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, job_id)
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=job_id,
                worker_id="private-edit-worker",
                lease_token=int(job.lease_token),
                result=result,
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
            return decision

    async def test_known_target_edit_handoff_and_success_are_atomic(self):
        target_message_id = 441
        outbox_id, job_id = await self._seed_and_handoff(
            target_message_id=target_message_id
        )
        decision = await self._dispatch_and_resolve(
            job_id=job_id,
            result=TelegramGatewayResult(
                ok=True,
                method="editMessageText",
                status_code=200,
                response_json={
                    "ok": True,
                    "result": {"message_id": target_message_id},
                },
            ),
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            job = await db.get(TelegramDeliveryJobRecord, job_id)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(outbox.telegram_message_id, target_message_id)
        self.assertEqual(job.state, TelegramDeliveryState.SENT)

    async def test_not_modified_is_terminal_success_with_known_target_evidence(self):
        target_message_id = 442
        outbox_id, job_id = await self._seed_and_handoff(
            target_message_id=target_message_id
        )
        decision = await self._dispatch_and_resolve(
            job_id=job_id,
            result=TelegramGatewayResult(
                ok=False,
                method="editMessageText",
                status_code=400,
                response_json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: message is not modified",
                },
            ),
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT_NOOP)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            job = await db.get(TelegramDeliveryJobRecord, job_id)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(outbox.telegram_message_id, target_message_id)
        self.assertEqual(job.state, TelegramDeliveryState.SENT_NOOP)


if __name__ == "__main__":
    unittest.main()
