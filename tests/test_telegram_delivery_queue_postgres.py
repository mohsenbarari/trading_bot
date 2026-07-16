import asyncio
from datetime import timedelta
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDedupeConflictError,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_queue_limiter import (
    TelegramDeliveryDispatchAdmission,
    TelegramDeliveryLimiterUnavailableError,
)
from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    TelegramDeliveryQueueSurfaceError,
    apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    enqueue_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started,
    recover_expired_telegram_delivery_leases,
    resolve_telegram_delivery_result,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from core.telegram_delivery_queue_worker import run_telegram_delivery_queue_cycle
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


DATABASE_NAME_PATTERN = re.compile(r"^telegram_queue_stage3_[a-z0-9_]+_test$")


def _test_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    target = make_url(explicit)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Telegram queue PostgreSQL tests require a telegram_queue_stage3_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False),
        target.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


DATABASE_URLS = _test_database_urls()


class _AllowLimiter:
    async def acquire(self, _job, *, now):
        return TelegramDeliveryDispatchAdmission(allowed=True)

    async def observe(self, _job, _decision, *, now):
        return None


class _DenyLimiter(_AllowLimiter):
    async def acquire(self, _job, *, now):
        return TelegramDeliveryDispatchAdmission(
            allowed=False,
            retry_after_seconds=0.75,
            wait_reason="destination_gate",
        )


class _UnavailableLimiter(_AllowLimiter):
    async def acquire(self, _job, *, now):
        raise TelegramDeliveryLimiterUnavailableError("synthetic_limiter_down")


def _run_alembic(sync_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = os.getcwd()
    env["TRADING_BOT_EXPECTED_ALEMBIC_HEAD"] = "f2c7d8e9a0bd"
    result = subprocess.run(
        [sys.executable, "scripts/run_guarded_scratch_alembic.py", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


class TelegramDeliveryQueueDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL": "postgresql://u:p@db/trading_bot"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "telegram_queue_stage3"):
                _test_database_urls()


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryQueuePostgresTests(unittest.IsolatedAsyncioTestCase):
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
            await db.execute(text("TRUNCATE TABLE telegram_delivery_jobs RESTART IDENTITY CASCADE"))
            await db.execute(text("ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq RESTART WITH 1"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _enqueue_kwargs(
        source_id: str,
        *,
        feeder: TelegramFeederKind = TelegramFeederKind.DIRECT,
        action: TelegramDeliveryAction = TelegramDeliveryAction.GENERAL_IMMEDIATE,
        destination: str = "private:1001",
        destination_class: TelegramDestinationClass = TelegramDestinationClass.PRIVATE,
        method: str = "sendMessage",
        payload: dict | None = None,
        deadline=None,
        bot_identity: str = "primary",
    ) -> dict:
        return {
            "current_server": "foreign",
            "feeder": feeder,
            "source_natural_id": source_id,
            "source_version": 1,
            "action": action,
            "bot_identity": bot_identity,
            "destination_key": destination,
            "destination_class": destination_class,
            "method": method,
            "payload": payload or {"chat_id": 1001, "text": source_id},
            "template_version": "test-v1",
            "delivery_deadline_at": deadline,
            "run_id": "stage3-foundation-test",
        }

    async def _enqueue(self, source_id: str, **overrides):
        kwargs = self._enqueue_kwargs(source_id, **overrides)
        async with self.Session() as db:
            result = await enqueue_telegram_delivery_job(db, **kwargs)
            await db.commit()
            return result

    async def _claim(self, worker: str, *, now=None, bot_identity: str = "primary"):
        async with self.Session() as db:
            job = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity=bot_identity,
                worker_id=worker,
                request_timeout_seconds=10,
                lease_seconds=30,
                now=now,
            )
            await db.commit()
            return job

    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

    async def test_concurrent_enqueue_creates_exactly_one_logical_job(self):
        start = asyncio.Event()

        async def enqueue_once():
            async with self.Session() as db:
                await start.wait()
                result = await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs("concurrent-dedupe"),
                )
                await db.commit()
                return result.created, int(result.job.id)

        tasks = [asyncio.create_task(enqueue_once()) for _ in range(20)]
        start.set()
        results = await asyncio.gather(*tasks)

        self.assertEqual(sum(created for created, _ in results), 1)
        self.assertEqual(len({job_id for _, job_id in results}), 1)
        async with self.Session() as db:
            count = await db.scalar(select(func.count()).select_from(TelegramDeliveryJobRecord))
        self.assertEqual(count, 1)

    async def test_same_identity_with_changed_payload_is_rejected(self):
        await self._enqueue("dedupe-conflict")
        async with self.Session() as db:
            with self.assertRaises(TelegramDeliveryDedupeConflictError):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs(
                        "dedupe-conflict",
                        payload={"chat_id": 1001, "text": "different"},
                    ),
                )
            await db.rollback()

    async def test_oversized_identity_is_rejected_before_database_insert(self):
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "source_natural_id_too_long",
            ):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs("x" * 257),
                )
            await db.rollback()
        async with self.Session() as db:
            count = await db.scalar(select(func.count()).select_from(TelegramDeliveryJobRecord))
        self.assertEqual(count, 0)

    async def test_channel_editor_accepts_only_allowlisted_channel_edits(self):
        accepted = await self._enqueue(
            "editor-valid-edit",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            destination="channel:market",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 55, "text": "expired"},
            bot_identity="channel_editor",
        )
        self.assertTrue(accepted.created)

        invalid_cases = (
            {
                "source_id": "editor-send",
                "action": TelegramDeliveryAction.OFFER_PUBLISH,
                "destination": "channel:market",
                "destination_class": TelegramDestinationClass.CHANNEL,
                "method": "sendMessage",
                "payload": {"chat_id": -1001, "text": "forbidden"},
            },
            {
                "source_id": "editor-private-edit",
                "action": TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
                "destination": "private:1001",
                "destination_class": TelegramDestinationClass.PRIVATE,
                "method": "editMessageReplyMarkup",
                "payload": {"chat_id": 1001, "message_id": 10, "reply_markup": {}},
            },
        )
        for case in invalid_cases:
            source_id = case.pop("source_id")
            with self.subTest(source_id=source_id):
                async with self.Session() as db:
                    with self.assertRaisesRegex(
                        TelegramDeliveryQueueValidationError,
                        "channel_editor_route_not_allowlisted",
                    ):
                        await enqueue_telegram_delivery_job(
                            db,
                            **self._enqueue_kwargs(
                                source_id,
                                feeder=TelegramFeederKind.OFFER_EDIT,
                                bot_identity="channel_editor",
                                **case,
                            ),
                        )
                    await db.rollback()

    async def test_existing_job_cannot_be_replayed_with_a_different_bot_route(self):
        kwargs = self._enqueue_kwargs(
            "immutable-bot-route",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            destination="channel:market",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 56, "text": "expired"},
            bot_identity="primary",
        )
        async with self.Session() as db:
            created = await enqueue_telegram_delivery_job(db, **kwargs)
            await db.commit()
        self.assertTrue(created.created)

        kwargs["bot_identity"] = "channel_editor"
        async with self.Session() as db:
            with self.assertRaises(TelegramDeliveryDedupeConflictError):
                await enqueue_telegram_delivery_job(db, **kwargs)
            await db.rollback()

    async def test_unknown_bot_identity_is_rejected(self):
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "telegram_bot_identity_not_allowlisted",
            ):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs(
                        "unknown-bot",
                        bot_identity="bot:token-or-arbitrary-identity",
                    ),
                )
            await db.rollback()

    async def test_claim_rejects_unknown_bot_lane_before_query(self):
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "telegram_bot_identity_not_allowlisted",
            ):
                await claim_next_telegram_delivery_job(
                    db,
                    current_server="foreign",
                    bot_identity="unknown-lane",
                    worker_id="worker-invalid-lane",
                    request_timeout_seconds=10,
                    lease_seconds=30,
                )
            await db.rollback()

    async def test_editor_claim_lane_progresses_with_300_primary_and_100_editor_jobs(self):
        async with self.Session() as db:
            for index in range(300):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs(f"hol-primary-{index}"),
                )
            for index in range(100):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs(
                        f"hol-editor-{index}",
                        feeder=TelegramFeederKind.OFFER_EDIT,
                        action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                        destination="channel:market",
                        destination_class=TelegramDestinationClass.CHANNEL,
                        method="editMessageText",
                        payload={
                            "chat_id": -1001,
                            "message_id": 10_000 + index,
                            "text": "expired",
                        },
                        bot_identity="channel_editor",
                    ),
                )
            await db.commit()

        async with self.Session() as db:
            claimed_editor_ids = []
            for index in range(100):
                job = await claim_next_telegram_delivery_job(
                    db,
                    current_server="foreign",
                    bot_identity="channel_editor",
                    worker_id=f"editor-worker-{index}",
                    request_timeout_seconds=10,
                    lease_seconds=30,
                )
                self.assertIsNotNone(job)
                self.assertEqual(job.bot_identity, "channel_editor")
                claimed_editor_ids.append(int(job.id))
            await db.commit()

        self.assertEqual(len(set(claimed_editor_ids)), 100)
        async with self.Session() as db:
            pending_primary = await db.scalar(
                select(func.count())
                .select_from(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.bot_identity == "primary",
                    TelegramDeliveryJobRecord.state == TelegramDeliveryState.PENDING,
                )
            )
        self.assertEqual(pending_primary, 300)

    async def test_database_constraint_rejects_editor_send_even_if_service_is_bypassed(self):
        enqueued = await self._enqueue("constraint-primary-send")
        async with self.Session() as db:
            with self.assertRaises(IntegrityError):
                await db.execute(
                    update(TelegramDeliveryJobRecord)
                    .where(TelegramDeliveryJobRecord.id == enqueued.job.id)
                    .values(bot_identity="channel_editor")
                )
            await db.rollback()

    async def test_physical_schema_contains_route_constraints_and_dedupe_capacity(self):
        async with self.Session() as db:
            dedupe_length = await db.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'telegram_delivery_jobs' AND column_name = 'dedupe_key'"
                )
            )
            constraint_names = set(
                (
                    await db.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'telegram_delivery_jobs'::regclass"
                        )
                    )
                ).scalars()
            )
            claim_index_columns = await db.scalar(
                text(
                    "SELECT array_agg(attribute.attname ORDER BY indexed_column.ordinality) "
                    "FROM pg_index AS index_meta "
                    "JOIN pg_class AS index_class ON index_class.oid = index_meta.indexrelid "
                    "CROSS JOIN LATERAL unnest(index_meta.indkey) WITH ORDINALITY "
                    "AS indexed_column(attnum, ordinality) "
                    "JOIN pg_attribute AS attribute "
                    "ON attribute.attrelid = index_meta.indrelid "
                    "AND attribute.attnum = indexed_column.attnum "
                    "WHERE index_class.relname = 'ix_telegram_delivery_jobs_claim'"
                )
            )
        self.assertEqual(dedupe_length, 1024)
        self.assertTrue(
            {
                "ck_telegram_delivery_jobs_bot_identity",
                "ck_telegram_delivery_jobs_editor_route",
            }.issubset(constraint_names)
        )
        self.assertEqual(
            list(claim_index_columns or ()),
            [
                "bot_identity",
                "priority",
                "priority_rank",
                "delivery_deadline_at",
                "eligible_at",
                "next_retry_at",
                "enqueued_seq",
            ],
        )

    async def test_claim_order_promotes_overdue_trade_without_overriding_callback(self):
        now = utc_now()
        await self._enqueue(
            "offer-publish",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination="channel:market",
            destination_class=TelegramDestinationClass.CHANNEL,
        )
        await self._enqueue(
            "trade-overdue",
            feeder=TelegramFeederKind.TRADE,
            action=TelegramDeliveryAction.TRADE_RESULT,
            deadline=now - timedelta(milliseconds=1),
        )
        await self._enqueue(
            "callback",
            action=TelegramDeliveryAction.CALLBACK_DEADLINE,
            method="answerCallbackQuery",
            payload={"callback_query_id": "test-callback"},
            deadline=now + timedelta(seconds=2),
        )

        claimed = [await self._claim(f"worker-{index}", now=now) for index in range(3)]
        self.assertEqual(
            [job.source_natural_id for job in claimed],
            ["callback", "trade-overdue", "offer-publish"],
        )

    async def test_skip_locked_allows_two_workers_to_claim_different_rows(self):
        await self._enqueue("skip-locked-1")
        await self._enqueue("skip-locked-2")
        now = utc_now()
        async with self.Session() as first, self.Session() as second:
            first_job = await claim_next_telegram_delivery_job(
                first,
                current_server="foreign",
                bot_identity="primary",
                worker_id="worker-first",
                request_timeout_seconds=10,
                lease_seconds=30,
                now=now,
            )
            second_job = await claim_next_telegram_delivery_job(
                second,
                current_server="foreign",
                bot_identity="primary",
                worker_id="worker-second",
                request_timeout_seconds=10,
                lease_seconds=30,
                now=now,
            )
            self.assertNotEqual(first_job.id, second_job.id)
            await first.commit()
            await second.commit()

    async def test_fencing_rejects_old_worker_result_after_lease_recovery(self):
        await self._enqueue("fencing")
        started = utc_now()
        first = await self._claim("worker-old", now=started)
        async with self.Session() as db:
            recovery = await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=started + timedelta(seconds=31),
            )
            await db.commit()
        self.assertEqual(recovery.released_unstarted, 1)

        second = await self._claim("worker-new", now=started + timedelta(seconds=31))
        self.assertGreater(second.lease_token, first.lease_token)
        success = TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            status_code=200,
            response_json={"ok": True, "result": {"message_id": 9001}},
        )
        async with self.Session() as db:
            stale = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id="worker-old",
                lease_token=first.lease_token,
                result=success,
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=started + timedelta(seconds=32),
            )
            await db.commit()
        self.assertEqual(stale.outcome, TelegramDeliveryOutcome.STALE_LEASE)

        async with self.Session() as db:
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=second.id,
                worker_id="worker-new",
                lease_token=second.lease_token,
                now=started + timedelta(seconds=32),
            )
            await db.commit()
        self.assertTrue(marked)
        async with self.Session() as db:
            sent = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=second.id,
                worker_id="worker-new",
                lease_token=second.lease_token,
                result=success,
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=started + timedelta(seconds=33),
            )
            await db.commit()
        self.assertEqual(sent.outcome, TelegramDeliveryOutcome.SENT)

    async def test_result_is_rejected_until_dispatch_marker_is_committed(self):
        await self._enqueue("dispatch-marker-required")
        now = utc_now()
        job = await self._claim("worker-without-marker", now=now)
        async with self.Session() as db:
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                result=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={"ok": True, "result": {"message_id": 1}},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now,
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.STALE_LEASE)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.LEASED)
        self.assertIsNone(persisted.telegram_message_id)

    async def test_dispatch_marker_is_single_use_for_one_fence(self):
        await self._enqueue("single-dispatch-marker")
        now = utc_now()
        job = await self._claim("worker-single-marker", now=now)
        async with self.Session() as db:
            first = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            second = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                now=now + timedelta(milliseconds=1),
            )
            await db.rollback()
        self.assertTrue(first)
        self.assertFalse(second)

    async def test_reclassification_preserves_immutable_identity_and_waits_for_reconciliation(self):
        enqueued = await self._enqueue(
            "partial-became-terminal",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            destination="channel:market",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 44, "text": "partial"},
        )
        original_dedupe = enqueued.job.dedupe_key
        job = await self._claim("worker-reclassify", now=utc_now())
        async with self.Session() as db:
            may_dispatch = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                decision=TelegramFreshnessDecision(
                    TelegramFreshnessOutcome.RECLASSIFY,
                    replacement_action=TelegramDeliveryAction.TRADED_OFFER_EDIT,
                    reason="partial_became_terminal",
                ),
            )
            await db.commit()
        self.assertFalse(may_dispatch)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
        self.assertEqual(persisted.dedupe_key, original_dedupe)
        self.assertEqual(persisted.action_kind, TelegramDeliveryAction.PARTIAL_OFFER_EDIT)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING_RECONCILE)
        self.assertIsNone(persisted.worker_id)
        self.assertIsNone(persisted.next_retry_at)

    async def test_dispatch_marker_refuses_iran_surface(self):
        await self._enqueue("foreign-only-marker")
        job = await self._claim("worker-foreign", now=utc_now())
        async with self.Session() as db:
            with self.assertRaises(TelegramDeliveryQueueSurfaceError):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="iran",
                    job_id=job.id,
                    worker_id=job.worker_id,
                    lease_token=job.lease_token,
                )
            await db.rollback()

    async def test_expired_leases_never_blindly_retry_started_dispatches(self):
        await self._enqueue("unstarted")
        await self._enqueue("started-send")
        await self._enqueue(
            "started-edit",
            feeder=TelegramFeederKind.TIMED_BOT,
            action=TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
            method="editMessageReplyMarkup",
            payload={"chat_id": 1001, "message_id": 11, "reply_markup": {"inline_keyboard": []}},
        )
        now = utc_now()
        jobs = [await self._claim(f"worker-{index}", now=now) for index in range(3)]
        by_source = {job.source_natural_id: job for job in jobs}
        for source_id in ("started-send", "started-edit"):
            job = by_source[source_id]
            async with self.Session() as db:
                self.assertTrue(
                    await mark_telegram_delivery_dispatch_started(
                        db,
                        current_server="foreign",
                        job_id=job.id,
                        worker_id=job.worker_id,
                        lease_token=job.lease_token,
                        now=now + timedelta(seconds=1),
                    )
                )
                await db.commit()
        async with self.Session() as db:
            for job in jobs:
                persisted = await db.get(TelegramDeliveryJobRecord, job.id)
                persisted.lease_until = now - timedelta(seconds=1)
            await db.commit()
        async with self.Session() as db:
            report = await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=now,
            )
            await db.commit()
        self.assertEqual(report.released_unstarted, 1)
        self.assertEqual(report.ambiguous_sends, 1)
        self.assertEqual(report.pending_reconcile, 1)
        async with self.Session() as db:
            rows = list((await db.execute(select(TelegramDeliveryJobRecord))).scalars())
        states = {row.source_natural_id: row.state for row in rows}
        self.assertEqual(states["unstarted"], TelegramDeliveryState.PENDING_RETRY)
        self.assertEqual(states["started-send"], TelegramDeliveryState.AMBIGUOUS)
        self.assertEqual(states["started-edit"], TelegramDeliveryState.PENDING_RECONCILE)

    async def test_429_envelope_preserves_raw_retry_after_without_terminal_failure(self):
        await self._enqueue("rate-limited")
        now = utc_now()
        job = await self._claim("worker-rate-limit", now=now)
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=job.id,
                    worker_id=job.worker_id,
                    lease_token=job.lease_token,
                    now=now,
                )
            )
            await db.commit()
        response = TelegramGatewayResult(
            ok=True,
            method="sendMessage",
            status_code=200,
            response_json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 1_000_000},
            },
        )
        async with self.Session() as db:
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                result=response,
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now,
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertEqual(persisted.last_retry_after_seconds, 1_000_000)
        self.assertFalse(persisted.provider_ok)
        self.assertEqual(persisted.provider_status_code, 200)
        self.assertEqual(persisted.provider_error_code, 429)
        self.assertAlmostEqual(
            (persisted.next_retry_at - now).total_seconds(),
            1_000_000.1,
            places=5,
        )

    async def test_worker_commits_claim_and_dispatch_marker_before_gateway_io(self):
        enqueued = await self._enqueue("worker-transaction-boundary")
        observed = {}

        async def freshness_validator(db, job, now):
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
            observed["freshness_state"] = persisted.state
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        async def gateway_call(method, payload, **kwargs):
            observed["method"] = method
            observed["payload"] = payload
            observed["idempotency_key"] = kwargs["idempotency_key"]
            # NOWAIT succeeds only if the worker did not keep its claim/marker
            # transaction open over this simulated HTTP call.
            async with self.Session() as probe:
                locked = (
                    await probe.execute(
                        select(TelegramDeliveryJobRecord)
                        .where(TelegramDeliveryJobRecord.id == enqueued.job.id)
                        .with_for_update(nowait=True)
                    )
                ).scalar_one()
                observed["dispatch_started"] = locked.dispatch_started_at is not None
                await probe.rollback()
            return TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 777}},
            )

        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            report = await run_telegram_delivery_queue_cycle(
                bot_identity="primary",
                limit=1,
                freshness_validator=freshness_validator,
                gateway_call=gateway_call,
                dispatch_limiter=_AllowLimiter(),
                worker_id="worker-boundary-test",
            )

        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.status_counts, {"sent": 1})
        self.assertEqual(observed["freshness_state"], TelegramDeliveryState.LEASED)
        self.assertTrue(observed["dispatch_started"])
        self.assertEqual(observed["method"], "sendMessage")
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.SENT)
        self.assertEqual(persisted.telegram_message_id, 777)

    async def test_primary_gateway_wait_does_not_block_editor_lane_cycle(self):
        primary = await self._enqueue("lane-primary-slow")
        editor = await self._enqueue(
            "lane-editor-fast",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            destination="channel:market",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 81, "text": "expired"},
            bot_identity="channel_editor",
        )
        primary_started = asyncio.Event()
        editor_finished = asyncio.Event()

        async def freshness_validator(db, job, now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        async def primary_gateway(method, payload, **kwargs):
            primary_started.set()
            await asyncio.wait_for(editor_finished.wait(), timeout=2)
            return TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 8801}},
            )

        async def editor_gateway(method, payload, **kwargs):
            await asyncio.wait_for(primary_started.wait(), timeout=2)
            editor_finished.set()
            return TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": True},
            )

        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            primary_report, editor_report = await asyncio.gather(
                run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    gateway_call=primary_gateway,
                    dispatch_limiter=_AllowLimiter(),
                    worker_id="primary-lane-worker",
                    recover_leases=False,
                ),
                run_telegram_delivery_queue_cycle(
                    bot_identity="channel_editor",
                    limit=1,
                    freshness_validator=freshness_validator,
                    gateway_call=editor_gateway,
                    dispatch_limiter=_AllowLimiter(),
                    worker_id="editor-lane-worker",
                    recover_leases=False,
                ),
            )

        self.assertTrue(editor_finished.is_set())
        self.assertEqual(primary_report.bot_identity, "primary")
        self.assertEqual(editor_report.bot_identity, "channel_editor")
        async with self.Session() as db:
            persisted_primary = await db.get(TelegramDeliveryJobRecord, primary.job.id)
            persisted_editor = await db.get(TelegramDeliveryJobRecord, editor.job.id)
        self.assertEqual(persisted_primary.state, TelegramDeliveryState.SENT)
        self.assertEqual(persisted_editor.state, TelegramDeliveryState.SENT)

    async def test_predispatch_validator_failure_releases_unstarted_lease(self):
        enqueued = await self._enqueue("worker-validator-failure")

        async def broken_validator(db, job, now):
            raise RuntimeError("synthetic_revalidation_failure")

        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic_revalidation_failure"):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=broken_validator,
                    gateway_call=AsyncMock(),
                    dispatch_limiter=_AllowLimiter(),
                    worker_id="worker-validator-test",
                )

        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(persisted.worker_id)
        self.assertIsNone(persisted.lease_until)
        self.assertIsNone(persisted.dispatch_started_at)

    async def test_dispatch_limit_wait_defers_without_error_or_gateway_call(self):
        enqueued = await self._enqueue("worker-limiter-wait")
        gateway = AsyncMock()

        async def freshness_validator(db, job, now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            report = await run_telegram_delivery_queue_cycle(
                bot_identity="primary",
                limit=1,
                freshness_validator=freshness_validator,
                gateway_call=gateway,
                dispatch_limiter=_DenyLimiter(),
                worker_id="worker-limiter-wait-test",
            )

        self.assertEqual(report.status_counts, {"limiter_wait": 1})
        gateway.assert_not_awaited()
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(persisted.dispatch_started_at)
        self.assertIsNone(persisted.worker_id)
        self.assertIsNone(persisted.last_error_class)
        self.assertEqual(
            persisted.outcome_reason,
            "telegram_limiter_wait:destination_gate",
        )

    async def test_limiter_failure_releases_lease_and_fails_closed_before_gateway(self):
        enqueued = await self._enqueue("worker-limiter-unavailable")
        gateway = AsyncMock()

        async def freshness_validator(db, job, now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
            return_value=self._queue_runtime(),
        ):
            with self.assertRaisesRegex(
                TelegramDeliveryLimiterUnavailableError,
                "synthetic_limiter_down",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    gateway_call=gateway,
                    dispatch_limiter=_UnavailableLimiter(),
                    worker_id="worker-limiter-unavailable-test",
                )

        gateway.assert_not_awaited()
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(persisted.dispatch_started_at)
        self.assertIsNone(persisted.worker_id)
        self.assertEqual(persisted.last_error_class, "PreDispatchError")


if __name__ == "__main__":
    unittest.main()
