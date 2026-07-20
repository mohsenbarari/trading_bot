import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import SettlementType, UserAccountStatus
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.services.telegram_notification_outbox_queue_feedback import (
    TelegramNotificationOutboxQueueLifecycleFeedback,
)
from core.services.telegram_channel_membership_queue_feedback import (
    TelegramChannelMembershipQueueLifecycleFeedback,
)
from core.services.telegram_notification_outbox_queue_service import (
    NOTIFICATION_OUTBOX_QUEUE_HANDOFF,
    NOTIFICATION_OUTBOX_QUEUE_REQUIRES_RECONCILIATION,
    handoff_next_due_telegram_notification_outbox,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
    TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
    TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH,
    TelegramNotificationRecipient,
    enqueue_account_deletion_telegram_notification_once,
    enqueue_account_restriction_telegram_notification_once,
    claim_next_telegram_notification_outbox,
    enqueue_account_status_telegram_notification_once,
    enqueue_delayed_restriction_telegram_notification_once,
    enqueue_offer_repeat_response_notification,
    enqueue_offer_success_preview_notification_once,
    enqueue_telegram_action_notification_once,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_notification_action_freshness import (
    validate_notification_action_telegram_delivery_freshness,
)
from core.telegram_delivery_channel_membership_freshness import (
    validate_channel_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_offer_success_contract import (
    OFFER_SUCCESS_COPY_LEGACY,
)
from core.telegram_delivery_offer_success_freshness import (
    validate_offer_success_telegram_delivery_freshness,
)
from core.telegram_delivery_new_user_membership_freshness import (
    validate_new_user_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_repeat_offer_freshness import (
    validate_repeat_offer_response_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_offer_channel_service import build_offer_channel_message
from core.utils import utc_now
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_channel_membership_saga import TelegramChannelMembershipSaga
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic

TEST_CHANNEL_ID = -1001234567890


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramNotificationOutboxQueueBridgePostgresTests(
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
                    "telegram_notification_outbox, "
                    "telegram_delivery_jobs, commodities, users "
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

    async def _seed_outbox(self, *, recipients: int = 2, include_unknown=False):
        async with self.Session() as db:
            subject = self._user(
                account_name="new_project_user",
                mobile="09121110001",
                telegram_id=8_000_001,
            )
            recipient_rows = [
                self._user(
                    account_name=f"membership_recipient_{index}",
                    mobile=f"0912111{index + 1:04d}",
                    telegram_id=8_100_000 + index,
                )
                for index in range(1, recipients + 1)
            ]
            db.add_all([subject, *recipient_rows])
            await db.flush()
            now = utc_now() - timedelta(seconds=1)
            rows = []
            if include_unknown:
                rows.append(
                    TelegramNotificationOutbox(
                        dedupe_key="telegram-notification:unknown:1:1",
                        source_type="unknown",
                        source_id="1",
                        recipient_user_id=int(recipient_rows[0].id),
                        telegram_id_at_enqueue=int(recipient_rows[0].telegram_id),
                        text="source قرارداد ندارد",
                        parse_mode=None,
                        status=TelegramNotificationOutboxStatus.PENDING,
                        attempt_count=0,
                        extra_payload={},
                        created_at=now - timedelta(seconds=1),
                    )
                )
            for user in recipient_rows:
                rows.append(
                    TelegramNotificationOutbox(
                        dedupe_key=telegram_notification_dedupe_key(
                            source_type=TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
                            source_id=int(subject.id),
                            recipient_user_id=int(user.id),
                        ),
                        source_type=TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
                        source_id=str(subject.id),
                        recipient_user_id=int(user.id),
                        telegram_id_at_enqueue=int(user.telegram_id),
                        text="کاربر جدید به لیست همکاران اضافه شدند.",
                        parse_mode=None,
                        status=TelegramNotificationOutboxStatus.PENDING,
                        attempt_count=0,
                        extra_payload={"exclude_customers": True},
                        created_at=now,
                    )
                )
            db.add_all(rows)
            await db.commit()
            known = [
                row
                for row in rows
                if row.source_type
                == TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED
            ]
            return (
                tuple(int(row.id) for row in known),
                tuple(int(user.id) for user in recipient_rows),
            )

    async def _handoff(self):
        async with self.Session() as db:
            result = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=TEST_CHANNEL_ID,
                now=utc_now(),
            )
            await db.commit()
            return result

    async def _seed_repeat_outbox(self):
        async with self.Session() as db:
            user = self._user(
                account_name="repeat_offer_recipient",
                mobile="09121119991",
                telegram_id=8_199_991,
            )
            db.add(user)
            await db.flush()
            source_id = "repeat-success:bot-repeat:postgres-intent-1"
            outbox = TelegramNotificationOutbox(
                dedupe_key=telegram_notification_dedupe_key(
                    source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
                    source_id=source_id,
                    recipient_user_id=int(user.id),
                ),
                source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
                source_id=source_id,
                recipient_user_id=int(user.id),
                telegram_id_at_enqueue=int(user.telegram_id),
                text=TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
                parse_mode=None,
                status=TelegramNotificationOutboxStatus.PENDING,
                attempt_count=0,
                extra_payload={
                    "response_kind": (
                        TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH
                    )
                },
                created_at=utc_now() - timedelta(seconds=1),
            )
            db.add(outbox)
            await db.commit()
            return int(outbox.id), int(user.id)

    async def _handoff_repeat(self):
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=None),
        ):
            return await self._handoff()

    async def _seed_action_outbox(
        self,
        *,
        action=TelegramDeliveryAction.TRADE_ALTERNATIVE,
        account_status=UserAccountStatus.ACTIVE,
        messenger_blocked=False,
        current_account_status=None,
        expected_user_sync_version=None,
    ):
        async with self.Session() as db:
            user = self._user(
                account_name=f"action_recipient_{action.value}",
                mobile=f"0912999{len(action.value):04d}",
                telegram_id=8_299_000 + len(action.value),
            )
            user.account_status = current_account_status or account_status
            if messenger_blocked:
                user.messenger_blocked_at = utc_now().replace(tzinfo=None)
            db.add(user)
            await db.flush()
            recipient = TelegramNotificationRecipient(
                user_id=int(user.id),
                telegram_id=int(user.telegram_id),
            )
            if action in {
                TelegramDeliveryAction.ACCOUNT_STATUS,
                TelegramDeliveryAction.TIMED_SECURITY,
            }:
                result = await enqueue_account_status_telegram_notification_once(
                    db,
                    recipient=recipient,
                    source_id=f"account-state:{user.id}:{account_status.value}:{int(messenger_blocked)}",
                    text="*اعلان وضعیت حساب*",
                    account_status=account_status,
                    messenger_blocked=messenger_blocked,
                    user_sync_version=(
                        int(expected_user_sync_version)
                        if expected_user_sync_version is not None
                        else int(user.sync_version)
                    ),
                    action=action,
                )
            else:
                result = await enqueue_telegram_action_notification_once(
                    db,
                    recipient=recipient,
                    action=action,
                    source_id=f"action:{action.value}:{user.id}",
                    text="پیشنهاد جایگزین",
                    user_sync_version=(
                        int(expected_user_sync_version)
                        if expected_user_sync_version is not None
                        else int(user.sync_version)
                    ),
                    parse_mode=None,
                )
            await db.commit()
            return int(result.outbox.id), int(user.id)

    async def _seed_offer_success_outbox(self):
        async with self.Session() as db:
            user = self._user(
                account_name="offer_success_recipient",
                mobile="09128880001",
                telegram_id=8_388_001,
            )
            commodity = Commodity(name="offer-success-commodity")
            db.add_all([user, commodity])
            await db.flush()
            offer = Offer(
                offer_public_id="ofr_offer_success_pg",
                home_server="foreign",
                user_id=int(user.id),
                offer_type=OfferType.BUY,
                settlement_type=SettlementType.CASH,
                commodity_id=int(commodity.id),
                commodity=commodity,
                quantity=12,
                remaining_quantity=12,
                price=72_500_000,
                is_wholesale=True,
                status=OfferStatus.ACTIVE,
            )
            db.add(offer)
            await db.flush()
            result = await enqueue_offer_success_preview_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                ),
                offer_public_id=str(offer.offer_public_id),
                offer_version=int(offer.version_id),
                preview_message_id=881,
                success_copy=OFFER_SUCCESS_COPY_LEGACY,
                offer_text=build_offer_channel_message(offer),
                user_sync_version=int(user.sync_version),
            )
            await db.commit()
            return int(result.outbox.id), int(user.id), int(offer.id)

    async def _claim_job(self, *, worker_id="membership-bridge"):
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

    async def _resolve_success(self, job, *, worker_id="membership-bridge"):
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = (
                await validate_new_user_membership_telegram_delivery_freshness(
                    db,
                    current,
                    utc_now(),
                )
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
                        "result": {"message_id": 8000 + int(job.id)},
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

    async def test_success_commits_job_and_outbox_terminal_state_together(self):
        outbox_ids, _ = await self._seed_outbox(recipients=1)
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        job = await self._claim_job()
        decision = await self._resolve_success(job)
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)

        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
            self.assertIsNotNone(outbox.telegram_message_id)
            self.assertIsNone(outbox.queue_job_id)
            self.assertEqual(current.state, TelegramDeliveryState.SENT)

    async def test_repeat_response_success_uses_same_atomic_lifecycle(self):
        outbox_id, _ = await self._seed_repeat_outbox()
        handoff = await self._handoff_repeat()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        job = await self._claim_job(worker_id="repeat-bridge")
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()

        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=None),
        ):
            async with self.Session() as db:
                current = await db.get(TelegramDeliveryJobRecord, int(job.id))
                freshness = (
                    await validate_repeat_offer_response_telegram_delivery_freshness(
                        db,
                        current,
                        utc_now(),
                    )
                )
                self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
                marked = await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="repeat-bridge",
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
                worker_id="repeat-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={"ok": True, "result": {"message_id": 911}},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
            self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)

        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
            self.assertEqual(outbox.telegram_message_id, 911)
            self.assertIsNone(outbox.queue_job_id)
            self.assertEqual(current.state, TelegramDeliveryState.SENT)

    async def test_repeat_response_source_enqueue_is_idempotent(self):
        async with self.Session() as db:
            user = self._user(
                account_name="repeat_enqueue_recipient",
                mobile="09121119992",
                telegram_id=8_199_992,
            )
            db.add(user)
            await db.flush()
            recipient = TelegramNotificationRecipient(
                user_id=int(user.id),
                telegram_id=int(user.telegram_id),
            )
            first = await enqueue_offer_repeat_response_notification(
                db,
                recipient=recipient,
                source_id="expiry:ofr_repeat_idempotent_1",
                response_kind=TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH,
                text=TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
            )
            second = await enqueue_offer_repeat_response_notification(
                db,
                recipient=recipient,
                source_id="expiry:ofr_repeat_idempotent_1",
                response_kind=TELEGRAM_OFFER_REPEAT_RESPONSE_KIND_MENU_REFRESH,
                text=TELEGRAM_OFFER_REPEAT_MENU_REFRESH_TEXT,
            )
            await db.commit()

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.outbox.id, second.outbox.id)
        async with self.Session() as db:
            legacy_claim = await claim_next_telegram_notification_outbox(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=utc_now() + timedelta(hours=1),
            )
            self.assertIsNone(legacy_claim)

    async def test_concurrent_handoff_creates_one_job_per_outbox(self):
        await self._seed_outbox(recipients=3)
        results = await asyncio.gather(*(self._handoff() for _ in range(8)))
        self.assertEqual(sum(result is not None for result in results), 3)
        async with self.Session() as db:
            bound = int(
                (
                    await db.execute(
                        select(text("count(*)"))
                        .select_from(TelegramNotificationOutbox)
                        .where(TelegramNotificationOutbox.queue_job_id.is_not(None))
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

    async def test_unknown_source_is_not_handed_off_from_generic_payload(self):
        outbox_ids, _ = await self._seed_outbox(
            recipients=1,
            include_unknown=True,
        )
        result = await self._handoff()
        self.assertEqual(result.outbox_id, outbox_ids[0])
        async with self.Session() as db:
            unknown = (
                await db.execute(
                    select(TelegramNotificationOutbox).where(
                        TelegramNotificationOutbox.source_type == "unknown"
                    )
                )
            ).scalar_one()
            self.assertIsNone(unknown.queue_job_id)
            self.assertEqual(unknown.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_handoff_rollback_leaves_neither_binding_nor_job(self):
        outbox_ids, _ = await self._seed_outbox(recipients=1)
        async with self.Session() as db:
            result = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=TEST_CHANNEL_ID,
                now=utc_now(),
            )
            self.assertEqual(result.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
            await db.rollback()
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            jobs = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
            self.assertIsNone(outbox.queue_job_id)
            self.assertEqual(jobs, 0)

    async def test_429_keeps_binding_for_same_main_queue_job(self):
        outbox_ids, _ = await self._seed_outbox(recipients=1)
        await self._handoff()
        job = await self._claim_job()
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="membership-bridge",
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
                worker_id="membership-bridge",
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
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            self.assertEqual(outbox.queue_job_id, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_repeat_response_429_keeps_same_bound_job(self):
        outbox_id, _ = await self._seed_repeat_outbox()
        await self._handoff_repeat()
        job = await self._claim_job(worker_id="repeat-bridge")
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=None),
        ):
            async with self.Session() as db:
                current = await db.get(TelegramDeliveryJobRecord, int(job.id))
                marked = await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="repeat-bridge",
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
                worker_id="repeat-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 120},
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
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            self.assertEqual(outbox.queue_job_id, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_relink_suppresses_membership_notice_without_new_job(self):
        outbox_ids, user_ids = await self._seed_outbox(recipients=1)
        first = await self._handoff()
        job = await self._claim_job()
        async with self.Session() as db:
            user = await db.get(User, user_ids[0], with_for_update=True)
            user.telegram_id = int(user.telegram_id) + 999
            user.sync_version = int(user.sync_version) + 1
            await db.commit()
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = (
                await validate_new_user_membership_telegram_delivery_freshness(
                    db,
                    current,
                    utc_now(),
                )
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SUPERSEDED)
            dispatched = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="membership-bridge",
                lease_token=int(current.lease_token),
                decision=freshness,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            self.assertFalse(dispatched)
            await db.commit()
        replacement = await self._handoff()
        self.assertIsNone(replacement)
        async with self.Session() as db:
            old = await db.get(TelegramDeliveryJobRecord, int(first.job_id))
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            self.assertEqual(old.state, TelegramDeliveryState.SUPERSEDED)
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
            self.assertIsNone(outbox.queue_job_id)

    async def test_repeat_response_relink_suppresses_without_distinct_job(self):
        outbox_id, user_id = await self._seed_repeat_outbox()
        first = await self._handoff_repeat()
        job = await self._claim_job(worker_id="repeat-bridge")
        async with self.Session() as db:
            user = await db.get(User, user_id, with_for_update=True)
            user.telegram_id = int(user.telegram_id) + 999
            user.sync_version = int(user.sync_version) + 1
            await db.commit()

        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=None),
        ):
            async with self.Session() as db:
                current = await db.get(TelegramDeliveryJobRecord, int(job.id))
                freshness = (
                    await validate_repeat_offer_response_telegram_delivery_freshness(
                        db,
                        current,
                        utc_now(),
                    )
                )
                self.assertEqual(
                    freshness.outcome,
                    TelegramFreshnessOutcome.SUPERSEDED,
                )
                dispatched = await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="repeat-bridge",
                    lease_token=int(current.lease_token),
                    decision=freshness,
                    feedback=feedback.apply_freshness,
                    now=utc_now(),
                )
                self.assertFalse(dispatched)
                await db.commit()

        replacement = await self._handoff_repeat()
        self.assertIsNone(replacement)
        async with self.Session() as db:
            old = await db.get(TelegramDeliveryJobRecord, int(first.job_id))
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            self.assertEqual(old.state, TelegramDeliveryState.SUPERSEDED)
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
            self.assertIsNone(outbox.queue_job_id)

    async def test_lifecycle_callbacks_are_required(self):
        await self._seed_outbox(recipients=1)
        await self._handoff()
        job = await self._claim_job()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "new_user_membership_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="membership-bridge",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()

        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="membership-bridge",
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
                "new_user_membership_delivery_feedback_required",
            ):
                await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="membership-bridge",
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
                "new_user_membership_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="membership-bridge",
                    lease_token=int(job.lease_token),
                    decision=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="synthetic",
                    ),
                    now=utc_now(),
                )
            await db.rollback()

    async def test_repeat_response_lifecycle_callbacks_are_required(self):
        await self._seed_repeat_outbox()
        await self._handoff_repeat()
        job = await self._claim_job(worker_id="repeat-bridge")
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "repeat_offer_response_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="repeat-bridge",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "repeat_offer_response_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="repeat-bridge",
                    lease_token=int(job.lease_token),
                    decision=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="synthetic",
                    ),
                    now=utc_now(),
                )
            await db.rollback()

        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        with patch(
            "core.telegram_delivery_repeat_offer_freshness."
            "load_latest_bot_repeat_offer_candidate",
            new=AsyncMock(return_value=None),
        ):
            async with self.Session() as db:
                current = await db.get(TelegramDeliveryJobRecord, int(job.id))
                marked = await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="repeat-bridge",
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
                "repeat_offer_response_delivery_feedback_required",
            ):
                await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=int(current.id),
                    worker_id="repeat-bridge",
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

    async def test_existing_orphan_job_is_never_adopted(self):
        outbox_ids, _ = await self._seed_outbox(recipients=1)
        first = await self._handoff()
        async with self.Session() as db:
            outbox = await db.get(
                TelegramNotificationOutbox,
                outbox_ids[0],
                with_for_update=True,
            )
            outbox.queue_job_id = None
            outbox.queue_handed_off_at = None
            outbox.reason = "synthetic_crash_gap"
            await db.commit()
        result = await self._handoff()
        self.assertEqual(
            result.disposition,
            NOTIFICATION_OUTBOX_QUEUE_REQUIRES_RECONCILIATION,
        )
        self.assertEqual(result.job_id, first.job_id)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            self.assertIsNone(outbox.queue_job_id)
            self.assertTrue(outbox.worker_id.startswith("telegram-delivery-reconcile:"))
            legacy = await claim_next_telegram_notification_outbox(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=utc_now() + timedelta(hours=1),
            )
            self.assertIsNone(legacy)

    async def test_action_notification_handoff_and_success_are_atomic(self):
        outbox_id, _ = await self._seed_action_outbox()
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        job = await self._claim_job(worker_id="action-bridge")
        self.assertEqual(
            job.action_kind,
            TelegramDeliveryAction.TRADE_ALTERNATIVE,
        )
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = (
                await validate_notification_action_telegram_delivery_freshness(
                    db,
                    current,
                    utc_now(),
                )
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="action-bridge",
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
                worker_id="action-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={"ok": True, "result": {"message_id": 9901}},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
            self.assertEqual(outbox.telegram_message_id, 9901)
            self.assertIsNone(outbox.queue_job_id)
            self.assertEqual(current.state, TelegramDeliveryState.SENT)

    async def test_action_notification_requires_lifecycle_callbacks(self):
        await self._seed_action_outbox(
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
        )
        await self._handoff()
        job = await self._claim_job(worker_id="action-callback-guard")
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "notification_action_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="action-callback-guard",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "notification_action_freshness_feedback_required",
            ):
                await apply_telegram_delivery_freshness_result(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="action-callback-guard",
                    lease_token=int(job.lease_token),
                    decision=TelegramFreshnessDecision(
                        TelegramFreshnessOutcome.SUPERSEDED,
                        reason="synthetic",
                    ),
                    now=utc_now(),
                )
            await db.rollback()

    async def test_stale_account_state_is_skipped_before_job_creation(self):
        outbox_id, user_id = await self._seed_action_outbox(
            action=TelegramDeliveryAction.ACCOUNT_STATUS,
            account_status=UserAccountStatus.ACTIVE,
        )
        async with self.Session() as db:
            user = await db.get(User, user_id, with_for_update=True)
            user.account_status = UserAccountStatus.INACTIVE
            user.sync_version = int(user.sync_version) + 1
            await db.commit()
        result = await self._handoff()
        self.assertEqual(result.disposition, "skipped")
        self.assertEqual(result.reason, "notification_action_source_state_changed")
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            jobs = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
            self.assertEqual(jobs, 0)

    async def test_stale_active_restriction_is_skipped_before_job_creation(self):
        async with self.Session() as db:
            user = self._user(
                account_name="active_restriction_recipient",
                mobile="09128887731",
                telegram_id=8_477_731,
            )
            user.trading_restricted_until = (
                utc_now() + timedelta(hours=2)
            ).replace(tzinfo=None)
            db.add(user)
            await db.flush()
            result = await enqueue_account_restriction_telegram_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                ),
                source_id=f"restriction-block:{user.id}:1",
                text="حساب موقتاً محدود شد",
                user=user,
                restriction_kind="block",
                user_sync_version=1,
            )
            outbox_id = int(result.outbox.id)
            user.trading_restricted_until = (
                utc_now() + timedelta(hours=3)
            ).replace(tzinfo=None)
            user.sync_version = 2
            await db.commit()

        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, "skipped")
        self.assertEqual(
            handoff.reason,
            "notification_action_source_state_changed",
        )
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            self.assertEqual(
                outbox.status,
                TelegramNotificationOutboxStatus.SKIPPED,
            )

    async def test_deleted_account_notice_uses_predelete_route(self):
        async with self.Session() as db:
            user = self._user(
                account_name="deleted_notice_recipient",
                mobile="09128887732",
                telegram_id=8_477_732,
            )
            db.add(user)
            await db.flush()
            user_id = int(user.id)
            telegram_id = int(user.telegram_id)
            user.is_deleted = True
            user.deleted_at = utc_now().replace(tzinfo=None)
            user.telegram_id = None
            user.sync_version = 2
            result = await enqueue_account_deletion_telegram_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=user_id,
                    telegram_id=telegram_id,
                ),
                source_id=f"account-deleted:{user_id}:2",
                text="حساب از پروژه حذف شد",
                user=user,
                user_sync_version=2,
            )
            outbox_id = int(result.outbox.id)
            await db.commit()

        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, int(handoff.job_id))
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            decision = await validate_notification_action_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
            )

        self.assertEqual(job.action_kind, TelegramDeliveryAction.ACCOUNT_STATUS)
        self.assertEqual(job.payload["chat_id"], telegram_id)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

        async with self.Session() as db:
            db.add(
                self._user(
                    account_name="deleted_notice_reassigned_after_handoff",
                    mobile="09128887735",
                    telegram_id=telegram_id,
                )
            )
            await db.commit()
            job = await db.get(
                TelegramDeliveryJobRecord,
                int(handoff.job_id),
            )
            reassigned_decision = (
                await validate_notification_action_telegram_delivery_freshness(
                    db,
                    job,
                    utc_now(),
                )
            )

        self.assertEqual(
            reassigned_decision.outcome,
            TelegramFreshnessOutcome.SUPERSEDED,
        )
        self.assertEqual(
            reassigned_decision.reason,
            "notification_action_freshness_deleted_route_reassigned",
        )

    async def test_deleted_account_materializes_ordered_ban_unban_saga(self):
        channel_id = -1001234567890
        async with self.Session() as db:
            user = self._user(
                account_name="deleted_membership_saga",
                mobile="09128887742",
                telegram_id=8_477_742,
            )
            db.add(user)
            await db.flush()
            user_id = int(user.id)
            telegram_id = int(user.telegram_id)
            user.is_deleted = True
            user.deleted_at = utc_now().replace(tzinfo=None)
            user.telegram_id = None
            user.sync_version = 2
            result = await enqueue_account_deletion_telegram_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=user_id,
                    telegram_id=telegram_id,
                ),
                source_id=f"account-deleted:{user_id}:2",
                text="حساب از پروژه حذف شد",
                user=user,
                user_sync_version=2,
            )
            outbox_id = int(result.outbox.id)
            await db.commit()

        async with self.Session() as db:
            handoff = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=channel_id,
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)

        async with self.Session() as db:
            saga = (
                await db.execute(
                    select(TelegramChannelMembershipSaga).where(
                        TelegramChannelMembershipSaga.source_outbox_id == outbox_id
                    )
                )
            ).scalar_one()
            ban = await db.get(TelegramDeliveryJobRecord, int(saga.ban_job_id))
            unban = await db.get(TelegramDeliveryJobRecord, int(saga.unban_job_id))
            ban_freshness = await validate_channel_membership_telegram_delivery_freshness(
                db,
                ban,
                utc_now(),
                expected_channel_id=channel_id,
            )
            unban_wait = await validate_channel_membership_telegram_delivery_freshness(
                db,
                unban,
                utc_now(),
                expected_channel_id=channel_id,
            )
            self.assertEqual(ban.method, "banChatMember")
            self.assertEqual(ban.payload["user_id"], telegram_id)
            self.assertEqual(unban.method, "unbanChatMember")
            self.assertEqual(ban_freshness.outcome, TelegramFreshnessOutcome.SEND)
            self.assertEqual(
                unban_wait.outcome,
                TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            )

            feedback = TelegramChannelMembershipQueueLifecycleFeedback(
                expected_channel_id=channel_id
            )
            ban.state = TelegramDeliveryState.SENT
            await feedback.apply_delivery_result(
                db,
                ban,
                TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.SENT,
                    reason="sent",
                ),
                utc_now(),
            )
            # A later relink must not cancel the compensating unban after the
            # provider has durably accepted the ban.
            db.add(
                self._user(
                    account_name="membership_saga_relinked_after_ban",
                    mobile="09128887743",
                    telegram_id=telegram_id,
                )
            )
            await db.commit()

        async with self.Session() as db:
            saga = await db.get(TelegramChannelMembershipSaga, int(saga.id))
            unban = await db.get(TelegramDeliveryJobRecord, int(saga.unban_job_id))
            unban_freshness = await validate_channel_membership_telegram_delivery_freshness(
                db,
                unban,
                utc_now(),
                expected_channel_id=channel_id,
            )
            self.assertEqual(unban_freshness.outcome, TelegramFreshnessOutcome.SEND)
            self.assertEqual(
                unban_freshness.reason,
                "membership_freshness_unban_compensation_required",
            )
            unban.state = TelegramDeliveryState.SENT
            feedback = TelegramChannelMembershipQueueLifecycleFeedback(
                expected_channel_id=channel_id
            )
            await feedback.apply_delivery_result(
                db,
                unban,
                TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.SENT,
                    reason="sent",
                ),
                utc_now(),
            )
            await db.commit()

        async with self.Session() as db:
            saga = await db.get(TelegramChannelMembershipSaga, int(saga.id))
            self.assertEqual(saga.state, "complete")
            self.assertIsNotNone(saga.ban_succeeded_at)
            self.assertIsNotNone(saga.completed_at)

    async def test_inactive_account_notice_materializes_membership_saga(self):
        outbox_id, _user_id = await self._seed_action_outbox(
            action=TelegramDeliveryAction.ACCOUNT_STATUS,
            account_status=UserAccountStatus.INACTIVE,
            current_account_status=UserAccountStatus.INACTIVE,
        )
        await self._handoff()

        async with self.Session() as db:
            saga = (
                await db.execute(
                    select(TelegramChannelMembershipSaga).where(
                        TelegramChannelMembershipSaga.source_outbox_id == outbox_id
                    )
                )
            ).scalar_one()
            ban = await db.get(TelegramDeliveryJobRecord, int(saga.ban_job_id))
            unban = await db.get(TelegramDeliveryJobRecord, int(saga.unban_job_id))
            self.assertEqual(saga.source_kind, "account_inactive")
            self.assertEqual(ban.action_kind, TelegramDeliveryAction.CHANNEL_MEMBER_BAN)
            self.assertEqual(
                unban.action_kind,
                TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN,
            )

    async def test_deleted_account_notice_skips_reassigned_predelete_route(self):
        async with self.Session() as db:
            deleted_user = self._user(
                account_name="deleted_notice_old_owner",
                mobile="09128887733",
                telegram_id=8_477_733,
            )
            db.add(deleted_user)
            await db.flush()
            deleted_user_id = int(deleted_user.id)
            telegram_id = int(deleted_user.telegram_id)
            deleted_user.is_deleted = True
            deleted_user.deleted_at = utc_now().replace(tzinfo=None)
            deleted_user.telegram_id = None
            deleted_user.sync_version = 2
            await enqueue_account_deletion_telegram_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=deleted_user_id,
                    telegram_id=telegram_id,
                ),
                source_id=f"account-deleted:{deleted_user_id}:2",
                text="حساب از پروژه حذف شد",
                user=deleted_user,
                user_sync_version=2,
            )
            db.add(
                self._user(
                    account_name="deleted_notice_new_owner",
                    mobile="09128887734",
                    telegram_id=telegram_id,
                )
            )
            await db.commit()

        handoff = await self._handoff()

        self.assertEqual(handoff.disposition, "skipped")
        self.assertEqual(
            handoff.reason,
            "notification_action_deleted_route_reassigned",
        )

    async def test_account_notice_waits_for_user_sync_version(self):
        outbox_id, user_id = await self._seed_action_outbox(
            action=TelegramDeliveryAction.ACCOUNT_STATUS,
            account_status=UserAccountStatus.INACTIVE,
            current_account_status=UserAccountStatus.ACTIVE,
            expected_user_sync_version=2,
        )
        first = await self._handoff()
        self.assertEqual(first.disposition, "deferred")
        self.assertEqual(
            first.reason,
            "notification_action_recipient_version_pending",
        )
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)
            self.assertIsNone(outbox.queue_job_id)
            self.assertIsNotNone(outbox.next_retry_at)
            user = await db.get(User, user_id, with_for_update=True)
            user.account_status = UserAccountStatus.INACTIVE
            user.sync_version = 2
            await db.commit()
        async with self.Session() as db:
            second = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=TEST_CHANNEL_ID,
                now=utc_now() + timedelta(seconds=2),
            )
            await db.commit()
        self.assertEqual(second.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)

    async def test_generic_action_waits_for_user_sync_version(self):
        outbox_id, user_id = await self._seed_action_outbox(
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            expected_user_sync_version=2,
        )
        first = await self._handoff()
        self.assertEqual(first.disposition, "deferred")
        self.assertEqual(
            first.reason,
            "notification_action_recipient_version_pending",
        )
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)
            self.assertIsNone(outbox.queue_job_id)
            user = await db.get(User, user_id, with_for_update=True)
            user.sync_version = 2
            await db.commit()
        async with self.Session() as db:
            second = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=TEST_CHANNEL_ID,
                now=utc_now() + timedelta(seconds=2),
            )
            await db.commit()
        self.assertEqual(second.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)

    async def test_inactive_account_notice_bypasses_market_access_only(self):
        outbox_id, _ = await self._seed_action_outbox(
            action=TelegramDeliveryAction.ACCOUNT_STATUS,
            account_status=UserAccountStatus.INACTIVE,
        )
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, int(handoff.job_id))
            decision = await validate_notification_action_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
            )
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_timed_security_uses_account_snapshot_and_timed_feeder(self):
        outbox_id, _ = await self._seed_action_outbox(
            action=TelegramDeliveryAction.TIMED_SECURITY,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked=True,
        )
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, int(handoff.job_id))
            decision = await validate_notification_action_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
            )
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
        self.assertEqual(job.action_kind, TelegramDeliveryAction.TIMED_SECURITY)
        self.assertEqual(job.feeder_kind.value, "timed_bot")
        self.assertEqual(job.priority, 5)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SEND)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_delayed_restriction_waits_and_revalidates_current_state(self):
        async with self.Session() as db:
            user = self._user(
                account_name="delayed_restriction_recipient",
                mobile="09128887771",
                telegram_id=8_477_771,
            )
            db.add(user)
            await db.flush()
            due = utc_now() + timedelta(minutes=2)
            result = await enqueue_delayed_restriction_telegram_notification_once(
                db,
                recipient=TelegramNotificationRecipient(
                    user_id=int(user.id),
                    telegram_id=int(user.telegram_id),
                ),
                source_id=f"delayed-limit:{user.id}:1",
                text="رفع محدودیت انجام شد",
                restriction_kind="limitations",
                not_before=due,
                user_sync_version=int(user.sync_version),
                parse_mode=None,
            )
            outbox_id = int(result.outbox.id)
            user_id = int(user.id)
            await db.commit()
        self.assertIsNone(await self._handoff())
        async with self.Session() as db:
            legacy = await claim_next_telegram_notification_outbox(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=due + timedelta(seconds=1),
            )
            await db.rollback()
        self.assertIsNotNone(legacy)
        self.assertEqual(
            legacy.source_type,
            f"queue_action:{TelegramDeliveryAction.DELAYED_RESTRICTION.value}",
        )
        async with self.Session() as db:
            handoff = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                expected_channel_id=TEST_CHANNEL_ID,
                now=due,
            )
            await db.commit()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        async with self.Session() as db:
            user = await db.get(User, user_id, with_for_update=True)
            user.max_daily_trades = 1
            user.sync_version = int(user.sync_version) + 1
            await db.commit()
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, int(handoff.job_id))
            decision = await validate_notification_action_telegram_delivery_freshness(
                db,
                job,
                due + timedelta(seconds=1),
            )
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
        self.assertEqual(job.action_kind, TelegramDeliveryAction.DELAYED_RESTRICTION)
        self.assertEqual(job.priority, 5)
        self.assertEqual(decision.outcome, TelegramFreshnessOutcome.SUPERSEDED)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.PENDING)

    async def test_generic_action_is_claimed_by_legacy_owner_before_cutover(self):
        await self._seed_action_outbox(
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
        )
        async with self.Session() as db:
            claimed = await claim_next_telegram_notification_outbox(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=utc_now() + timedelta(hours=1),
            )
            await db.rollback()
        self.assertIsNotNone(claimed)
        self.assertEqual(
            claimed.source_type,
            f"queue_action:{TelegramDeliveryAction.GENERAL_IMMEDIATE.value}",
        )

    async def test_offer_success_edit_handoff_and_success_are_atomic(self):
        outbox_id, _, _ = await self._seed_offer_success_outbox()
        handoff = await self._handoff()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
        job = await self._claim_job(worker_id="offer-success-bridge")
        self.assertEqual(job.action_kind, TelegramDeliveryAction.OFFER_SUCCESS)
        self.assertEqual(job.method, "editMessageText")
        self.assertEqual(job.payload["message_id"], 881)
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "offer_success_dispatch_guard_required",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=int(job.id),
                    worker_id="offer-success-bridge",
                    lease_token=int(job.lease_token),
                    now=utc_now(),
                )
            await db.rollback()
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_offer_success_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
            )
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.SEND)
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="offer-success-bridge",
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
                worker_id="offer-success-bridge",
                lease_token=int(current.lease_token),
                result=TelegramGatewayResult(
                    ok=True,
                    method="editMessageText",
                    status_code=200,
                    response_json={"ok": True, "result": {"message_id": 881}},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
            self.assertEqual(outbox.telegram_message_id, 881)
            self.assertIsNone(outbox.queue_job_id)
            self.assertEqual(current.state, TelegramDeliveryState.SENT)

    async def test_offer_success_is_skipped_when_offer_becomes_terminal(self):
        outbox_id, _, offer_id = await self._seed_offer_success_outbox()
        async with self.Session() as db:
            offer = await db.get(Offer, offer_id, with_for_update=True)
            offer.status = OfferStatus.EXPIRED
            await db.commit()
        result = await self._handoff()
        self.assertEqual(result.disposition, "skipped")
        self.assertEqual(result.reason, "offer_success_source_state_changed")
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            job_count = int(
                (
                    await db.execute(
                        select(text("count(*)")).select_from(
                            TelegramDeliveryJobRecord
                        )
                    )
                ).scalar_one()
            )
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
        self.assertEqual(job_count, 0)

    async def test_offer_success_never_retargets_a_relinked_chat(self):
        outbox_id, user_id, _ = await self._seed_offer_success_outbox()
        async with self.Session() as db:
            user = await db.get(User, user_id, with_for_update=True)
            user.telegram_id = int(user.telegram_id) + 1
            user.sync_version = int(user.sync_version) + 1
            await db.commit()
        result = await self._handoff()
        self.assertEqual(result.disposition, "skipped")
        self.assertEqual(result.reason, "offer_success_source_state_changed")
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SKIPPED)

    async def test_offer_success_is_never_claimed_by_legacy_outbox_worker(self):
        await self._seed_offer_success_outbox()
        async with self.Session() as db:
            claimed = await claim_next_telegram_notification_outbox(
                db,
                current_server="foreign",
                lease_seconds=30,
                now=utc_now() + timedelta(hours=1),
            )
            await db.rollback()
        self.assertIsNone(claimed)

    async def test_physical_binding_constraints_and_indexes_exist(self):
        async with self.Session() as db:
            indexes = set(
                (
                    await db.execute(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE tablename = 'telegram_notification_outbox'"
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
                            "'telegram_notification_outbox'::regclass"
                        )
                    )
                ).scalars()
            )
            queue_predicate = (
                await db.execute(
                    text(
                        "SELECT pg_get_expr(i.indpred, i.indrelid) "
                        "FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indexrelid "
                        "WHERE c.relname = "
                        "'ix_telegram_notification_outbox_queue_handoff'"
                    )
                )
            ).scalar_one()
        self.assertIn("ix_telegram_notification_outbox_queue_handoff", indexes)
        self.assertIn("ux_telegram_notification_outbox_queue_job", indexes)
        self.assertIn("ck_telegram_notification_outbox_queue_binding", constraints)
        self.assertIn("fk_telegram_notification_outbox_queue_job", constraints)
        self.assertIn("project_user_joined", queue_predicate)
        self.assertIn("offer_repeat_response", queue_predicate)
        self.assertIn("queue_action:account_status", queue_predicate)
        self.assertIn("queue_action:trade_alternative", queue_predicate)
        self.assertIn("offer_success_preview", queue_predicate)


if __name__ == "__main__":
    unittest.main()
