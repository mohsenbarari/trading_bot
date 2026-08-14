import asyncio
import unittest
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.services.market_transition_service import (
    MARKET_CLOSED_CHANNEL_NOTICE,
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_STATUS_SENT,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    MARKET_NOTICE_TRANSITION_CLOSED,
    MARKET_NOTICE_TRANSITION_OPENED,
    MARKET_OPENED_CHANNEL_NOTICE,
    market_channel_notice_dedupe_key,
)
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.services.telegram_market_notice_queue_feedback import (
    TelegramMarketNoticeQueueLifecycleFeedback,
    TelegramMarketNoticeQueueFeedbackError,
)
from core.services.telegram_market_notice_queue_service import (
    MARKET_NOTICE_QUEUE_HANDOFF,
    MARKET_NOTICE_QUEUE_REQUIRES_RECONCILIATION,
    MARKET_NOTICE_QUEUE_SUPPRESSED,
    handoff_next_due_market_channel_notice,
)
from core.telegram_delivery_market_freshness import (
    validate_market_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.market_runtime_state import MarketRuntimeState
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


CHANNEL_ID = -1001234567890


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramMarketNoticeQueueBridgePostgresTests(
    unittest.IsolatedAsyncioTestCase
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _run_alembic(DATABASE_URLS.owner_sync, "upgrade", "head")

    async def asyncSetUp(self):
        self.engine = create_async_engine(DATABASE_URLS.runtime_async, pool_pre_ping=True)
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
                    "market_channel_notice_receipts, "
                    "telegram_delivery_jobs, market_runtime_state "
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

    async def _seed_receipts(
        self,
        *,
        count: int = 1,
        stale: bool = False,
    ) -> tuple[int, ...]:
        base_time = utc_now() - (
            timedelta(minutes=10) if stale else timedelta(seconds=5)
        )
        async with self.Session() as db:
            rows = []
            for index in range(count):
                transition = (
                    MARKET_NOTICE_TRANSITION_OPENED
                    if index % 2 == 0
                    else MARKET_NOTICE_TRANSITION_CLOSED
                )
                notice_text = (
                    MARKET_OPENED_CHANNEL_NOTICE
                    if transition == MARKET_NOTICE_TRANSITION_OPENED
                    else MARKET_CLOSED_CHANNEL_NOTICE
                )
                transition_at = base_time - timedelta(milliseconds=index)
                rows.append(
                    MarketChannelNoticeReceipt(
                        dedupe_key=market_channel_notice_dedupe_key(
                            transition=transition,
                            transition_at=transition_at,
                            notice_text=notice_text,
                        ),
                        transition=transition,
                        transition_at=transition_at,
                        notice_text=notice_text,
                        channel_id=str(CHANNEL_ID),
                        status=MARKET_NOTICE_STATUS_PENDING,
                        attempt_count=0,
                        source="stage3-postgres",
                    )
                )
            db.add_all(rows)
            await db.flush()
            current = rows[0]
            db.add(
                MarketRuntimeState(
                    id=1,
                    is_open=(
                        current.transition == MARKET_NOTICE_TRANSITION_OPENED
                    ),
                    active_web_notice_visible=True,
                    offers_since_last_open=0,
                    last_transition_at=current.transition_at,
                )
            )
            await db.commit()
            return tuple(int(row.id) for row in rows)

    async def _handoff(self):
        async with self.Session() as db:
            result = await handoff_next_due_market_channel_notice(
                db,
                current_server="foreign",
                expected_channel_id=CHANNEL_ID,
                now=utc_now(),
            )
            await db.commit()
            return result

    async def _claim(self, *, worker_id="market-notice-bridge"):
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

    async def _resolve_success(self, job, *, worker_id="market-notice-bridge"):
        feedback = TelegramMarketNoticeQueueLifecycleFeedback(
            expected_channel_id=CHANNEL_ID
        )
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_market_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
                expected_channel_id=CHANNEL_ID,
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            self.assertTrue(
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id=worker_id,
                    lease_token=int(current.lease_token),
                    decision=freshness,
                    feedback=feedback.apply_freshness,
                    now=utc_now(),
                )
            )
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id=worker_id,
                    lease_token=int(current.lease_token),
                    dispatch_guard=feedback.assert_dispatchable,
                    now=utc_now(),
                )
            )
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
                        "result": {"message_id": 9000 + int(job.id)},
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

    async def test_success_commits_job_and_receipt_terminal_state_together(self):
        receipt_ids = await self._seed_receipts()
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, MARKET_NOTICE_QUEUE_HANDOFF)
        job = await self._claim()
        decision = await self._resolve_success(job)
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)

        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(receipt.status, MARKET_NOTICE_STATUS_SENT)
            self.assertIsNotNone(receipt.telegram_message_id)
            self.assertIsNone(receipt.queue_job_id)
            self.assertEqual(receipt.attempt_count, 1)
            self.assertEqual(current.state, TelegramDeliveryState.SENT)

    async def test_concurrent_handoff_creates_one_job_per_receipt(self):
        await self._seed_receipts(count=3)
        results = await asyncio.gather(*(self._handoff() for _ in range(8)))
        self.assertEqual(sum(result is not None for result in results), 3)
        async with self.Session() as db:
            bound = int(
                (
                    await db.execute(
                        select(text("count(*)"))
                        .select_from(MarketChannelNoticeReceipt)
                        .where(MarketChannelNoticeReceipt.queue_job_id.is_not(None))
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
        self.assertEqual((bound, jobs), (3, 3))

    async def test_handoff_rollback_leaves_neither_binding_nor_job(self):
        receipt_ids = await self._seed_receipts()
        async with self.Session() as db:
            result = await handoff_next_due_market_channel_notice(
                db,
                current_server="foreign",
                expected_channel_id=CHANNEL_ID,
                now=utc_now(),
            )
            self.assertEqual(result.disposition, MARKET_NOTICE_QUEUE_HANDOFF)
            await db.rollback()
        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
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

    async def test_stale_receipt_is_suppressed_without_job(self):
        receipt_ids = await self._seed_receipts(stale=True)
        result = await self._handoff()
        self.assertEqual(result.disposition, MARKET_NOTICE_QUEUE_SUPPRESSED)
        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
            jobs = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(receipt.status, MARKET_NOTICE_STATUS_SUPPRESSED_STALE)
            self.assertIsNone(receipt.queue_job_id)
            self.assertEqual(jobs, 0)

    async def test_429_keeps_binding_for_same_main_queue_job(self):
        receipt_ids = await self._seed_receipts()
        await self._handoff()
        job = await self._claim()
        feedback = TelegramMarketNoticeQueueLifecycleFeedback(
            expected_channel_id=CHANNEL_ID
        )
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(current.lease_token),
                    dispatch_guard=feedback.assert_dispatchable,
                    now=utc_now(),
                )
            )
            await db.commit()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="market-notice-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 1400},
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
        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
            self.assertEqual(receipt.queue_job_id, int(job.id))
            self.assertEqual(receipt.status, MARKET_NOTICE_STATUS_PENDING)

    async def test_lifecycle_callbacks_are_required(self):
        await self._seed_receipts()
        await self._handoff()
        job = await self._claim()
        feedback = TelegramMarketNoticeQueueLifecycleFeedback(
            expected_channel_id=CHANNEL_ID
        )
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "market_notice_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(job.lease_token),
                    decision=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="synthetic",
                    ),
                    now=utc_now(),
                )
            await db.rollback()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "market_notice_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(job.lease_token),
                    dispatch_guard=feedback.assert_dispatchable,
                    now=utc_now(),
                )
            )
            await db.commit()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "market_notice_delivery_feedback_required",
            ):
                await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(job.lease_token),
                    result=TelegramGatewayResult(
                        ok=True,
                        method="sendMessage",
                        status_code=200,
                        response_json={
                            "ok": True,
                            "result": {"message_id": 999},
                        },
                    ),
                    retry_after_safety_seconds=0.1,
                    retry_base_seconds=1,
                    retry_max_seconds=300,
                    now=utc_now(),
                )
            await db.rollback()

    async def test_dispatch_guard_rejects_route_drift_and_preserves_binding(self):
        receipt_ids = await self._seed_receipts()
        await self._handoff()
        job = await self._claim()
        feedback = TelegramMarketNoticeQueueLifecycleFeedback(
            expected_channel_id=CHANNEL_ID
        )
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            current.destination_key = "channel:-1009999999999"
            await db.commit()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramMarketNoticeQueueFeedbackError,
                "market_notice_queue_feedback_route_mismatch",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="market-notice-bridge",
                    lease_token=int(job.lease_token),
                    dispatch_guard=feedback.assert_dispatchable,
                    now=utc_now(),
                )
            await db.rollback()
        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(receipt.queue_job_id, int(job.id))
            self.assertIsNone(current.dispatch_started_at)

    async def test_existing_orphan_job_is_never_adopted(self):
        receipt_ids = await self._seed_receipts()
        first = await self._handoff()
        async with self.Session() as db:
            receipt = await db.get(
                MarketChannelNoticeReceipt,
                receipt_ids[0],
                with_for_update=True,
            )
            receipt.queue_job_id = None
            receipt.queue_handed_off_at = None
            await db.commit()
        result = await self._handoff()
        self.assertEqual(
            result.disposition,
            MARKET_NOTICE_QUEUE_REQUIRES_RECONCILIATION,
        )
        self.assertEqual(result.job_id, first.job_id)
        async with self.Session() as db:
            receipt = await db.get(MarketChannelNoticeReceipt, receipt_ids[0])
            self.assertIsNone(receipt.queue_job_id)
            self.assertIsNotNone(receipt.queue_reconciliation_required_at)

    async def test_physical_binding_constraints_and_indexes_exist(self):
        async with self.Session() as db:
            indexes = set(
                (
                    await db.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'market_channel_notice_receipts'"
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
                            "'market_channel_notice_receipts'::regclass"
                        )
                    )
                ).scalars()
            )
        self.assertIn("ix_market_channel_notice_receipts_queue_handoff", indexes)
        self.assertIn("ux_market_channel_notice_receipts_queue_job", indexes)
        self.assertIn("ck_market_channel_notice_receipts_queue_binding", constraints)
        self.assertIn("ck_market_channel_notice_receipts_queue_owner", constraints)
        self.assertIn("fk_market_channel_notice_receipts_queue_job", constraints)


if __name__ == "__main__":
    unittest.main()
