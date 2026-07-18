import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import UserAccountStatus
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
    claim_next_telegram_notification_outbox,
    enqueue_account_status_telegram_notification_once,
    enqueue_offer_repeat_response_notification,
    enqueue_telegram_action_notification_once,
    telegram_notification_dedupe_key,
)
from core.telegram_delivery_notification_action_freshness import (
    validate_notification_action_telegram_delivery_freshness,
)
from core.telegram_delivery_new_user_membership_freshness import (
    validate_new_user_membership_telegram_delivery_freshness,
)
from core.telegram_delivery_repeat_offer_freshness import (
    validate_repeat_offer_response_telegram_delivery_freshness,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryOutcome,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramFreshnessDecision,
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
                    "TRUNCATE TABLE telegram_notification_outbox, "
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
            if action == TelegramDeliveryAction.ACCOUNT_STATUS:
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

    async def test_relink_reclassifies_and_builds_new_job(self):
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
            self.assertEqual(freshness.outcome, TelegramFreshnessOutcome.RECLASSIFY)
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
        self.assertNotEqual(replacement.job_id, first.job_id)
        async with self.Session() as db:
            old = await db.get(TelegramDeliveryJobRecord, int(first.job_id))
            new = await db.get(TelegramDeliveryJobRecord, int(replacement.job_id))
            outbox = await db.get(TelegramNotificationOutbox, outbox_ids[0])
            user = await db.get(User, user_ids[0])
            self.assertEqual(old.state, TelegramDeliveryState.PENDING_RECONCILE)
            self.assertEqual(new.payload["chat_id"], int(user.telegram_id))
            self.assertEqual(outbox.queue_job_id, int(new.id))

    async def test_repeat_response_relink_reclassifies_to_distinct_job(self):
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
                    TelegramFreshnessOutcome.RECLASSIFY,
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
        self.assertNotEqual(replacement.job_id, first.job_id)
        async with self.Session() as db:
            old = await db.get(TelegramDeliveryJobRecord, int(first.job_id))
            new = await db.get(TelegramDeliveryJobRecord, int(replacement.job_id))
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            user = await db.get(User, user_id)
            self.assertEqual(old.state, TelegramDeliveryState.PENDING_RECONCILE)
            self.assertEqual(new.payload["chat_id"], int(user.telegram_id))
            self.assertNotEqual(new.source_version, old.source_version)
            self.assertEqual(outbox.queue_job_id, int(new.id))

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

    async def test_generic_action_is_never_claimed_by_legacy_outbox_worker(self):
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


if __name__ == "__main__":
    unittest.main()
