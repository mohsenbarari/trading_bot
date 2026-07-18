import asyncio
import unittest
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import UserAccountStatus
from core.services.telegram_admin_broadcast_delivery_service import (
    claim_next_telegram_admin_broadcast_receipt,
)
from core.services.telegram_admin_broadcast_queue_feedback import (
    TelegramAdminBroadcastQueueLifecycleFeedback,
)
from core.services.telegram_admin_broadcast_queue_service import (
    ADMIN_BROADCAST_QUEUE_HANDOFF,
    ADMIN_BROADCAST_QUEUE_REQUIRES_RECONCILIATION,
    handoff_next_due_telegram_admin_broadcast_receipt,
)
from core.services.telegram_admin_broadcast_service import (
    telegram_admin_broadcast_dedupe_key,
)
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    validate_admin_broadcast_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastAudienceType,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramAdminBroadcastQueueBridgePostgresTests(
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
                    "TRUNCATE TABLE telegram_admin_broadcast_receipts, "
                    "telegram_admin_broadcasts, telegram_delivery_jobs, users "
                    "RESTART IDENTITY CASCADE"
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

    @staticmethod
    def _user(
        *,
        account_name: str,
        mobile: str,
        telegram_id: int,
        role: UserRole = UserRole.STANDARD,
    ) -> User:
        return User(
            account_name=account_name,
            mobile_number=mobile,
            full_name=account_name,
            address="test",
            role=role,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=telegram_id,
            sync_version=1,
            is_deleted=False,
            has_bot_access=True,
        )

    async def _seed_broadcast(self, *, recipients: int = 2, suffix: int = 0):
        async with self.Session() as db:
            creator = self._user(
                account_name=f"broadcast_creator_{suffix}",
                mobile=f"0912{suffix:03d}0001",
                telegram_id=8_000_001 + (suffix * 1_000),
                role=UserRole.SUPER_ADMIN,
            )
            recipient_rows = [
                self._user(
                    account_name=f"broadcast_recipient_{suffix}_{index}",
                    mobile=f"0912{suffix:03d}{index + 1:04d}",
                    telegram_id=8_100_000 + (suffix * 1_000) + index,
                )
                for index in range(1, recipients + 1)
            ]
            db.add_all([creator, *recipient_rows])
            await db.flush()
            now = utc_now() - timedelta(seconds=1)
            broadcast = TelegramAdminBroadcast(
                content="پیام مدیریتی صف اصلی",
                created_by_id=int(creator.id),
                audience_type=TelegramAdminBroadcastAudienceType.SELECTED,
                target_groups=[],
                recipient_count=len(recipient_rows),
                status=TelegramAdminBroadcastStatus.QUEUED,
                queued_at=now,
                created_at=now,
            )
            db.add(broadcast)
            await db.flush()
            receipts = []
            for user in recipient_rows:
                receipt = TelegramAdminBroadcastReceipt(
                    broadcast_id=int(broadcast.id),
                    recipient_user_id=int(user.id),
                    telegram_id_at_enqueue=int(user.telegram_id),
                    dedupe_key=telegram_admin_broadcast_dedupe_key(
                        broadcast_id=int(broadcast.id),
                        recipient_user_id=int(user.id),
                    ),
                    status=TelegramAdminBroadcastReceiptStatus.PENDING,
                    attempt_count=0,
                    created_at=now,
                )
                receipts.append(receipt)
            db.add_all(receipts)
            await db.commit()
            return (
                int(broadcast.id),
                tuple(int(receipt.id) for receipt in receipts),
                tuple(int(user.id) for user in recipient_rows),
            )

    async def _handoff(self):
        async with self.Session() as db:
            result = await handoff_next_due_telegram_admin_broadcast_receipt(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
            return result

    async def _claim_job(self, *, worker_id="admin-bridge"):
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id=worker_id,
                request_timeout_seconds=1,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()
            return job

    async def _resolve_success(self, job, *, worker_id="admin-bridge"):
        feedback = TelegramAdminBroadcastQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_admin_broadcast_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            may_send = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id=worker_id,
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
                worker_id=worker_id,
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
                worker_id=worker_id,
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={
                        "ok": True,
                        "result": {"message_id": 7000 + int(job.id)},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
            return decision

    async def test_sequential_slot_releases_only_after_terminal_feedback(self):
        _, receipt_ids, _ = await self._seed_broadcast(recipients=2)
        first = await self._handoff()
        self.assertEqual(first.disposition, ADMIN_BROADCAST_QUEUE_HANDOFF)
        self.assertIsNone(await self._handoff())

        job = await self._claim_job()
        decision = await self._resolve_success(job)
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)

        second = await self._handoff()
        self.assertEqual(second.disposition, ADMIN_BROADCAST_QUEUE_HANDOFF)
        self.assertEqual(second.receipt_id, receipt_ids[1])
        async with self.Session() as db:
            first_receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            second_receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[1])
            self.assertEqual(first_receipt.status, TelegramAdminBroadcastReceiptStatus.SENT)
            self.assertIsNone(first_receipt.queue_job_id)
            self.assertEqual(second_receipt.queue_job_id, second.job_id)

    async def test_last_terminal_feedback_completes_broadcast_aggregate(self):
        broadcast_id, receipt_ids, _ = await self._seed_broadcast(recipients=1)
        await self._handoff()
        job = await self._claim_job()
        await self._resolve_success(job)

        async with self.Session() as db:
            receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            broadcast = await db.get(TelegramAdminBroadcast, broadcast_id)
            self.assertEqual(receipt.status, TelegramAdminBroadcastReceiptStatus.SENT)
            self.assertEqual(receipt.attempt_count, 1)
            self.assertEqual(broadcast.status, TelegramAdminBroadcastStatus.COMPLETED)
            self.assertIsNotNone(broadcast.completed_at)

    async def test_concurrent_handoff_creates_exactly_one_active_binding(self):
        await self._seed_broadcast(recipients=3)

        async def contender():
            return await self._handoff()

        results = await asyncio.gather(contender(), contender(), contender())
        self.assertEqual(sum(result is not None for result in results), 1)
        async with self.Session() as db:
            bound = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramAdminBroadcastReceipt
                        ).where(
                            TelegramAdminBroadcastReceipt.queue_job_id.is_not(None)
                        )
                    )
                ).scalar_one()
            )
            jobs = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
        self.assertEqual((bound, jobs), (1, 1))

    async def test_two_campaign_slots_and_round_robin_skip_active_campaigns(self):
        first_broadcast, first_receipts, _ = await self._seed_broadcast(
            recipients=2,
            suffix=1,
        )
        second_broadcast, second_receipts, _ = await self._seed_broadcast(
            recipients=2,
            suffix=2,
        )
        third_broadcast, third_receipts, _ = await self._seed_broadcast(
            recipients=2,
            suffix=3,
        )

        first = await self._handoff()
        second = await self._handoff()
        self.assertEqual(
            {first.receipt_id, second.receipt_id},
            {
                first_receipts[0],
                second_receipts[0],
            },
        )
        self.assertIsNone(await self._handoff())

        first_job = await self._claim_job()
        await self._resolve_success(first_job)
        third = await self._handoff()
        self.assertEqual(third.receipt_id, third_receipts[0])

        async with self.Session() as db:
            active_campaigns = set(
                (
                    await db.execute(
                        select(TelegramAdminBroadcastReceipt.broadcast_id)
                        .where(
                            TelegramAdminBroadcastReceipt.queue_job_id.is_not(None)
                        )
                        .distinct()
                    )
                ).scalars()
            )
        self.assertEqual(
            active_campaigns,
            {second_broadcast, third_broadcast},
        )

    async def test_concurrent_handoff_caps_distinct_campaigns_at_two(self):
        for suffix in range(1, 5):
            await self._seed_broadcast(recipients=2, suffix=suffix)

        results = await asyncio.gather(
            *(self._handoff() for _ in range(8))
        )
        self.assertEqual(sum(result is not None for result in results), 2)
        async with self.Session() as db:
            bound_campaigns = list(
                (
                    await db.execute(
                        select(TelegramAdminBroadcastReceipt.broadcast_id)
                        .where(
                            TelegramAdminBroadcastReceipt.queue_job_id.is_not(None)
                        )
                        .order_by(TelegramAdminBroadcastReceipt.broadcast_id.asc())
                    )
                ).scalars()
            )
        self.assertEqual(len(bound_campaigns), 2)
        self.assertEqual(len(set(bound_campaigns)), 2)

    async def test_handoff_rollback_leaves_neither_binding_nor_job(self):
        _, receipt_ids, _ = await self._seed_broadcast(recipients=1)
        async with self.Session() as db:
            result = await handoff_next_due_telegram_admin_broadcast_receipt(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            self.assertEqual(result.disposition, ADMIN_BROADCAST_QUEUE_HANDOFF)
            await db.rollback()

        async with self.Session() as db:
            receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            jobs = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
            self.assertIsNone(receipt.queue_job_id)
            self.assertEqual(jobs, 0)
        self.assertEqual((await self._handoff()).disposition, ADMIN_BROADCAST_QUEUE_HANDOFF)

    async def test_429_keeps_binding_and_does_not_release_next_recipient(self):
        _, receipt_ids, _ = await self._seed_broadcast(recipients=2)
        await self._handoff()
        job = await self._claim_job()
        feedback = TelegramAdminBroadcastQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="admin-bridge",
                lease_token=int(current.lease_token),
                dispatch_guard=feedback.assert_dispatchable,
                now=utc_now(),
            )
            await db.commit()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="admin-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 1234},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
            self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertIsNone(await self._handoff())
        async with self.Session() as db:
            first = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            self.assertEqual(first.queue_job_id, int(job.id))
            legacy_claim = await claim_next_telegram_admin_broadcast_receipt(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=utc_now() + timedelta(hours=1),
            )
            self.assertEqual(int(legacy_claim.id), receipt_ids[1])
            # The old worker may see only the next unbound recipient. Runtime
            # ownership prevents it from running in queue mode; it can never
            # reclaim the queue-bound first recipient.

    async def test_relink_reclassifies_and_rebuilds_a_new_job(self):
        _, receipt_ids, user_ids = await self._seed_broadcast(recipients=1)
        first = await self._handoff()
        job = await self._claim_job()
        async with self.Session() as db:
            user = await db.get(User, user_ids[0], with_for_update=True)
            user.telegram_id = int(user.telegram_id) + 999
            user.sync_version = int(user.sync_version) + 1
            await db.commit()

        feedback = TelegramAdminBroadcastQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_admin_broadcast_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.RECLASSIFY)
            dispatched = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="admin-bridge",
                lease_token=int(current.lease_token),
                decision=freshness,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            self.assertFalse(dispatched)
            await db.commit()

        replacement = await self._handoff()
        self.assertEqual(replacement.disposition, ADMIN_BROADCAST_QUEUE_HANDOFF)
        self.assertNotEqual(replacement.job_id, first.job_id)
        async with self.Session() as db:
            old_job = await db.get(TelegramDeliveryJobRecord, int(first.job_id))
            new_job = await db.get(TelegramDeliveryJobRecord, int(replacement.job_id))
            receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            user = await db.get(User, user_ids[0])
            self.assertEqual(old_job.state, TelegramDeliveryState.PENDING_RECONCILE)
            self.assertEqual(new_job.payload["chat_id"], int(user.telegram_id))
            self.assertEqual(receipt.queue_job_id, int(new_job.id))

    async def test_lifecycle_callbacks_are_mandatory_for_broadcast_jobs(self):
        await self._seed_broadcast(recipients=1)
        await self._handoff()
        job = await self._claim_job()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "admin_broadcast_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="admin-bridge",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()

        feedback = TelegramAdminBroadcastQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="admin-bridge",
                lease_token=int(current.lease_token),
                dispatch_guard=feedback.assert_dispatchable,
                now=utc_now(),
            )
            self.assertTrue(marked)
            await db.commit()

        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "admin_broadcast_delivery_feedback_required",
            ):
                await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="admin-bridge",
                    lease_token=int(current.lease_token),
                    result=TelegramGatewayResult(
                        ok=True,
                        method="sendMessage",
                        status_code=200,
                        response_json={
                            "ok": True,
                            "result": {"message_id": 777},
                        },
                    ),
                    retry_after_safety_seconds=0.1,
                    retry_base_seconds=1,
                    retry_max_seconds=300,
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "admin_broadcast_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="admin-bridge",
                    lease_token=int(job.lease_token),
                    decision=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="synthetic",
                    ),
                    now=utc_now(),
                )
            await db.rollback()

    async def test_existing_orphan_job_is_never_adopted(self):
        _, receipt_ids, _ = await self._seed_broadcast(recipients=1)
        first = await self._handoff()
        async with self.Session() as db:
            receipt = await db.get(
                TelegramAdminBroadcastReceipt,
                receipt_ids[0],
                with_for_update=True,
            )
            receipt.queue_job_id = None
            receipt.queue_handed_off_at = None
            receipt.reason = "synthetic_crash_gap"
            await db.commit()

        result = await self._handoff()
        self.assertEqual(
            result.disposition,
            ADMIN_BROADCAST_QUEUE_REQUIRES_RECONCILIATION,
        )
        self.assertEqual(result.job_id, first.job_id)
        async with self.Session() as db:
            receipt = await db.get(TelegramAdminBroadcastReceipt, receipt_ids[0])
            self.assertIsNone(receipt.queue_job_id)
            self.assertTrue(receipt.worker_id.startswith("telegram-delivery-reconcile:"))

    async def test_physical_binding_constraints_and_indexes_exist(self):
        async with self.Session() as db:
            broadcast_indexes = set(
                (
                    await db.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'telegram_admin_broadcasts'"
                        )
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await db.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'telegram_admin_broadcast_receipts'"
                        )
                    )
                ).scalars()
            )
            constraints = set(
                (
                    await db.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = "
                            "'telegram_admin_broadcast_receipts'::regclass"
                        )
                    )
                ).scalars()
            )
        self.assertIn(
            "ix_telegram_admin_broadcasts_queue_fairness",
            broadcast_indexes,
        )
        self.assertIn("ix_telegram_admin_broadcast_receipts_queue_handoff", indexes)
        self.assertIn("ux_telegram_admin_broadcast_receipts_queue_job", indexes)
        self.assertIn("ck_telegram_admin_broadcast_receipts_queue_binding", constraints)
        self.assertIn("fk_telegram_admin_broadcast_receipts_queue_job", constraints)


if __name__ == "__main__":
    unittest.main()
