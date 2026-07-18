import unittest
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import UserAccountStatus
from core.services.telegram_delivery_queue_service import (
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.services.telegram_scheduled_operation_queue_feedback import (
    TelegramScheduledOperationQueueLifecycleFeedback,
)
from core.services.telegram_scheduled_operation_service import (
    SCHEDULED_OPERATION_HANDOFF,
    cancel_telegram_scheduled_operation,
    enqueue_cosmetic_markup_cleanup_once,
    enqueue_noncritical_market_notice_once,
    enqueue_temporary_message_cleanup_once,
    handoff_next_due_telegram_scheduled_operation,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_scheduled_operation_freshness import (
    validate_scheduled_operation_telegram_delivery_freshness,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_scheduled_operation import TelegramScheduledOperation
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


CHANNEL_ID = -1001234567890


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramScheduledOperationBridgePostgresTests(
    unittest.IsolatedAsyncioTestCase
):
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
                    "telegram_scheduled_operations, "
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

    async def _seed_user(self, *, telegram_id=8_500_001):
        async with self.Session() as db:
            user = User(
                account_name=f"scheduled_{telegram_id}",
                mobile_number=f"09{telegram_id:09d}"[-11:],
                full_name="Scheduled User",
                address="test",
                role=UserRole.STANDARD,
                account_status=UserAccountStatus.ACTIVE,
                telegram_id=telegram_id,
                sync_version=1,
                is_deleted=False,
                has_bot_access=True,
            )
            db.add(user)
            await db.commit()
            return int(user.id)

    async def _handoff(self, *, now=None):
        async with self.Session() as db:
            result = await handoff_next_due_telegram_scheduled_operation(
                db,
                current_server="foreign",
                expected_channel_id=CHANNEL_ID,
                now=now or utc_now(),
            )
            await db.commit()
            return result

    async def _claim(self, worker_id="scheduled-bridge"):
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id=worker_id,
                request_timeout_seconds=10,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()
            return job

    async def test_three_sources_handoff_with_accepted_routes_and_priorities(self):
        await self._seed_user()
        due = utc_now() - timedelta(seconds=1)
        async with self.Session() as db:
            market = await enqueue_noncritical_market_notice_once(
                db,
                current_server="foreign",
                channel_id=CHANNEL_ID,
                source_id="market-reminder-1",
                text="یادآوری زمان‌بندی بازار",
                due_at=due,
            )
            temporary = await enqueue_temporary_message_cleanup_once(
                db,
                current_server="foreign",
                chat_id=8_500_001,
                message_id=101,
                source_id="temporary-error-101",
                due_at=due,
                run_id="stage4-run-1",
            )
            cosmetic = await enqueue_cosmetic_markup_cleanup_once(
                db,
                current_server="foreign",
                chat_id=8_500_001,
                message_id=102,
                source_id="suggestion-markup-102",
                due_at=due,
            )
            self.assertTrue(market.created)
            self.assertTrue(temporary.created)
            self.assertTrue(cosmetic.created)
            await db.commit()

        results = [await self._handoff() for _ in range(3)]
        self.assertTrue(
            all(result.disposition == SCHEDULED_OPERATION_HANDOFF for result in results)
        )
        async with self.Session() as db:
            jobs = list(
                (
                    await db.execute(
                        select(TelegramDeliveryJobRecord).order_by(
                            TelegramDeliveryJobRecord.id
                        )
                    )
                ).scalars()
            )
        by_action = {job.action_kind: job for job in jobs}
        self.assertEqual(by_action[TelegramDeliveryAction.NONCRITICAL_MARKET].method, "sendMessage")
        self.assertEqual(by_action[TelegramDeliveryAction.NONCRITICAL_MARKET].priority, 6)
        self.assertEqual(by_action[TelegramDeliveryAction.TEMPORARY_CLEANUP].method, "deleteMessage")
        self.assertEqual(by_action[TelegramDeliveryAction.TEMPORARY_CLEANUP].priority, 7)
        self.assertEqual(by_action[TelegramDeliveryAction.TEMPORARY_CLEANUP].run_id, "stage4-run-1")
        self.assertEqual(by_action[TelegramDeliveryAction.COSMETIC_CLEANUP].method, "editMessageReplyMarkup")
        self.assertEqual(by_action[TelegramDeliveryAction.COSMETIC_CLEANUP].priority, 7)

    async def test_future_due_is_not_handed_off_and_cancelled_source_supersedes(self):
        await self._seed_user()
        due = utc_now() + timedelta(minutes=1)
        async with self.Session() as db:
            result = await enqueue_temporary_message_cleanup_once(
                db,
                current_server="foreign",
                chat_id=8_500_001,
                message_id=201,
                source_id="future-cleanup-201",
                due_at=due,
            )
            dedupe_key = result.operation.dedupe_key
            await db.commit()
        self.assertIsNone(await self._handoff(now=due - timedelta(seconds=1)))
        handed = await self._handoff(now=due)
        self.assertEqual(handed.disposition, SCHEDULED_OPERATION_HANDOFF)
        async with self.Session() as db:
            cancelled = await cancel_telegram_scheduled_operation(
                db,
                current_server="foreign",
                dedupe_key=dedupe_key,
                reason="message_became_protected",
                now=due,
            )
            self.assertTrue(cancelled)
            job = await db.get(TelegramDeliveryJobRecord, handed.job_id)
            decision = await validate_scheduled_operation_telegram_delivery_freshness(
                db,
                job,
                due,
                expected_channel_id=CHANNEL_ID,
            )
            self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
            await db.rollback()

    async def test_recipient_route_change_stops_cleanup_before_gateway(self):
        user_id = await self._seed_user()
        async with self.Session() as db:
            await enqueue_cosmetic_markup_cleanup_once(
                db,
                current_server="foreign",
                chat_id=8_500_001,
                message_id=301,
                source_id="route-change-301",
                due_at=utc_now() - timedelta(seconds=1),
            )
            await db.commit()
        handed = await self._handoff()
        async with self.Session() as db:
            user = await db.get(User, user_id, with_for_update=True)
            user.telegram_id = 8_500_999
            user.sync_version += 1
            await db.commit()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handed.job_id)
            decision = await validate_scheduled_operation_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
                expected_channel_id=CHANNEL_ID,
            )
            self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)

    async def test_delete_success_atomically_finalizes_source(self):
        await self._seed_user()
        async with self.Session() as db:
            result = await enqueue_temporary_message_cleanup_once(
                db,
                current_server="foreign",
                chat_id=8_500_001,
                message_id=401,
                source_id="delete-success-401",
                due_at=utc_now() - timedelta(seconds=1),
            )
            operation_id = int(result.operation.id)
            await db.commit()
        handed = await self._handoff()
        job = await self._claim()
        self.assertEqual(int(job.id), handed.job_id)
        feedback = TelegramScheduledOperationQueueLifecycleFeedback(
            expected_channel_id=CHANNEL_ID
        )
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_scheduled_operation_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
                expected_channel_id=CHANNEL_ID,
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            may_send = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="scheduled-bridge",
                lease_token=int(current.lease_token),
                decision=freshness,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            self.assertTrue(may_send)
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="scheduled-bridge",
                lease_token=int(current.lease_token),
                dispatch_guard=feedback.assert_dispatchable,
                now=utc_now(),
            )
            self.assertTrue(marked)
            await db.commit()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="scheduled-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=True,
                    method="deleteMessage",
                    status_code=200,
                    response_json={"ok": True, "result": True},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
            await db.commit()
        async with self.Session() as db:
            operation = await db.get(TelegramScheduledOperation, operation_id)
            resolved = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(operation.status, "sent")
            self.assertIsNone(operation.queue_job_id)
            self.assertEqual(resolved.state, TelegramDeliveryState.SENT)


if __name__ == "__main__":
    unittest.main()
