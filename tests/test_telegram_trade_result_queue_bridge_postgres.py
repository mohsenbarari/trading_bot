import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import SettlementType, UserAccountStatus
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    apply_telegram_delivery_provider_outcome,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    recover_expired_telegram_delivery_leases,
    resolve_telegram_delivery_result,
)
from core.services.telegram_trade_result_queue_feedback import (
    TradeResultQueueLifecycleFeedback,
)
from core.services.telegram_trade_result_queue_service import (
    TRADE_RESULT_QUEUE_HANDOFF,
    TRADE_RESULT_QUEUE_REQUIRES_RECONCILIATION,
    handoff_next_due_trade_result_receipt,
)
from core.services.trade_delivery_receipt_service import (
    claim_next_receipt_for_delivery,
    trade_completed_receipt_dedupe_key,
)
from core.services.trade_telegram_delivery_service import (
    TELEGRAM_DELIVERY_STATUS_QUEUED_FOR_MAIN_QUEUE,
    deliver_telegram_trade_notification,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_queue_limiter import TelegramDeliveryDispatchAdmission
from core.telegram_delivery_queue_worker import run_telegram_delivery_queue_cycle
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from core.telegram_delivery_trade_freshness import (
    validate_trade_result_telegram_delivery_freshness,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_queue_job_id_from_receipt,
    trade_result_queue_reconciliation_job_id_from_receipt,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.commodity import Commodity
from models.customer_relation import (
    CustomerRelation,
    CustomerRelationStatus,
    CustomerTier,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_APPLIED,
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.trade import Trade, TradeStatus, TradeType
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


class _AllowLimiter:
    async def acquire(self, _job, *, now):
        return TelegramDeliveryDispatchAdmission(allowed=True)

    async def observe(self, _job, _decision, *, now):
        return None

    async def extend_destination_cooldown(self, _job, *, until):
        return None

    async def extend_bot_cooldown(self, _bot_identity, *, until):
        return None

    async def prepare_preflight(self, _bot_identity):
        return True

    async def preflight_gate_open(self, _bot_identity):
        return True


class _FailDeliveryFeedback(TradeResultQueueLifecycleFeedback):
    async def apply_delivery_result(self, _db, _job, _decision, _now):
        raise RuntimeError("synthetic_trade_feedback_failure")


class _FailOnceDeliveryFeedback(TradeResultQueueLifecycleFeedback):
    def __init__(self):
        self.delivery_attempts = 0

    async def apply_delivery_result(self, db, job, decision, now):
        self.delivery_attempts += 1
        if self.delivery_attempts == 1:
            raise RuntimeError("synthetic_transient_trade_feedback_failure")
        return await super().apply_delivery_result(db, job, decision, now)


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramTradeResultQueueBridgePostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "telegram_delivery_jobs, "
                    "trade_delivery_receipts, trades, users, commodities "
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
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

    @staticmethod
    def _user(*, account_name: str, mobile: str, telegram_id: int) -> User:
        return User(
            account_name=account_name,
            mobile_number=mobile,
            full_name=account_name,
            address="test",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            telegram_id=telegram_id,
            sync_version=1,
            is_deleted=False,
            has_bot_access=True,
        )

    async def _seed_receipt(self, *, trade_number: int = 19101):
        committed_at = utc_now() - timedelta(seconds=1)
        async with self.Session() as db:
            recipient = self._user(
                account_name=f"queue_recipient_{trade_number}",
                mobile=f"0912{trade_number:08d}"[-11:],
                telegram_id=9_000_000 + trade_number,
            )
            counterparty = self._user(
                account_name=f"queue_counterparty_{trade_number}",
                mobile=f"0935{trade_number:08d}"[-11:],
                telegram_id=9_100_000 + trade_number,
            )
            commodity = Commodity(name=f"queue_commodity_{trade_number}")
            db.add_all([recipient, counterparty, commodity])
            await db.flush()
            trade = Trade(
                trade_number=trade_number,
                offer_user_id=counterparty.id,
                responder_user_id=recipient.id,
                commodity_id=commodity.id,
                trade_type=TradeType.BUY,
                settlement_type=SettlementType.CASH,
                quantity=1,
                price=75_000_000,
                status=TradeStatus.COMPLETED,
                created_at=committed_at,
            )
            db.add(trade)
            await db.flush()
            receipt = TradeDeliveryReceipt(
                event_type="trade_completed",
                dedupe_key=trade_completed_receipt_dedupe_key(
                    channel=TradeDeliveryChannel.TELEGRAM,
                    trade_number=trade_number,
                    recipient_user_id=recipient.id,
                ),
                trade_id=trade.id,
                trade_number=trade_number,
                recipient_user_id=recipient.id,
                recipient_role="responder",
                channel=TradeDeliveryChannel.TELEGRAM,
                destination_server="foreign",
                status=TradeDeliveryReceiptStatus.PENDING,
                audit_payload={
                    "message": f"نتیجه معامله {trade_number}",
                    "telegram_id_at_audience_build": recipient.telegram_id,
                    "extra_payload": {
                        "trade_number": trade_number,
                        "recipient_user_id": recipient.id,
                        "recipient_role": "responder",
                        "side": "responder",
                    },
                },
                event_created_at=committed_at,
            )
            db.add(receipt)
            await db.commit()
            return int(receipt.id), int(recipient.id)

    async def _handoff(self):
        async with self.Session() as db:
            result = await handoff_next_due_trade_result_receipt(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
            return result

    async def _run_worker(
        self,
        gateway,
        *,
        feedback=None,
        worker_id="trade-bridge",
        limit=1,
    ):
        with patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ), patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            return await run_telegram_delivery_queue_cycle(
                bot_identity="primary",
                limit=limit,
                freshness_validator=validate_trade_result_telegram_delivery_freshness,
                lifecycle_feedback=feedback or TradeResultQueueLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=_AllowLimiter(),
                worker_id=worker_id,
                recover_leases=False,
            )

    async def test_real_producer_in_queue_mode_upserts_then_feeder_hands_off_without_send(self):
        trade_number = 19110
        committed_at = utc_now() - timedelta(seconds=1)
        async with self.Session() as db:
            recipient = self._user(
                account_name="queue_producer_recipient",
                mobile="09120019110",
                telegram_id=9_000_000 + trade_number,
            )
            counterparty = self._user(
                account_name="queue_producer_counterparty",
                mobile="09350019110",
                telegram_id=9_100_000 + trade_number,
            )
            commodity = Commodity(name="queue_producer_commodity")
            db.add_all([recipient, counterparty, commodity])
            await db.flush()
            trade = Trade(
                trade_number=trade_number,
                offer_user_id=counterparty.id,
                responder_user_id=recipient.id,
                commodity_id=commodity.id,
                trade_type=TradeType.BUY,
                settlement_type=SettlementType.CASH,
                quantity=1,
                price=75_000_000,
                status=TradeStatus.COMPLETED,
                created_at=committed_at,
            )
            db.add(trade)
            await db.flush()
            recipient_id = int(recipient.id)
            trade_id = int(trade.id)
            await db.commit()

        gateway = AsyncMock()
        with patch(
            "core.services.trade_telegram_delivery_service."
            "configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            async with self.Session() as db:
                result = await deliver_telegram_trade_notification(
                    db,
                    trade_number=trade_number,
                    recipient_user_id=recipient_id,
                    message=f"نتیجه معامله {trade_number}",
                    current_server="foreign",
                    telegram_id=9_000_000 + trade_number,
                    trade_id=trade_id,
                    recipient_role="responder",
                    event_created_at=committed_at,
                    gateway_send=gateway,
                    commit=False,
                    now=utc_now(),
                )
                await db.commit()

        self.assertEqual(
            result.status,
            TELEGRAM_DELIVERY_STATUS_QUEUED_FOR_MAIN_QUEUE,
        )
        gateway.assert_not_awaited()
        async with self.Session() as db:
            jobs_before = await db.scalar(
                select(func.count()).select_from(TelegramDeliveryJobRecord)
            )
            receipt = (
                await db.execute(
                    select(TradeDeliveryReceipt).where(
                        TradeDeliveryReceipt.trade_number == trade_number,
                        TradeDeliveryReceipt.recipient_user_id == recipient_id,
                        TradeDeliveryReceipt.channel
                        == TradeDeliveryChannel.TELEGRAM,
                    )
                )
            ).scalar_one()
        self.assertEqual(jobs_before, 0)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PENDING)
        self.assertIsNone(receipt.worker_id)

        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, TRADE_RESULT_QUEUE_HANDOFF)
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, int(receipt.id))
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(receipt),
            handoff.job_id,
        )
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)

    async def test_concurrent_handoff_creates_one_job_and_one_local_binding(self):
        receipt_id, _ = await self._seed_receipt()
        start = asyncio.Event()

        async def handoff_once():
            async with self.Session() as db:
                await start.wait()
                result = await handoff_next_due_trade_result_receipt(
                    db,
                    current_server="foreign",
                    now=utc_now(),
                )
                await db.commit()
                return result

        tasks = [asyncio.create_task(handoff_once()) for _ in range(20)]
        start.set()
        results = await asyncio.gather(*tasks)
        handed_off = [result for result in results if result is not None]

        self.assertEqual(len(handed_off), 1)
        self.assertEqual(handed_off[0].disposition, TRADE_RESULT_QUEUE_HANDOFF)
        async with self.Session() as db:
            job_count = await db.scalar(
                select(func.count()).select_from(TelegramDeliveryJobRecord)
            )
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(job_count, 1)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(receipt),
            handed_off[0].job_id,
        )

    async def test_handoff_exception_rolls_back_both_job_and_receipt_binding(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19108)
        with patch(
            "core.services.telegram_trade_result_queue_service."
            "trade_result_queue_receipt_worker_id",
            side_effect=RuntimeError("synthetic_after_enqueue_failure"),
        ):
            async with self.Session() as db:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic_after_enqueue_failure",
                ):
                    await handoff_next_due_trade_result_receipt(
                        db,
                        current_server="foreign",
                        now=utc_now(),
                    )
                await db.rollback()

        async with self.Session() as db:
            job_count = await db.scalar(
                select(func.count()).select_from(TelegramDeliveryJobRecord)
            )
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(job_count, 0)
        self.assertIsNone(receipt.worker_id)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PENDING)

    async def test_orphan_existing_job_is_not_adopted_by_runtime_feeder(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19109)
        first = await self._handoff()
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            receipt.worker_id = None
            receipt.reason = "synthetic_orphan_for_reconciliation"
            await db.commit()
        next_receipt_id, _ = await self._seed_receipt(trade_number=19111)

        async with self.Session() as db:
            isolated = await handoff_next_due_trade_result_receipt(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()

        self.assertEqual(
            isolated.disposition,
            TRADE_RESULT_QUEUE_REQUIRES_RECONCILIATION,
        )
        self.assertFalse(isolated.job_created)
        self.assertEqual(isolated.job_id, first.job_id)
        progressed = await self._handoff()
        self.assertEqual(progressed.disposition, TRADE_RESULT_QUEUE_HANDOFF)
        self.assertTrue(progressed.job_created)

        async with self.Session() as db:
            job_count = await db.scalar(
                select(func.count()).select_from(TelegramDeliveryJobRecord)
            )
            job = await db.get(TelegramDeliveryJobRecord, first.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            next_receipt = await db.get(
                TradeDeliveryReceipt,
                next_receipt_id,
            )
        self.assertEqual(job_count, 2)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING)
        self.assertEqual(
            trade_result_queue_reconciliation_job_id_from_receipt(receipt),
            first.job_id,
        )
        self.assertIsNone(trade_result_queue_job_id_from_receipt(receipt))
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PENDING)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(next_receipt),
            progressed.job_id,
        )

    async def test_success_commits_job_and_receipt_terminal_state_together(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19102)
        handoff = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 880102}},
            )
        )

        report = await self._run_worker(gateway)

        self.assertEqual(report.status_counts, {"sent": 1})
        gateway.assert_awaited_once()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(job.state, TelegramDeliveryState.SENT)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(receipt.telegram_message_id, 880102)
        self.assertIsNone(receipt.worker_id)
        self.assertIsNotNone(receipt.terminal_at)

    async def test_terminal_audit_adds_queue_attempts_to_legacy_baseline(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19116)
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            receipt.attempt_count = 3
            await db.commit()
        handoff = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 880116}},
            )
        )

        await self._run_worker(gateway, worker_id="trade-bridge-attempt-audit")

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(receipt.attempt_count, 4)

    async def test_terminal_receipt_landing_after_freshness_blocks_dispatch_marker(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19109)
        handoff = await self._handoff()
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="dispatch-guard-race",
                request_timeout_seconds=10,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(job.id, handoff.job_id)

        terminal_time = utc_now()
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            receipt.status = TradeDeliveryReceiptStatus.SENT
            receipt.telegram_message_id = 880109
            receipt.sent_at = terminal_time
            receipt.terminal_at = terminal_time
            await db.commit()

        feedback = TradeResultQueueLifecycleFeedback()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                RuntimeError,
                "receipt_not_active",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=handoff.job_id,
                    worker_id="dispatch-guard-race",
                    lease_token=job.lease_token,
                    dispatch_guard=feedback.assert_dispatchable,
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            current_job = await db.get(
                TelegramDeliveryJobRecord,
                handoff.job_id,
            )
        self.assertIsNone(current_job.dispatch_started_at)

    async def test_trade_result_dispatch_marker_cannot_bypass_domain_guard(self):
        await self._seed_receipt(trade_number=19118)
        handoff = await self._handoff()
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="dispatch-guard-required",
                request_timeout_seconds=10,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "trade_result_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=handoff.job_id,
                    worker_id="dispatch-guard-required",
                    lease_token=job.lease_token,
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            current_job = await db.get(
                TelegramDeliveryJobRecord,
                handoff.job_id,
            )
        self.assertIsNone(current_job.dispatch_started_at)

    async def test_trade_result_freshness_and_delivery_feedback_are_mandatory(self):
        await self._seed_receipt(trade_number=19119)
        handoff = await self._handoff()
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="feedback-required",
                request_timeout_seconds=10,
                lease_seconds=30,
                now=utc_now(),
            )
            await db.commit()

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "trade_result_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=handoff.job_id,
                    worker_id="feedback-required",
                    lease_token=job.lease_token,
                    decision=TelegramFreshnessDecision(
                        outcome=TelegramFreshnessOutcome.QUARANTINED,
                        reason="synthetic_quarantine",
                    ),
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=handoff.job_id,
                worker_id="feedback-required",
                lease_token=job.lease_token,
                dispatch_guard=AsyncMock(),
                now=utc_now(),
            )
            self.assertTrue(marked)
            await db.commit()

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "trade_result_delivery_feedback_required",
            ):
                await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=handoff.job_id,
                    worker_id="feedback-required",
                    lease_token=job.lease_token,
                    result=TelegramGatewayResult(
                        ok=True,
                        method="sendMessage",
                        status_code=200,
                        response_json={
                            "ok": True,
                            "result": {"message_id": 880119},
                        },
                    ),
                    retry_after_safety_seconds=0.1,
                    retry_base_seconds=1,
                    retry_max_seconds=300,
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            current_job = await db.get(
                TelegramDeliveryJobRecord,
                handoff.job_id,
            )
        self.assertEqual(current_job.state, TelegramDeliveryState.LEASED)
        self.assertIsNotNone(current_job.dispatch_started_at)
        self.assertIsNone(current_job.telegram_message_id)

    async def test_access_relation_update_waits_until_dispatch_marker_commits(self):
        receipt_id, recipient_id = await self._seed_receipt(trade_number=19117)
        async with self.Session() as db:
            trade = (
                await db.execute(
                    select(Trade).where(Trade.trade_number == 19117)
                )
            ).scalar_one()
            relation = CustomerRelation(
                owner_user_id=int(trade.offer_user_id),
                customer_user_id=recipient_id,
                created_by_user_id=int(trade.offer_user_id),
                invitation_token="queue-access-race-19117",
                management_name="queue-access-race",
                customer_tier=CustomerTier.TIER_1,
                status=CustomerRelationStatus.ACTIVE,
                activated_at=utc_now(),
            )
            db.add(relation)
            await db.commit()

        handoff = await self._handoff()
        guard_read = asyncio.Event()
        release_guard = asyncio.Event()
        update_attempted = asyncio.Event()
        original_validator = validate_trade_result_telegram_delivery_freshness

        async def paused_dispatch_guard_validator(db, job, now):
            decision = await original_validator(db, job, now)
            guard_read.set()
            await release_guard.wait()
            return decision

        async def change_customer_tier():
            async with self.Session() as db:
                relation = (
                    await db.execute(
                        select(CustomerRelation).where(
                            CustomerRelation.customer_user_id == recipient_id
                        )
                    )
                ).scalar_one()
                relation.customer_tier = CustomerTier.TIER_2
                update_attempted.set()
                await db.commit()

        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 880117}},
            )
        )
        with patch(
            "core.telegram_delivery_trade_freshness."
            "validate_trade_result_telegram_delivery_freshness",
            new=paused_dispatch_guard_validator,
        ):
            worker_task = asyncio.create_task(
                self._run_worker(
                    gateway,
                    worker_id="trade-bridge-access-lock",
                )
            )
            await asyncio.wait_for(guard_read.wait(), timeout=5)
            update_task = asyncio.create_task(change_customer_tier())
            await asyncio.wait_for(update_attempted.wait(), timeout=5)
            await asyncio.sleep(0.1)
            self.assertFalse(update_task.done())
            release_guard.set()
            report = await asyncio.wait_for(worker_task, timeout=5)
            await asyncio.wait_for(update_task, timeout=5)

        self.assertEqual(report.status_counts, {"sent": 1})
        gateway.assert_awaited_once()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            relation = (
                await db.execute(
                    select(CustomerRelation).where(
                        CustomerRelation.customer_user_id == recipient_id
                    )
                )
            ).scalar_one()
        self.assertIsNotNone(job.dispatch_started_at)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(relation.customer_tier, CustomerTier.TIER_2)

    async def test_429_keeps_one_queue_retry_owner_and_no_legacy_claim(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19103)
        handoff = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 127},
                },
            )
        )
        before = utc_now()

        report = await self._run_worker(gateway, worker_id="trade-bridge-429")
        after = utc_now()

        self.assertEqual(report.status_counts, {"retry_pending": 1})
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        async with self.Session() as db:
            legacy_claim = await claim_next_receipt_for_delivery(
                db,
                destination_server="foreign",
                worker_id="legacy-must-not-claim",
                channel=TradeDeliveryChannel.TELEGRAM,
                now=utc_now() + timedelta(seconds=200),
            )
            await db.rollback()
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        # retry_after starts at provider-result receipt; ``updated_at`` is the
        # later database linearization timestamp and is intentionally not the
        # deadline anchor. Bracket the receipt between the test timestamps so
        # lock/transaction latency cannot make this assertion flaky.
        self.assertGreaterEqual(
            job.next_retry_at,
            before + timedelta(seconds=127.1),
        )
        self.assertLessEqual(
            job.next_retry_at,
            after + timedelta(seconds=127.1),
        )
        self.assertEqual(job.last_retry_after_seconds, 127.0)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PENDING)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(receipt),
            handoff.job_id,
        )
        self.assertIsNone(legacy_claim)

    async def test_transient_feedback_failure_retries_persistence_not_telegram_429(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19113)
        handoff = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=False,
                method="sendMessage",
                status_code=429,
                response_json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 9},
                },
            )
        )
        feedback = _FailOnceDeliveryFeedback()
        before = utc_now()

        report = await self._run_worker(
            gateway,
            feedback=feedback,
            worker_id="trade-bridge-feedback-retry",
        )
        after = utc_now()

        self.assertEqual(report.status_counts, {"retry_pending": 1})
        self.assertEqual(feedback.delivery_attempts, 2)
        gateway.assert_awaited_once()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertGreaterEqual(
            job.next_retry_at,
            before + timedelta(seconds=9.1),
        )
        self.assertLessEqual(
            job.next_retry_at,
            after + timedelta(seconds=9.1),
        )
        self.assertEqual(job.last_retry_after_seconds, 9.0)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(receipt),
            handoff.job_id,
        )

    async def test_quarantined_poison_job_does_not_rollback_or_block_next_trade(self):
        first_receipt_id, _ = await self._seed_receipt(trade_number=19114)
        first = await self._handoff()
        async with self.Session() as db:
            first_job = await db.get(TelegramDeliveryJobRecord, first.job_id)
            first_job.source_natural_id = "corrupt-source-without-receipt-identity"
            await db.commit()

        second_receipt_id, _ = await self._seed_receipt(trade_number=19115)
        second = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 880115}},
            )
        )

        report = await self._run_worker(
            gateway,
            worker_id="trade-bridge-poison-isolation",
            limit=2,
        )

        self.assertEqual(report.status_counts, {"quarantined": 1, "sent": 1})
        gateway.assert_awaited_once()
        async with self.Session() as db:
            first_job = await db.get(TelegramDeliveryJobRecord, first.job_id)
            second_job = await db.get(TelegramDeliveryJobRecord, second.job_id)
            first_receipt = await db.get(TradeDeliveryReceipt, first_receipt_id)
            second_receipt = await db.get(TradeDeliveryReceipt, second_receipt_id)
        self.assertEqual(first_job.state, TelegramDeliveryState.QUARANTINED)
        self.assertEqual(
            trade_result_queue_job_id_from_receipt(first_receipt),
            first.job_id,
        )
        self.assertEqual(second_job.state, TelegramDeliveryState.SENT)
        self.assertEqual(second_receipt.status, TradeDeliveryReceiptStatus.SENT)

    async def test_feedback_failure_preserves_provider_fact_for_replay(self):
        receipt_id, _ = await self._seed_receipt(trade_number=19104)
        handoff = await self._handoff()
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 880104}},
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "synthetic_trade_feedback_failure",
        ):
            await self._run_worker(
                gateway,
                feedback=_FailDeliveryFeedback(),
                worker_id="trade-bridge-feedback-failure",
            )

        async with self.Session() as db:
            before_recovery = await db.get(
                TelegramDeliveryJobRecord,
                handoff.job_id,
            )
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            self.assertEqual(before_recovery.state, TelegramDeliveryState.LEASED)
            self.assertIsNotNone(before_recovery.dispatch_started_at)
            self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.PENDING)
            self.assertEqual(
                trade_result_queue_job_id_from_receipt(receipt),
                handoff.job_id,
            )
            outcome = (
                await db.execute(
                    select(TelegramDeliveryProviderOutcomeRecord).where(
                        TelegramDeliveryProviderOutcomeRecord.job_id
                        == handoff.job_id
                    )
                )
            ).scalar_one()
            self.assertEqual(outcome.apply_state, TELEGRAM_PROVIDER_OUTCOME_PENDING)
            self.assertEqual(outcome.telegram_message_id, 880104)
            report = await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=before_recovery.lease_until + timedelta(seconds=1),
            )
            await db.commit()
        self.assertEqual(report.job_ids, ())

        async with self.Session() as db:
            decision = await apply_telegram_delivery_provider_outcome(
                db,
                current_server="foreign",
                outcome_id=int(outcome.id),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1.0,
                retry_max_seconds=30.0,
                global_rate_limit_window_seconds=2.0,
                feedback=TradeResultQueueLifecycleFeedback().apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        gateway.assert_awaited_once()
        async with self.Session() as db:
            recovered = await db.get(TelegramDeliveryJobRecord, handoff.job_id)
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            applied = await db.get(
                TelegramDeliveryProviderOutcomeRecord,
                int(outcome.id),
            )
        self.assertEqual(recovered.state, TelegramDeliveryState.SENT)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SENT)
        self.assertEqual(receipt.telegram_message_id, 880104)
        self.assertEqual(applied.apply_state, TELEGRAM_PROVIDER_OUTCOME_APPLIED)

    async def test_relink_supersedes_old_binding_without_new_delivery(self):
        receipt_id, user_id = await self._seed_receipt(trade_number=19105)
        first = await self._handoff()
        async with self.Session() as db:
            user = await db.get(User, user_id)
            user.telegram_id += 1000
            user.sync_version = 2
            await db.commit()
        gateway = AsyncMock()

        report = await self._run_worker(
            gateway,
            worker_id="trade-bridge-reclassify",
        )

        self.assertEqual(report.status_counts, {"superseded": 1})
        gateway.assert_not_awaited()
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
            old_job = await db.get(TelegramDeliveryJobRecord, first.job_id)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)
        self.assertIsNone(receipt.worker_id)
        self.assertEqual(old_job.state, TelegramDeliveryState.SUPERSEDED)

        self.assertIsNone(await self._handoff())
        async with self.Session() as db:
            receipt = await db.get(TradeDeliveryReceipt, receipt_id)
        self.assertEqual(receipt.status, TradeDeliveryReceiptStatus.SKIPPED)

    async def test_private_forbidden_skips_receipt_and_malformed_payload_fails_it(self):
        for trade_number, result, expected_status in (
            (
                19106,
                TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=403,
                    response_json={
                        "ok": False,
                        "error_code": 403,
                        "description": "Forbidden: bot was blocked by the user",
                    },
                ),
                TradeDeliveryReceiptStatus.SKIPPED,
            ),
            (
                19107,
                TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=400,
                    response_json={
                        "ok": False,
                        "error_code": 400,
                        "description": "Bad Request: can't parse entities",
                    },
                ),
                TradeDeliveryReceiptStatus.PERMANENT_FAILED,
            ),
        ):
            with self.subTest(trade_number=trade_number):
                receipt_id, _ = await self._seed_receipt(
                    trade_number=trade_number
                )
                handoff = await self._handoff()
                await self._run_worker(
                    AsyncMock(return_value=result),
                    worker_id=f"trade-bridge-{trade_number}",
                )
                async with self.Session() as db:
                    receipt = await db.get(TradeDeliveryReceipt, receipt_id)
                    job = await db.get(
                        TelegramDeliveryJobRecord,
                        handoff.job_id,
                    )
                self.assertEqual(receipt.status, expected_status)
                self.assertIsNone(receipt.worker_id)
                self.assertIn(
                    job.state,
                    {
                        TelegramDeliveryState.PERMANENT_UNDELIVERABLE,
                        TelegramDeliveryState.TERMINAL_FAILED,
                    },
                )


if __name__ == "__main__":
    unittest.main()
