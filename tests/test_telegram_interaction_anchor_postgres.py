import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.enums import UserAccountStatus
from bot import telegram_interaction_message as interaction_adapter
from core.services.telegram_delivery_queue_service import (
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    resolve_telegram_delivery_result,
)
from core.services.telegram_interaction_outbox_service import (
    enqueue_private_interaction_once,
)
from core.services.telegram_notification_outbox_queue_feedback import (
    TelegramNotificationOutboxQueueLifecycleFeedback,
)
from core.services.telegram_notification_outbox_queue_service import (
    NOTIFICATION_OUTBOX_QUEUE_HANDOFF,
    NOTIFICATION_OUTBOX_QUEUE_SKIPPED,
    handoff_next_due_telegram_notification_outbox,
)
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionResultRequirement,
    parse_interaction_result_contract,
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
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeMode,
)
from core.telegram_gateway import TelegramGatewayResult
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_interaction_anchor_state import TelegramInteractionAnchorState
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User, UserRole
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


CHAT_ID = 8_711_001


def _persistent_menu() -> dict[str, object]:
    return {
        "keyboard": [[{"text": "منوی اصلی"}]],
        "resize_keyboard": True,
    }


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramInteractionAnchorPostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "telegram_interaction_anchor_states, "
                    "telegram_notification_outbox, telegram_delivery_jobs, users "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await db.execute(
                text(
                    "ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq "
                    "RESTART WITH 1"
                )
            )
            db.add(
                User(
                    account_name="interaction_anchor_user",
                    mobile_number="09128711001",
                    full_name="Interaction Anchor User",
                    address="test",
                    role=UserRole.STANDARD,
                    account_status=UserAccountStatus.ACTIVE,
                    telegram_id=CHAT_ID,
                    sync_version=1,
                    is_deleted=False,
                    has_bot_access=True,
                )
            )
            await db.commit()
        async with self.Session() as db:
            self.user_id = int(
                (
                    await db.execute(
                        select(User.id).where(User.telegram_id == CHAT_ID)
                    )
                ).scalar_one()
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _enqueue_anchor(self, *, source_id: str, logical_key: str):
        async with self.Session() as db:
            result = await enqueue_private_interaction_once(
                db,
                current_server="foreign",
                recipient=TelegramNotificationRecipient(
                    user_id=self.user_id,
                    telegram_id=CHAT_ID,
                ),
                action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
                source_id=source_id,
                logical_message_key=logical_key,
                text=f"پنل {logical_key}",
                user_sync_version=1,
                reply_markup=_persistent_menu(),
                result_requirement=(
                    TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
                ),
                anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            )
            await db.commit()
            return (
                int(result.notification.outbox.id),
                int(result.contract.anchor_generation),
            )

    async def _handoff_and_claim(self, *, worker_id: str):
        async with self.Session() as db:
            handoff = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
        self.assertIsNotNone(handoff)
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_HANDOFF)
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

    async def _send_success(self, job, *, worker_id: str, message_id: int):
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))
            freshness = await validate_notification_action_telegram_delivery_freshness(
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
                        "result": {"message_id": message_id},
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

    async def test_sent_receipt_and_anchor_activate_in_one_lifecycle(self):
        outbox_id, generation = await self._enqueue_anchor(
            source_id="interaction:anchor:sent",
            logical_key="user:anchor:sent",
        )
        job = await self._handoff_and_claim(worker_id="interaction-anchor-sent")
        decision = await self._send_success(
            job,
            worker_id="interaction-anchor-sent",
            message_id=91_001,
        )

        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
            current = await db.get(TelegramDeliveryJobRecord, int(job.id))

        self.assertEqual(generation, 1)
        self.assertEqual(current.state, TelegramDeliveryState.SENT)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(outbox.telegram_message_id, 91_001)
        self.assertEqual(anchor.active_generation, 1)
        self.assertEqual(anchor.active_outbox_id, outbox_id)
        self.assertEqual(anchor.active_message_id, 91_001)
        self.assertEqual(anchor.active_logical_message_key, "user:anchor:sent")

    async def test_runtime_adapter_persists_and_delivers_non_anchor_reply(self):
        async with self.Session() as db:
            user = await db.get(User, self.user_id)
            message = SimpleNamespace(
                chat=SimpleNamespace(id=CHAT_ID),
                message_id=701,
                answer=AsyncMock(),
            )
            with (
                patch.object(
                    interaction_adapter,
                    "configured_telegram_delivery_runtime",
                    return_value=SimpleNamespace(
                        mode=TelegramDeliveryRuntimeMode.QUEUE_V1
                    ),
                ),
                patch.object(
                    interaction_adapter,
                    "current_server",
                    return_value="foreign",
                ),
            ):
                result = await interaction_adapter.answer_incoming_message_via_runtime(
                    message,
                    user,
                    "کاربری یافت نشد",
                    source_key="block-search-empty",
                    reply_markup={"inline_keyboard": []},
                    session=db,
                    commit=False,
                )
            outbox_id = int(result.notification.outbox.id)
            await db.commit()

        message.answer.assert_not_awaited()
        self.assertIsNone(result.anchor_state)
        job = await self._handoff_and_claim(worker_id="interaction-adapter-non-anchor")
        decision = await self._send_success(
            job,
            worker_id="interaction-adapter-non-anchor",
            message_id=91_003,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
        self.assertEqual(outbox.status, TelegramNotificationOutboxStatus.SENT)
        self.assertEqual(outbox.telegram_message_id, 91_003)
        self.assertIsNone(anchor)

    async def test_physical_schema_rejects_active_outbox_without_active_anchor(self):
        await self._enqueue_anchor(
            source_id="interaction:anchor:constraint",
            logical_key="user:anchor:constraint",
        )

        async with self.Session() as db:
            with self.assertRaises(IntegrityError):
                await db.execute(
                    text(
                        "UPDATE telegram_interaction_anchor_states "
                        "SET active_outbox_id = desired_outbox_id "
                        "WHERE chat_id = :chat_id"
                    ),
                    {"chat_id": CHAT_ID},
                )
                await db.commit()
            await db.rollback()

    async def test_concurrent_enqueue_allocates_unique_monotonic_generations(self):
        results = await asyncio.gather(
            self._enqueue_anchor(
                source_id="interaction:anchor:concurrent:a",
                logical_key="user:anchor:concurrent:a",
            ),
            self._enqueue_anchor(
                source_id="interaction:anchor:concurrent:b",
                logical_key="user:anchor:concurrent:b",
            ),
        )

        outbox_ids = {result[0] for result in results}
        self.assertEqual({result[1] for result in results}, {1, 2})
        async with self.Session() as db:
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
            outboxes = list(
                (
                    await db.execute(
                        select(TelegramNotificationOutbox).where(
                            TelegramNotificationOutbox.id.in_(outbox_ids)
                        )
                    )
                ).scalars()
            )

        contracts = {
            int(outbox.id): parse_interaction_result_contract(
                outbox.extra_payload["interaction_result"]
            )
            for outbox in outboxes
        }
        self.assertEqual(
            {contract.anchor_generation for contract in contracts.values()},
            {1, 2},
        )
        self.assertEqual(anchor.desired_generation, 2)
        self.assertIn(anchor.desired_outbox_id, outbox_ids)
        self.assertEqual(
            contracts[int(anchor.desired_outbox_id)].anchor_generation,
            2,
        )

    async def test_older_generation_is_superseded_before_send(self):
        first_outbox_id, first_generation = await self._enqueue_anchor(
            source_id="interaction:anchor:old",
            logical_key="user:anchor:old",
        )
        second_outbox_id, second_generation = await self._enqueue_anchor(
            source_id="interaction:anchor:new",
            logical_key="user:anchor:new",
        )
        self.assertEqual((first_generation, second_generation), (1, 2))

        old_job = await self._handoff_and_claim(worker_id="interaction-anchor-old")
        feedback = TelegramNotificationOutboxQueueLifecycleFeedback()
        async with self.Session() as db:
            current = await db.get(TelegramDeliveryJobRecord, int(old_job.id))
            freshness = await validate_notification_action_telegram_delivery_freshness(
                db,
                current,
                utc_now(),
            )
            self.assertEqual(
                freshness.outcome,
                TelegramFreshnessOutcome.SUPERSEDED,
            )
            self.assertEqual(
                freshness.reason,
                "notification_action_freshness_anchor_superseded",
            )
            may_send = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=int(current.id),
                worker_id="interaction-anchor-old",
                lease_token=int(current.lease_token),
                decision=freshness,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            self.assertFalse(may_send)
            await db.commit()

        async with self.Session() as db:
            old_outbox = await db.get(TelegramNotificationOutbox, first_outbox_id)
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
        self.assertEqual(old_outbox.status, TelegramNotificationOutboxStatus.SKIPPED)
        self.assertIsNone(anchor.active_generation)

        new_job = await self._handoff_and_claim(worker_id="interaction-anchor-new")
        decision = await self._send_success(
            new_job,
            worker_id="interaction-anchor-new",
            message_id=91_002,
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
            new_outbox = await db.get(TelegramNotificationOutbox, second_outbox_id)
        self.assertEqual(anchor.active_generation, 2)
        self.assertEqual(anchor.active_outbox_id, second_outbox_id)
        self.assertEqual(anchor.active_message_id, 91_002)
        self.assertEqual(new_outbox.status, TelegramNotificationOutboxStatus.SENT)

    async def test_relinked_recipient_cannot_retarget_an_existing_anchor_intent(self):
        outbox_id, _ = await self._enqueue_anchor(
            source_id="interaction:anchor:relink",
            logical_key="user:anchor:relink",
        )
        async with self.Session() as db:
            user = await db.get(User, self.user_id, with_for_update=True)
            user.telegram_id = CHAT_ID + 100
            user.sync_version = int(user.sync_version) + 1
            await db.commit()

        async with self.Session() as db:
            handoff = await handoff_next_due_telegram_notification_outbox(
                db,
                current_server="foreign",
                now=utc_now(),
            )
            await db.commit()
        self.assertEqual(handoff.disposition, NOTIFICATION_OUTBOX_QUEUE_SKIPPED)
        self.assertEqual(handoff.reason, "notification_action_recipient_relinked")

        async with self.Session() as db:
            outbox = await db.get(TelegramNotificationOutbox, outbox_id)
            anchor = await db.get(TelegramInteractionAnchorState, CHAT_ID)
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
        self.assertIsNone(anchor.active_generation)
        self.assertIsNone(anchor.active_message_id)


if __name__ == "__main__":
    unittest.main()
