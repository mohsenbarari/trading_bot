import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import telegram_delivery_queue_worker as queue_worker
from core.telegram_delivery_queue_owner import (
    acquire_telegram_delivery_queue_owner,
)
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
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightIdentityReport,
    TelegramDeliveryPreflightRateLimitedError,
    TelegramDeliveryPreflightReport,
)
from core.telegram_delivery_freshness_router import (
    DURABLE_TELEGRAM_DELIVERY_ACTIONS,
    TelegramDeliveryFreshnessRegistry,
)
from core.telegram_delivery_market_freshness import (
    MARKET_NOTICE_CAMPAIGN_ID,
    MARKET_NOTICE_TEMPLATE_VERSION,
    MarketTelegramDeliveryFreshnessValidator,
    validate_market_telegram_delivery_freshness,
)
from core.telegram_delivery_offer_freshness import (
    telegram_channel_destination_key,
    validate_offer_telegram_delivery_freshness,
)
from core.telegram_gateway import TelegramGatewayResult
from core.enums import SettlementType
from core.services.offer_publication_state_service import (
    build_offer_publication_state,
)
from core.services.telegram_offer_channel_service import (
    build_offer_channel_message,
    build_offer_channel_reply_markup,
)
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryDispatchDeferredError,
    TelegramDeliveryQueueValidationError,
    TelegramDeliveryQueueSurfaceError,
    apply_telegram_delivery_freshness_result as _apply_telegram_delivery_freshness_result,
    claim_next_telegram_delivery_job,
    defer_unstarted_telegram_delivery_lease,
    enqueue_telegram_delivery_job,
    mark_telegram_delivery_dispatch_started as _mark_telegram_delivery_dispatch_started,
    apply_telegram_delivery_provider_outcome,
    record_telegram_delivery_provider_outcome,
    record_telegram_provider_outcome_apply_failure,
    recover_expired_telegram_delivery_leases,
    resolve_telegram_delivery_result as _resolve_telegram_delivery_result,
)
from core.services.telegram_delivery_reconciliation_service import (
    RECONCILIATION_CONFIRMED_ABSENT,
    reconcile_telegram_delivery_jobs,
    resolve_ambiguous_telegram_delivery_job,
)
from core.services.telegram_delivery_runtime_gate_service import (
    TELEGRAM_RUNTIME_GATE_ACTIVE,
    TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED,
    TelegramRuntimeGateConflictError,
    TelegramRuntimeGateResumeIncompleteError,
    mark_telegram_preflight_gate_active,
    record_telegram_preflight_rate_limit,
    resume_telegram_runtime_gate,
)
from core.services.telegram_callback_queue_feedback import (
    TelegramCallbackQueueLifecycleFeedback,
)
from core.services.telegram_callback_queue_service import (
    enqueue_telegram_callback_answer,
)
from core.telegram_delivery_callback_freshness import (
    validate_telegram_callback_delivery_freshness,
)
from core.services.telegram_offer_queue_service import (
    enqueue_current_offer_delivery,
    load_offer_edit_queue_candidates,
    load_offer_edit_fresh_success_counts,
    record_offer_edit_delivery_success,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_APPLIED,
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_reconciliation_evidence import (
    TelegramDeliveryReconciliationEvidence,
)
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate
from models.telegram_delivery_feeder_state import TelegramDeliveryFeederState
from models.commodity import Commodity
from models.offer import Offer, OfferStatus, OfferType
from models.offer_publication_state import (
    OfferPublicationStatus,
    OfferPublicationSurface,
)
from core.telegram_delivery_queue_worker import (
    rehydrate_telegram_delivery_limiter_state,
    run_telegram_delivery_queue_cycle,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from core.services.market_transition_service import (
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_TRANSITION_OPENED,
    MARKET_OPENED_CHANNEL_NOTICE,
    market_channel_notice_dedupe_key,
    market_channel_notice_freshness_deadline,
)
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.market_runtime_state import MarketRuntimeState


DATABASE_NAME_PATTERN = re.compile(r"^telegram_queue_stage3_[a-z0-9_]+_test$")


@dataclass(frozen=True)
class TestDatabaseUrls:
    owner_sync: str
    runtime_sync: str
    runtime_async: str


def _test_database_urls() -> TestDatabaseUrls | None:
    explicit = str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    runtime_target = make_url(explicit)
    owner_explicit = str(
        os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_OWNER_DATABASE_URL", explicit)
    ).strip()
    owner_target = make_url(owner_explicit)
    if not DATABASE_NAME_PATTERN.fullmatch(str(runtime_target.database or "").lower()):
        raise RuntimeError(
            "Telegram queue PostgreSQL tests require a telegram_queue_stage3_*_test scratch database"
        )
    if (
        owner_target.host != runtime_target.host
        or owner_target.port != runtime_target.port
        or owner_target.database != runtime_target.database
    ):
        raise RuntimeError(
            "Telegram queue owner and runtime URLs must address the same scratch database"
        )
    return TestDatabaseUrls(
        owner_sync=owner_target.set(drivername="postgresql+psycopg2").render_as_string(
            hide_password=False
        ),
        runtime_sync=runtime_target.set(drivername="postgresql+psycopg2").render_as_string(
            hide_password=False
        ),
        runtime_async=runtime_target.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )


DATABASE_URLS = _test_database_urls()


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


class _NoopLifecycleFeedback:
    async def assert_dispatchable(self, _db, _job, _now):
        return None

    async def apply_freshness(self, _db, _job, _decision, _now):
        return None

    async def apply_delivery_result(self, _db, _job, _decision, _now):
        return None


async def mark_telegram_delivery_dispatch_started(*args, **kwargs):
    feedback = _NoopLifecycleFeedback()
    kwargs.setdefault("dispatch_guard", feedback.assert_dispatchable)
    return await _mark_telegram_delivery_dispatch_started(*args, **kwargs)


async def apply_telegram_delivery_freshness_result(*args, **kwargs):
    feedback = _NoopLifecycleFeedback()
    kwargs.setdefault("feedback", feedback.apply_freshness)
    return await _apply_telegram_delivery_freshness_result(*args, **kwargs)


async def resolve_telegram_delivery_result(*args, **kwargs):
    feedback = _NoopLifecycleFeedback()
    kwargs.setdefault("feedback", feedback.apply_delivery_result)
    return await _resolve_telegram_delivery_result(*args, **kwargs)


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


class _RecordingLimiter(_AllowLimiter):
    def __init__(self):
        self.observations = []
        self.destination_extensions = []
        self.bot_extensions = []

    async def observe(self, job, decision, *, now):
        self.observations.append((job, decision, now))

    async def extend_destination_cooldown(self, job, *, until):
        self.destination_extensions.append((job, until))

    async def extend_bot_cooldown(self, bot_identity, *, until):
        self.bot_extensions.append((bot_identity, until))


class _ResumeRecordingLimiter(_RecordingLimiter):
    def __init__(self, *, fail_bot_resume: bool = False):
        super().__init__()
        self.fail_bot_resume = fail_bot_resume
        self.resumed_bots = []
        self.gateway_resume_count = 0

    async def resume_bot(self, bot_identity):
        if self.fail_bot_resume:
            raise RuntimeError("synthetic_redis_resume_failure")
        self.resumed_bots.append(bot_identity)

    async def resume_gateway(self):
        self.gateway_resume_count += 1


class _ProbeLimiter(_RecordingLimiter):
    async def acquire(self, _job, *, now):
        return TelegramDeliveryDispatchAdmission(
            allowed=True,
            is_rate_limit_probe=True,
        )


class _ObserveFailingLimiter(_AllowLimiter):
    async def observe(self, _job, _decision, *, now):
        raise TelegramDeliveryLimiterUnavailableError(
            "synthetic_observe_failure_after_commit"
        )


def _run_alembic(sync_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = os.getcwd()
    env["TRADING_BOT_EXPECTED_ALEMBIC_HEAD"] = "f764a5b6c8d9"
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

    def test_database_urls_expose_named_owner_and_runtime_endpoints(self):
        runtime = "postgresql://runtime:runtime@db/telegram_queue_stage3_named_test"
        owner = "postgresql://owner:owner@db/telegram_queue_stage3_named_test"
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL": runtime,
                "TELEGRAM_QUEUE_STAGE3_TEST_OWNER_DATABASE_URL": owner,
            },
            clear=False,
        ):
            urls = _test_database_urls()
        self.assertIsNotNone(urls)
        self.assertIn("owner:owner", urls.owner_sync)
        self.assertIn("runtime:runtime", urls.runtime_sync)
        self.assertTrue(urls.runtime_async.startswith("postgresql+asyncpg://"))
        with self.assertRaises((AttributeError, TypeError)):
            urls.runtime_sync = urls.owner_sync


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryQueuePostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _run_alembic(DATABASE_URLS.owner_sync, "upgrade", "head")

    async def asyncSetUp(self):
        self.previous_process_owner_lease = (
            queue_worker._active_process_owner_lease
        )
        queue_worker._active_process_owner_lease = SimpleNamespace(
            assert_held=AsyncMock(return_value=None)
        )
        self.engine = create_async_engine(DATABASE_URLS.runtime_async, pool_pre_ping=True)
        self.maintenance_engine = create_async_engine(
            make_url(DATABASE_URLS.owner_sync)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False),
            pool_pre_ping=True,
        )
        self.Session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        # Fixture maintenance deliberately bypasses the application Session
        # listener.  The test body still exercises the exact strict-DR
        # application path; only scratch-owner reset SQL belongs here.
        async with self.maintenance_engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE telegram_delivery_runtime_gates, "
                    "telegram_delivery_resume_operations, "
                    "telegram_delivery_jobs RESTART IDENTITY CASCADE"
                )
            )
            await connection.execute(text("ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq RESTART WITH 1"))
        # The mutable feeder singleton is reset through the real application
        # role and the exact Bot-FI database capability.  This proves the
        # hardened writer trigger is active during every Queue test instead of
        # silently weakening it for fixture setup.
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT set_config('trading_bot.mutation_capability', "
                    "'foreign_writer', true)"
                )
            )
            await connection.execute(
                text("SELECT set_config('trading_bot.physical_site', 'bot_fi', true)")
            )
            await connection.execute(
                text("SELECT set_config('trading_bot.dr_producer_epoch', '1', true)")
            )
            await connection.execute(
                text(
                    "UPDATE telegram_delivery_feeder_states "
                    "SET fresh_success_counts = '{}'::json, updated_at = now() "
                    "WHERE feeder_kind = 'offer_edit'"
                )
            )

    async def _reset_authoritative_fixture(self, *statements) -> None:
        """Reset scratch-only product rows without granting an owner bypass.

        The production write guard correctly rejects direct owner writes.  A
        fixture reset therefore disables enforcement only inside one owner
        transaction, restores it before commit, and rolls the entire change
        back if any reset statement fails.
        """

        async with self.maintenance_engine.begin() as connection:
            disabled = await connection.scalar(
                text(
                    "UPDATE dr_database_runtime SET enforcement_enabled=false "
                    "WHERE singleton_id=1 AND enforcement_enabled=true RETURNING 1"
                )
            )
            if disabled != 1:
                raise RuntimeError(
                    "scratch fixture reset requires active database enforcement"
                )
            for statement, parameters in statements:
                await connection.execute(text(statement), parameters)
            restored = await connection.scalar(
                text(
                    "UPDATE dr_database_runtime SET enforcement_enabled=true "
                    "WHERE singleton_id=1 AND enforcement_enabled=false RETURNING 1"
                )
            )
            if restored != 1:
                raise RuntimeError("scratch fixture reset failed to restore enforcement")

    async def test_two_processes_cannot_own_queue_executor_simultaneously(self):
        environment = os.environ.copy()
        environment["TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL"] = (
            make_url(DATABASE_URLS.runtime_sync)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        command = [
            sys.executable,
            "-m",
            "scripts.probe_telegram_queue_owner_lock",
        ]
        first = subprocess.Popen(
            [*command, "--hold"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        try:
            first_line = first.stdout.readline()
            if not first_line:
                self.fail(
                    "owner probe exited before readiness: "
                    + (first.stderr.read() if first.stderr is not None else "")
                )
            self.assertTrue(json.loads(first_line)["acquired"])
            second = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            self.assertEqual(second.returncode, 3, second.stderr)
            self.assertFalse(json.loads(second.stdout)["acquired"])
        finally:
            if first.stdin is not None and first.poll() is None:
                first.stdin.write("release\n")
                first.stdin.flush()
            first.wait(timeout=10)
            for stream in (first.stdin, first.stdout, first.stderr):
                if stream is not None:
                    stream.close()
        third = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertTrue(json.loads(third.stdout)["acquired"])

    async def test_process_owner_lease_verifies_real_session_lock(self):
        lease = await acquire_telegram_delivery_queue_owner(self.engine)
        try:
            await lease.assert_held()
        finally:
            await lease.close()

    async def asyncTearDown(self):
        queue_worker._active_process_owner_lease = (
            self.previous_process_owner_lease
        )
        await self.engine.dispose()
        await self.maintenance_engine.dispose()

    @staticmethod
    def _enqueue_kwargs(
        source_id: str,
        *,
        source_version: int = 1,
        feeder: TelegramFeederKind = TelegramFeederKind.DIRECT,
        action: TelegramDeliveryAction = TelegramDeliveryAction.GENERAL_IMMEDIATE,
        destination: str = "private:1001",
        destination_class: TelegramDestinationClass = TelegramDestinationClass.PRIVATE,
        method: str = "sendMessage",
        payload: dict | None = None,
        deadline=None,
        freshness_deadline=None,
        eligible=None,
        bot_identity: str = "primary",
        template_version: str = "test-v1",
        campaign_id: str | None = None,
        run_id: str | None = "stage3-foundation-test",
        source_order_at=None,
    ) -> dict:
        if feeder == TelegramFeederKind.OFFER_EDIT and source_order_at is None:
            source_order_at = eligible or utc_now()
        return {
            "current_server": "foreign",
            "feeder": feeder,
            "source_natural_id": source_id,
            "source_version": source_version,
            "action": action,
            "bot_identity": bot_identity,
            "destination_key": destination,
            "destination_class": destination_class,
            "method": method,
            "payload": payload or {"chat_id": 1001, "text": source_id},
            "template_version": template_version,
            "delivery_deadline_at": deadline,
            "freshness_deadline_at": freshness_deadline,
            "eligible_at": eligible,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "source_order_at": source_order_at,
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

    async def _persist_hard_pause(
        self,
        source_id: str,
        *,
        status_code: int,
        description: str,
        bot_identity: str = "primary",
    ):
        enqueued = await self._enqueue(
            source_id,
            bot_identity=bot_identity,
            destination=f"private:{source_id}",
        )
        now = utc_now()
        claimed = await self._claim(
            f"{source_id}-worker",
            now=now,
            bot_identity=bot_identity,
        )
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=claimed.id,
                    worker_id=claimed.worker_id,
                    lease_token=claimed.lease_token,
                    now=now,
                )
            )
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=claimed.id,
                worker_id=claimed.worker_id,
                lease_token=claimed.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=status_code,
                    response_json={
                        "ok": False,
                        "error_code": status_code,
                        "description": description,
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now,
            )
            await db.commit()
        return enqueued.job, decision

    async def test_physical_rate_limit_gate_schema_matches_model(self):
        async with self.Session() as db:
            column = (
                await db.execute(
                    text(
                        "SELECT is_nullable, column_default "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = 'telegram_delivery_jobs' "
                        "AND column_name = 'rate_limit_probe'"
                    )
                )
            ).mappings().one()
            indexes = {
                row["indexname"]: row["indexdef"]
                for row in (
                    await db.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE schemaname = 'public' "
                            "AND tablename = 'telegram_delivery_jobs'"
                        )
                    )
                ).mappings()
            }

        self.assertEqual(column["is_nullable"], "NO")
        self.assertEqual(column["column_default"], "false")
        expected_partial_indexes = {
            "ix_telegram_delivery_jobs_destination_gate",
            "ix_telegram_delivery_jobs_hard_pause_gate",
            "ix_telegram_delivery_jobs_bot_cooldown",
            "ix_telegram_delivery_jobs_recent_rate_limit",
            "ix_telegram_delivery_jobs_bot_probe_gate",
        }
        self.assertTrue(expected_partial_indexes.issubset(indexes))
        for name in expected_partial_indexes:
            self.assertIn(" WHERE ", indexes[name])

    async def test_provider_outcome_is_fenced_idempotent_and_conflict_safe(self):
        now = utc_now()
        enqueued = await self._enqueue("provider-outcome-fence")
        claimed = await self._claim("provider-worker", now=now)
        async with self.Session() as db:
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="provider-worker",
                lease_token=claimed.lease_token,
                now=now,
            )
            self.assertTrue(marked)
            await db.commit()

        result = TelegramGatewayResult(
            ok=False,
            method="sendMessage",
            status_code=429,
            response_json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 3},
            },
            transport_phase="response_received",
        )
        async with self.Session() as db:
            first = await record_telegram_delivery_provider_outcome(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="provider-worker",
                lease_token=claimed.lease_token,
                result=result,
                now=now,
            )
            first_id = int(first.outcome.id)
            await db.commit()
        self.assertTrue(first.created)
        self.assertEqual(first.outcome.transport_phase, "response_received")

        async with self.Session() as db:
            replay = await record_telegram_delivery_provider_outcome(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="provider-worker",
                lease_token=claimed.lease_token,
                result=result,
                now=now,
            )
            await db.commit()
        self.assertFalse(replay.created)
        self.assertEqual(int(replay.outcome.id), first_id)

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "provider_outcome_fence_conflict",
            ):
                await record_telegram_delivery_provider_outcome(
                    db,
                    current_server="foreign",
                    job_id=enqueued.job.id,
                    worker_id="provider-worker",
                    lease_token=claimed.lease_token,
                    result=TelegramGatewayResult(
                        ok=False,
                        method="sendMessage",
                        status_code=500,
                        response_json={"ok": False, "error_code": 500},
                    ),
                    now=now,
                )
            await db.rollback()

        async with self.Session() as db:
            outcomes = list(
                (
                    await db.execute(
                        select(TelegramDeliveryProviderOutcomeRecord).where(
                            TelegramDeliveryProviderOutcomeRecord.job_id == enqueued.job.id
                        )
                    )
                ).scalars()
            )
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].apply_state, TELEGRAM_PROVIDER_OUTCOME_PENDING)

    async def test_provider_outcome_survives_feedback_failure_and_recovery_skips_it(self):
        now = utc_now()
        enqueued = await self._enqueue("provider-outcome-replay")
        claimed = await self._claim("provider-worker", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="provider-worker",
                lease_token=claimed.lease_token,
                now=now,
            )
            persisted = await record_telegram_delivery_provider_outcome(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="provider-worker",
                lease_token=claimed.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 3},
                    },
                ),
                now=now,
            )
            outcome_id = int(persisted.outcome.id)
            await db.commit()

        async def failing_feedback(_db, _job, _decision, _now):
            raise RuntimeError("synthetic_feedback_failure")

        async with self.Session() as db:
            with self.assertRaisesRegex(RuntimeError, "synthetic_feedback_failure"):
                await apply_telegram_delivery_provider_outcome(
                    db,
                    current_server="foreign",
                    outcome_id=outcome_id,
                    retry_after_safety_seconds=0.1,
                    retry_base_seconds=1.0,
                    retry_max_seconds=30.0,
                    global_rate_limit_window_seconds=2.0,
                    feedback=failing_feedback,
                    now=now,
                )
            await db.rollback()

        async with self.Session() as db:
            await record_telegram_provider_outcome_apply_failure(
                db,
                current_server="foreign",
                outcome_id=outcome_id,
                error=RuntimeError("synthetic_feedback_failure"),
                now=now,
            )
            recovery = await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=now + timedelta(minutes=2),
            )
            await db.commit()
        self.assertEqual(recovery.job_ids, ())

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
            outcome = await db.get(TelegramDeliveryProviderOutcomeRecord, outcome_id)
            self.assertEqual(job.state, TelegramDeliveryState.LEASED)
            self.assertEqual(outcome.apply_state, TELEGRAM_PROVIDER_OUTCOME_PENDING)
            self.assertEqual(outcome.apply_attempt_count, 1)

        async with self.Session() as db:
            decision = await apply_telegram_delivery_provider_outcome(
                db,
                current_server="foreign",
                outcome_id=outcome_id,
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1.0,
                retry_max_seconds=30.0,
                global_rate_limit_window_seconds=2.0,
                feedback=_NoopLifecycleFeedback().apply_delivery_result,
                now=now + timedelta(seconds=2),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
            outcome = await db.get(TelegramDeliveryProviderOutcomeRecord, outcome_id)
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertEqual(outcome.apply_state, TELEGRAM_PROVIDER_OUTCOME_APPLIED)
        self.assertIsNotNone(outcome.applied_at)

    async def test_provider_outcome_initial_database_outage_retries_to_one_durable_fact(self):
        cases = (
            (
                "provider-outcome-initial-db-outage-success",
                TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={
                        "ok": True,
                        "result": {"message_id": 71001},
                    },
                ),
                TelegramDeliveryOutcome.SENT,
                TelegramDeliveryState.SENT,
            ),
            (
                "provider-outcome-initial-db-outage-429",
                TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 3},
                    },
                ),
                TelegramDeliveryOutcome.RETRY_PENDING,
                TelegramDeliveryState.PENDING_RETRY,
            ),
        )

        for index, (source_id, gateway_result, expected_outcome, expected_state) in enumerate(cases):
            with self.subTest(source_id=source_id):
                enqueued = await self._enqueue(
                    source_id,
                    destination=f"private:{9100 + index}",
                    payload={"chat_id": 9100 + index, "text": source_id},
                )
                claimed = await self._claim(f"provider-outage-worker-{index}")
                async with self.Session() as db:
                    await mark_telegram_delivery_dispatch_started(
                        db,
                        current_server="foreign",
                        job_id=enqueued.job.id,
                        worker_id=f"provider-outage-worker-{index}",
                        lease_token=claimed.lease_token,
                    )
                    await db.commit()

                real_record = queue_worker.record_telegram_delivery_provider_outcome
                attempts = 0

                async def flaky_record(*args, **kwargs):
                    nonlocal attempts
                    attempts += 1
                    if attempts <= 4:
                        raise OperationalError(
                            "INSERT telegram_delivery_provider_outcomes",
                            {},
                            OSError("synthetic_initial_database_outage"),
                        )
                    return await real_record(*args, **kwargs)

                with (
                    patch.object(queue_worker, "AsyncSessionLocal", self.Session),
                    patch.object(queue_worker, "current_server", return_value="foreign"),
                    patch.object(
                        queue_worker,
                        "record_telegram_delivery_provider_outcome",
                        side_effect=flaky_record,
                    ),
                    patch.object(queue_worker.asyncio, "sleep", new=AsyncMock()),
                ):
                    decision, provider_decision = (
                        await queue_worker._persist_delivery_result_after_dispatch(
                            bot_identity="primary",
                            job_id=enqueued.job.id,
                            worker_id=f"provider-outage-worker-{index}",
                            lease_token=claimed.lease_token,
                            gateway_result=gateway_result,
                            feedback=_NoopLifecycleFeedback().apply_delivery_result,
                        )
                    )

                self.assertEqual(attempts, 5)
                self.assertEqual(decision.outcome, expected_outcome)
                self.assertEqual(provider_decision.outcome, expected_outcome)
                async with self.Session() as db:
                    job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
                    outcomes = list(
                        (
                            await db.execute(
                                select(TelegramDeliveryProviderOutcomeRecord).where(
                                    TelegramDeliveryProviderOutcomeRecord.job_id
                                    == enqueued.job.id
                                )
                            )
                        ).scalars()
                    )
                self.assertEqual(job.state, expected_state)
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(
                    outcomes[0].apply_state,
                    TELEGRAM_PROVIDER_OUTCOME_APPLIED,
                )
                self.assertEqual(outcomes[0].apply_attempt_count, 0)

    async def test_reconciler_revalidates_idempotent_edit_before_safe_retry(self):
        now = utc_now()
        enqueued = await self._enqueue(
            "reconcile-edit",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
            destination="channel:-1001",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 10, "text": "latest"},
            bot_identity="channel_editor",
        )
        claimed = await self._claim(
            "editor-worker",
            now=now,
            bot_identity="channel_editor",
        )
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="editor-worker",
                lease_token=claimed.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            recovery = await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=now + timedelta(minutes=2),
            )
            await db.commit()
        self.assertEqual(recovery.pending_reconcile, 1)

        async def current_freshness(_db, _job, _now):
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.SEND,
                reason="authoritative_edit_still_current",
            )

        feedback = _NoopLifecycleFeedback()
        async with self.Session() as db:
            report = await reconcile_telegram_delivery_jobs(
                db,
                current_server="foreign",
                freshness_validators={"channel_editor": current_freshness},
                freshness_feedbacks={
                    "channel_editor": feedback.apply_freshness
                },
                result_feedbacks={
                    "channel_editor": feedback.apply_delivery_result
                },
                max_rows=10,
                now=now + timedelta(minutes=2, seconds=1),
            )
            await db.commit()
        self.assertEqual(report.safe_retry_count, 1)

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
            evidence_count = await db.scalar(
                select(func.count(TelegramDeliveryReconciliationEvidence.id)).where(
                    TelegramDeliveryReconciliationEvidence.job_id == enqueued.job.id
                )
            )
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertIsNone(job.dispatch_started_at)
        self.assertEqual(evidence_count, 1)

    async def test_ambiguous_send_escalates_and_only_audited_positive_absence_retries(self):
        now = utc_now()
        enqueued = await self._enqueue("reconcile-ambiguous-send")
        claimed = await self._claim("send-worker", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="send-worker",
                lease_token=claimed.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            await recover_expired_telegram_delivery_leases(
                db,
                current_server="foreign",
                max_rows=10,
                now=now + timedelta(minutes=2),
            )
            await db.commit()

        feedback = _NoopLifecycleFeedback()
        async with self.Session() as db:
            report = await reconcile_telegram_delivery_jobs(
                db,
                current_server="foreign",
                freshness_validators={},
                freshness_feedbacks={},
                result_feedbacks={"primary": feedback.apply_delivery_result},
                ambiguity_grace_seconds=1,
                max_rows=10,
                now=now + timedelta(minutes=2, seconds=2),
            )
            await db.commit()
        self.assertEqual(report.unresolved_count, 1)

        async with self.Session() as db:
            dry_run = await resolve_ambiguous_telegram_delivery_job(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                resolution=RECONCILIATION_CONFIRMED_ABSENT,
                evidence_reference="operator-channel-audit-42",
                operator_reference="operator@example.test",
                reason_code="confirmed_not_visible",
                dry_run=True,
                now=now + timedelta(minutes=3),
            )
            await db.rollback()
        self.assertEqual(dry_run.outcome, TelegramDeliveryOutcome.RETRY_PENDING)

        async with self.Session() as db:
            before = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
            self.assertEqual(before.state, TelegramDeliveryState.AMBIGUOUS_UNRESOLVED)

        async with self.Session() as db:
            resolved = await resolve_ambiguous_telegram_delivery_job(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                resolution=RECONCILIATION_CONFIRMED_ABSENT,
                evidence_reference="operator-channel-audit-42",
                operator_reference="operator@example.test",
                reason_code="confirmed_not_visible",
                now=now + timedelta(minutes=3),
            )
            await db.commit()
        self.assertEqual(resolved.outcome, TelegramDeliveryOutcome.RETRY_PENDING)

        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
            evidence = list(
                (
                    await db.execute(
                        select(TelegramDeliveryReconciliationEvidence)
                        .where(
                            TelegramDeliveryReconciliationEvidence.job_id
                            == enqueued.job.id
                        )
                        .order_by(TelegramDeliveryReconciliationEvidence.id)
                    )
                ).scalars()
            )
        self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertEqual(len(evidence), 2)
        self.assertNotIn("operator@example.test", str(evidence[-1].details))
        self.assertNotEqual(evidence[-1].actor_ref_hash, "operator@example.test")

    async def test_offer_edit_fairness_counter_survives_transactions_and_resets_on_stale(self):
        now = utc_now()
        for index in range(21):
            enqueued = await self._enqueue(
                f"fairness-fresh-{index}",
                feeder=TelegramFeederKind.OFFER_EDIT,
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                destination="channel:fairness",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="editMessageText",
                payload={
                    "chat_id": -1001,
                    "message_id": index + 1,
                    "text": f"fresh-{index}",
                },
                eligible=now - timedelta(seconds=30),
            )
            async with self.Session() as db:
                job = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
                await record_offer_edit_delivery_success(db, job, now=now)
                await db.commit()

        async with self.Session() as db:
            counts = await load_offer_edit_fresh_success_counts(db)
            state = await db.get(
                TelegramDeliveryFeederState,
                TelegramFeederKind.OFFER_EDIT.value,
            )
        self.assertEqual(counts[0], 20)
        self.assertEqual(state.fresh_success_counts, {"0": 20})

        stale = await self._enqueue(
            "fairness-stale",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            destination="channel:fairness",
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={
                "chat_id": -1001,
                "message_id": 999,
                "text": "stale",
            },
            eligible=now - timedelta(minutes=6),
        )
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, stale.job.id)
            await record_offer_edit_delivery_success(db, job, now=now)
            await db.commit()
        async with self.Session() as db:
            counts = await load_offer_edit_fresh_success_counts(db)
        self.assertEqual(counts[0], 0)

    async def test_offer_feeder_candidate_selection_uses_postgres_time_under_host_skew(self):
        public_id = "ofr_feeder_db_clock_pg"
        commodity_name = "feeder-db-clock-commodity"
        channel_id = -100123456701
        await self._reset_authoritative_fixture(
            ("DELETE FROM offer_publication_states WHERE offer_public_id = :id", {"id": public_id}),
            ("DELETE FROM offers WHERE offer_public_id = :id", {"id": public_id}),
            ("DELETE FROM commodities WHERE name = :name", {"name": commodity_name}),
        )
        async with self.Session() as db:
            commodity = Commodity(name=commodity_name)
            db.add(commodity)
            await db.flush()
            offer = Offer(
                offer_public_id=public_id,
                home_server="foreign",
                offer_type=OfferType.SELL,
                settlement_type=SettlementType.CASH,
                commodity_id=commodity.id,
                commodity=commodity,
                quantity=10,
                remaining_quantity=5,
                price=72_500_000,
                is_wholesale=True,
                status=OfferStatus.ACTIVE,
            )
            db.add(offer)
            await db.flush()
            state = build_offer_publication_state(
                offer,
                OfferPublicationSurface.TELEGRAM_CHANNEL,
                status=OfferPublicationStatus.SENT,
            )
            state.offer_version_id = max(0, int(offer.version_id) - 1)
            state.surface_resource_id = "901"
            state.telegram_chat_id = channel_id
            state.telegram_message_id = 901
            db.add(state)
            await db.commit()

        for host_skew in (-timedelta(hours=24), timedelta(hours=24)):
            with patch(
                "core.utils.utc_now",
                return_value=utc_now() + host_skew,
            ):
                async with self.Session() as db:
                    candidates = await load_offer_edit_queue_candidates(
                        db,
                        limit=25,
                    )
                    candidate_public_ids = {
                        candidate.offer.offer_public_id for candidate in candidates
                    }
                    await db.rollback()
            self.assertIn(
                public_id,
                candidate_public_ids,
            )

    async def test_offer_publication_deadline_edges_ignore_host_clock_skew(self):
        commodity_name = "publication-db-deadline-commodity"
        await self._reset_authoritative_fixture(
            (
                "DELETE FROM offer_publication_states "
                "WHERE offer_public_id IN ('ofr_db_deadline_edge_1', 'ofr_db_deadline_edge_2')",
                {},
            ),
            (
                "DELETE FROM offers "
                "WHERE offer_public_id IN ('ofr_db_deadline_edge_1', 'ofr_db_deadline_edge_2')",
                {},
            ),
            ("DELETE FROM commodities WHERE name = :name", {"name": commodity_name}),
        )
        async with self.Session() as db:
            commodity = Commodity(name=commodity_name)
            db.add(commodity)
            await db.flush()
            database_now = (
                await db.execute(select(func.clock_timestamp()))
            ).scalar_one()
            results = []
            for index, deadline_offset in enumerate((1, -1), start=1):
                offer = Offer(
                    offer_public_id=f"ofr_db_deadline_edge_{index}",
                    home_server="foreign",
                    offer_type=OfferType.SELL,
                    settlement_type=SettlementType.CASH,
                    commodity_id=commodity.id,
                    commodity=commodity,
                    quantity=10,
                    remaining_quantity=10,
                    price=72_500_000,
                    is_wholesale=True,
                    status=OfferStatus.ACTIVE,
                )
                db.add(offer)
                await db.flush()
                state = build_offer_publication_state(
                    offer,
                    OfferPublicationSurface.TELEGRAM_CHANNEL,
                    status=OfferPublicationStatus.PENDING,
                )
                db.add(state)
                await db.flush()
                with patch(
                    "core.utils.utc_now",
                    return_value=database_now + timedelta(hours=24),
                ):
                    results.append(
                        await enqueue_current_offer_delivery(
                            db,
                            current_server="foreign",
                            offer=offer,
                            state=state,
                            expected_channel_id=-100123456701,
                            offer_expiry_minutes=2,
                            publication_freshness_deadline_at=(
                                database_now + timedelta(seconds=deadline_offset)
                            ),
                        )
                    )
            await db.commit()
        self.assertIsNotNone(results[0].queue_result)
        self.assertIsNone(results[1].queue_result)
        self.assertEqual(
            results[1].skipped_reason,
            "offer_publication_deadline_passed",
        )

    async def test_offer_edit_claim_is_globally_newest_first_across_feeder_cycles(self):
        now = utc_now()
        expected = []
        for source_id, source_order_at in (
            ("cycle-one-old", now - timedelta(minutes=4)),
            ("cycle-one-new", now - timedelta(minutes=2)),
            ("cycle-two-old", now - timedelta(minutes=3)),
            ("cycle-two-new", now - timedelta(minutes=1)),
        ):
            await self._enqueue(
                source_id,
                feeder=TelegramFeederKind.OFFER_EDIT,
                action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                destination="channel:global-newest",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="editMessageText",
                payload={"chat_id": -1001, "message_id": len(expected) + 1, "text": source_id},
                eligible=now - timedelta(seconds=30),
                source_order_at=source_order_at,
                bot_identity="channel_editor",
            )
            expected.append(source_id)

        claimed = []
        for index in range(4):
            job = await self._claim(
                f"global-newest-worker-{index}",
                now=now,
                bot_identity="channel_editor",
            )
            self.assertIsNotNone(job)
            claimed.append(job.source_natural_id)
        self.assertEqual(
            claimed,
            ["cycle-two-new", "cycle-one-new", "cycle-two-old", "cycle-one-old"],
        )

    async def test_offer_edit_claim_moves_stale_behind_fresh_then_keeps_newest_first(self):
        now = utc_now()
        fixtures = (
            ("fresh-older", now - timedelta(minutes=2), now - timedelta(seconds=20)),
            ("fresh-newer", now - timedelta(minutes=1), now - timedelta(seconds=10)),
            ("stale-older", now - timedelta(minutes=4), now - timedelta(minutes=7)),
            ("stale-newer", now, now - timedelta(minutes=6)),
        )
        for index, (source_id, source_order_at, eligible) in enumerate(fixtures):
            await self._enqueue(
                source_id,
                feeder=TelegramFeederKind.OFFER_EDIT,
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                destination="channel:stale-order",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="editMessageText",
                payload={"chat_id": -1001, "message_id": index + 1, "text": source_id},
                eligible=eligible,
                source_order_at=source_order_at,
                bot_identity="channel_editor",
            )

        claimed = []
        for index in range(4):
            job = await self._claim(
                f"stale-order-worker-{index}",
                now=now,
                bot_identity="channel_editor",
            )
            claimed.append(job.source_natural_id)
        self.assertEqual(
            claimed,
            ["fresh-newer", "fresh-older", "stale-newer", "stale-older"],
        )

    async def test_offer_edit_claim_reserves_stale_catch_up_after_twenty_fresh_successes(self):
        now = utc_now()
        async with self.Session() as db:
            await db.execute(
                text(
                    "UPDATE telegram_delivery_feeder_states "
                    "SET fresh_success_counts = '{\"0\": 20}'::json "
                    "WHERE feeder_kind = 'offer_edit'"
                )
            )
            await db.commit()
        for source_id, source_order_at, eligible in (
            ("catch-up-fresh", now, now - timedelta(seconds=5)),
            ("catch-up-stale", now - timedelta(minutes=10), now - timedelta(minutes=6)),
        ):
            await self._enqueue(
                source_id,
                feeder=TelegramFeederKind.OFFER_EDIT,
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                destination="channel:catch-up-order",
                destination_class=TelegramDestinationClass.CHANNEL,
                method="editMessageText",
                payload={"chat_id": -1001, "message_id": 1, "text": source_id},
                eligible=eligible,
                source_order_at=source_order_at,
                bot_identity="channel_editor",
            )

        claimed = await self._claim(
            "catch-up-order-worker",
            now=now,
            bot_identity="channel_editor",
        )
        self.assertEqual(claimed.source_natural_id, "catch-up-stale")

    @staticmethod
    def _queue_runtime():
        return TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )

    @staticmethod
    def _credential_registry(*, editor_enabled: bool = False):
        return TelegramDeliveryCredentialRegistry.from_values(
            primary_token=":".join(
                ("123456", "test-primary-runtime-gate-token")
            ),
            editor_enabled=editor_enabled,
            editor_token=(
                ":".join(("654321", "test-editor-runtime-gate-token"))
                if editor_enabled
                else None
            ),
        )

    @staticmethod
    def _preflight_report(
        registry: TelegramDeliveryCredentialRegistry,
        identities: tuple[str, ...],
    ) -> TelegramDeliveryPreflightReport:
        fingerprints = registry.fingerprints()
        channel_fingerprint = "test-channel-fingerprint"
        return TelegramDeliveryPreflightReport(
            approved_bot_identities=identities,
            channel_fingerprint=channel_fingerprint,
            identities=tuple(
                TelegramDeliveryPreflightIdentityReport(
                    bot_identity=identity,
                    credential_fingerprint=fingerprints[identity],
                    bot_fingerprint=f"test-bot-{identity}",
                    channel_fingerprint=channel_fingerprint,
                    member_status="administrator",
                    effective_permissions=(
                        (
                            "can_manage_chat",
                            "can_restrict_members",
                            "can_post_messages",
                            "can_edit_messages",
                        )
                        if identity == "primary"
                        else ("can_manage_chat", "can_edit_messages")
                    ),
                )
                for identity in identities
            ),
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

    async def test_direct_callback_enqueue_is_idempotent_and_hides_query_identity(self):
        received_at = utc_now()
        async with self.Session() as db:
            first = await enqueue_telegram_callback_answer(
                db,
                current_server="foreign",
                callback_query_id="private-callback-query-id",
                received_at=received_at,
                text="پاسخ معمول",
            )
            await db.commit()
        async with self.Session() as db:
            replay = await enqueue_telegram_callback_answer(
                db,
                current_server="foreign",
                callback_query_id="private-callback-query-id",
                received_at=received_at,
                text="پاسخ معمول",
            )
            await db.commit()

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.job.id, replay.job.id)
        self.assertEqual(first.job.priority, 0)
        self.assertEqual(first.job.priority_rank, 0)
        self.assertEqual(first.job.method, "answerCallbackQuery")
        self.assertNotIn(
            "private-callback-query-id",
            first.job.source_natural_id,
        )
        self.assertNotIn(
            "private-callback-query-id",
            first.job.destination_key,
        )
        self.assertEqual(
            first.job.payload["callback_query_id"],
            "private-callback-query-id",
        )

    async def test_callback_lifecycle_expires_at_deadline_and_requires_feedback(self):
        received_at = utc_now() - timedelta(seconds=11)
        async with self.Session() as db:
            enqueued = await enqueue_telegram_callback_answer(
                db,
                current_server="foreign",
                callback_query_id="expired-callback-query-id",
                received_at=received_at,
                action=TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
            )
            await db.commit()
        claimed = await self._claim("callback-expired-worker", now=utc_now())
        decision = await validate_telegram_callback_delivery_freshness(
            None,
            claimed,
            utc_now(),
        )
        self.assertEqual(
            decision.outcome,
            TelegramFreshnessOutcome.EXPIRED_INTERACTION,
        )
        feedback = TelegramCallbackQueueLifecycleFeedback()
        async with self.Session() as db:
            applied = await _apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="callback-expired-worker",
                lease_token=claimed.lease_token,
                decision=decision,
                feedback=feedback.apply_freshness,
                now=utc_now(),
            )
            await db.commit()
        self.assertFalse(applied)
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                enqueued.job.id,
            )
        self.assertEqual(
            persisted.state,
            TelegramDeliveryState.EXPIRED_INTERACTION,
        )

    async def test_callback_success_requires_guard_and_finishes_without_message_id(self):
        received_at = utc_now()
        async with self.Session() as db:
            enqueued = await enqueue_telegram_callback_answer(
                db,
                current_server="foreign",
                callback_query_id="successful-callback-query-id",
                received_at=received_at,
            )
            await db.commit()
        claimed = await self._claim("callback-success-worker", now=received_at)
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "callback_dispatch_guard_required",
            ):
                await _mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=enqueued.job.id,
                    worker_id="callback-success-worker",
                    lease_token=claimed.lease_token,
                    now=received_at,
                )
            await db.rollback()

        feedback = TelegramCallbackQueueLifecycleFeedback()
        async with self.Session() as db:
            marked = await _mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="callback-success-worker",
                lease_token=claimed.lease_token,
                dispatch_guard=feedback.assert_dispatchable,
                now=received_at,
            )
            await db.commit()
        self.assertTrue(marked)

        async with self.Session() as db:
            decision = await _resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="callback-success-worker",
                lease_token=claimed.lease_token,
                result=TelegramGatewayResult(
                    ok=True,
                    method="answerCallbackQuery",
                    status_code=200,
                    response_json={"ok": True, "result": True},
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=received_at,
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                enqueued.job.id,
            )
        self.assertEqual(persisted.state, TelegramDeliveryState.SENT)
        self.assertIsNone(persisted.telegram_message_id)

    async def test_provider_query_too_old_terminals_callback_as_expired(self):
        received_at = utc_now()
        async with self.Session() as db:
            enqueued = await enqueue_telegram_callback_answer(
                db,
                current_server="foreign",
                callback_query_id="provider-expired-callback-query-id",
                received_at=received_at,
            )
            await db.commit()
        claimed = await self._claim("callback-provider-expired", now=received_at)
        feedback = TelegramCallbackQueueLifecycleFeedback()
        async with self.Session() as db:
            marked = await _mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="callback-provider-expired",
                lease_token=claimed.lease_token,
                dispatch_guard=feedback.assert_dispatchable,
                now=received_at,
            )
            await db.commit()
        self.assertTrue(marked)

        async with self.Session() as db:
            decision = await _resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=enqueued.job.id,
                worker_id="callback-provider-expired",
                lease_token=claimed.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="answerCallbackQuery",
                    status_code=400,
                    response_json={
                        "ok": False,
                        "error_code": 400,
                        "description": (
                            "Bad Request: query is too old and response timeout "
                            "expired or query ID is invalid"
                        ),
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                feedback=feedback.apply_delivery_result,
                now=received_at,
            )
            await db.commit()
        self.assertEqual(
            decision.outcome,
            TelegramDeliveryOutcome.EXPIRED_INTERACTION,
        )
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                enqueued.job.id,
            )
        self.assertEqual(
            persisted.state,
            TelegramDeliveryState.EXPIRED_INTERACTION,
        )

    async def test_otp_payload_is_rejected_before_durable_insert(self):
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryQueueValidationError,
                "telegram_action_forbidden_in_durable_queue",
            ):
                await enqueue_telegram_delivery_job(
                    db,
                    **self._enqueue_kwargs(
                        "otp-must-not-persist",
                        action=TelegramDeliveryAction.OTP_DEADLINE,
                        payload={
                            "chat_id": 1001,
                            "text": "sensitive one-time code",
                        },
                        deadline=utc_now() + timedelta(seconds=30),
                    ),
                )
            await db.rollback()
        async with self.Session() as db:
            count = await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.source_natural_id
                    == "otp-must-not-persist"
                )
            )
        self.assertEqual(count, 0)

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
        async with self.engine.connect() as connection:
            dedupe_length = await connection.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'telegram_delivery_jobs' AND column_name = 'dedupe_key'"
                )
            )
            constraint_names = set(
                (
                    await connection.execute(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE conrelid = 'telegram_delivery_jobs'::regclass"
                        )
                    )
                ).scalars()
            )
            claim_index_columns = await connection.scalar(
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

    async def test_reserved_claim_only_takes_effective_m0_work(self):
        now = utc_now()
        await self._enqueue(
            "lower-priority-general",
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            destination="private:general-priority",
        )
        async with self.Session() as db:
            no_m0 = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="reserved-m0-empty",
                request_timeout_seconds=10,
                lease_seconds=30,
                maximum_effective_priority=0,
                now=now,
            )
            await db.rollback()
        self.assertIsNone(no_m0)

        overdue = await self._enqueue(
            "overdue-trade-for-reserved-slot",
            feeder=TelegramFeederKind.TRADE,
            action=TelegramDeliveryAction.TRADE_RESULT,
            destination="private:trade-recipient",
            deadline=now - timedelta(milliseconds=1),
        )
        async with self.Session() as db:
            claimed = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="reserved-m0-worker",
                request_timeout_seconds=10,
                lease_seconds=30,
                maximum_effective_priority=0,
                now=now,
            )
            await db.commit()
        self.assertEqual(claimed.id, overdue.job.id)

    async def test_host_clock_skew_cannot_claim_or_recover_against_database_time(self):
        async with self.Session() as db:
            database_now = (
                await db.execute(select(func.clock_timestamp()))
            ).scalar_one()
        future = await self._enqueue(
            "database-clock-future-job",
            eligible=database_now + timedelta(seconds=20),
        )
        with patch(
            "core.services.telegram_delivery_queue_service.utc_now",
            create=True,
            return_value=database_now + timedelta(days=365),
        ):
            claimed = await self._claim("host-clock-far-future")
        self.assertIsNone(claimed)

        async with self.Session() as db:
            record = await db.get(TelegramDeliveryJobRecord, future.job.id)
            record.state = TelegramDeliveryState.LEASED
            record.worker_id = "host-clock-skew-worker"
            record.lease_token = 1
            record.lease_until = database_now + timedelta(seconds=20)
            record.dispatch_started_at = None
            await db.commit()
        with patch(
            "core.services.telegram_delivery_queue_service.utc_now",
            create=True,
            return_value=database_now + timedelta(days=365),
        ):
            async with self.Session() as db:
                report = await recover_expired_telegram_delivery_leases(
                    db,
                    current_server="foreign",
                    max_rows=10,
                )
                await db.commit()
        self.assertEqual(report.job_ids, ())
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, future.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.LEASED)
        self.assertEqual(persisted.worker_id, "host-clock-skew-worker")

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

    async def test_admission_attempts_are_distinct_from_provider_attempts(self):
        await self._enqueue("provider-attempt-accounting")
        now = utc_now()
        first = await self._claim("admission-worker-1", now=now)
        self.assertEqual(first.attempt_count, 1)
        self.assertEqual(first.provider_attempt_count, 0)
        async with self.Session() as db:
            deferred = await defer_unstarted_telegram_delivery_lease(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                retry_seconds=0.001,
                reason="synthetic_limiter_wait",
                now=now,
            )
            await db.commit()
        self.assertTrue(deferred)

        second = await self._claim(
            "admission-worker-2",
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(second.attempt_count, 2)
        self.assertEqual(second.provider_attempt_count, 0)
        async with self.Session() as db:
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=second.id,
                worker_id=second.worker_id,
                lease_token=second.lease_token,
                now=now + timedelta(seconds=1),
            )
            await db.commit()
        self.assertTrue(marked)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, second.id)
            self.assertEqual(persisted.attempt_count, 2)
            self.assertEqual(persisted.provider_attempt_count, 1)

    async def test_dispatch_marker_rechecks_lease_after_scope_lock_wait(self):
        await self._enqueue("dispatch-marker-expired-after-lock")
        claimed_at = utc_now()
        job = await self._claim("worker-expired-after-lock", now=claimed_at)
        with patch(
            "core.services.telegram_delivery_queue_service.telegram_delivery_database_now",
            new=AsyncMock(
                side_effect=(claimed_at, claimed_at + timedelta(seconds=31))
            ),
        ):
            async with self.Session() as db:
                marked = await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=job.id,
                    worker_id=job.worker_id,
                    lease_token=job.lease_token,
                )
                await db.commit()

        self.assertFalse(marked)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
        self.assertIsNone(persisted.dispatch_started_at)

    async def test_durable_destination_gate_covers_inflight_and_committed_429(self):
        await self._enqueue("durable-gate-first", destination="private:rate-gate")
        await self._enqueue("durable-gate-second", destination="private:rate-gate")
        now = utc_now()
        first = await self._claim("durable-gate-worker-1", now=now)
        async with self.Session() as db:
            marked = await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                now=now,
            )
            await db.commit()
        self.assertTrue(marked)

        second = await self._claim(
            "durable-gate-worker-2",
            now=now + timedelta(milliseconds=10),
        )
        self.assertIsNone(second)
        async with self.Session() as db:
            second = (
                await db.execute(
                    select(TelegramDeliveryJobRecord)
                    .where(
                        TelegramDeliveryJobRecord.source_natural_id
                        == "durable-gate-second"
                    )
                    .with_for_update()
                )
            ).scalar_one()
            second.state = TelegramDeliveryState.LEASED
            second.worker_id = "durable-gate-worker-2"
            second.lease_token = 1
            second.lease_until = now + timedelta(seconds=30)
            second.attempt_count = 1
            await db.commit()
        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryDispatchDeferredError,
                "durable_dispatch_gate:leased",
            ):
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=second.id,
                    worker_id=second.worker_id,
                    lease_token=second.lease_token,
                    now=now + timedelta(milliseconds=20),
                )
            await db.rollback()

        async with self.Session() as db:
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 127},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now + timedelta(milliseconds=30),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)

        async with self.Session() as db:
            with self.assertRaisesRegex(
                TelegramDeliveryDispatchDeferredError,
                "durable_dispatch_gate:pending_retry",
            ) as raised:
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=second.id,
                    worker_id=second.worker_id,
                    lease_token=second.lease_token,
                    now=now + timedelta(milliseconds=40),
                )
            await db.rollback()
        self.assertGreater(raised.exception.retry_after_seconds, 126.9)

    async def test_concurrent_same_destination_markers_allow_at_most_one(self):
        await self._enqueue("marker-race-first", destination="private:marker-race")
        await self._enqueue("marker-race-second", destination="private:marker-race")
        now = utc_now()
        first = await self._claim("marker-race-worker-1", now=now)
        second = await self._claim(
            "marker-race-worker-2",
            now=now + timedelta(milliseconds=1),
        )
        start = asyncio.Event()

        async def mark(job):
            async with self.Session() as db:
                await start.wait()
                try:
                    marked = await mark_telegram_delivery_dispatch_started(
                        db,
                        current_server="foreign",
                        job_id=job.id,
                        worker_id=job.worker_id,
                        lease_token=job.lease_token,
                        now=now + timedelta(milliseconds=2),
                    )
                    await db.commit()
                    return "marked" if marked else "stale"
                except TelegramDeliveryDispatchDeferredError:
                    await db.rollback()
                    return "deferred"

        tasks = [asyncio.create_task(mark(first)), asyncio.create_task(mark(second))]
        start.set()
        outcomes = await asyncio.gather(*tasks)

        self.assertEqual(outcomes.count("marked"), 1)
        self.assertEqual(outcomes.count("deferred"), 1)
        async with self.Session() as db:
            marker_count = await db.scalar(
                select(func.count(TelegramDeliveryJobRecord.id)).where(
                    TelegramDeliveryJobRecord.dispatch_started_at.is_not(None)
                )
            )
        self.assertEqual(marker_count, 1)

    async def test_durable_bot_probe_blocks_same_bot_until_probe_result_persists(self):
        await self._enqueue("probe-gate-first", destination="private:probe-a")
        await self._enqueue("probe-gate-second", destination="private:probe-b")
        now = utc_now()
        first = await self._claim("probe-gate-worker-a", now=now)
        second = await self._claim(
            "probe-gate-worker-b",
            now=now + timedelta(milliseconds=1),
        )

        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=first.id,
                    worker_id=first.worker_id,
                    lease_token=first.lease_token,
                    rate_limit_probe=True,
                    now=now,
                )
            )
            await db.commit()

        restarted_limiter = _RecordingLimiter()
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            restored_probe = await rehydrate_telegram_delivery_limiter_state(
                restarted_limiter
            )
        self.assertEqual(restored_probe.restored_count, 1)
        self.assertEqual(restored_probe.blocked_bot_identities, ("primary",))
        self.assertEqual(
            restarted_limiter.bot_extensions,
            [("primary", first.lease_until)],
        )

        async with self.Session() as db:
            with self.assertRaises(TelegramDeliveryDispatchDeferredError) as raised:
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=second.id,
                    worker_id=second.worker_id,
                    lease_token=second.lease_token,
                    now=now + timedelta(milliseconds=2),
                )
            await db.rollback()
        self.assertEqual(raised.exception.scope, "bot")
        self.assertEqual(
            raised.exception.reason,
            "durable_dispatch_gate:bot_probe_inflight",
        )

        async with self.Session() as db:
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                result=TelegramGatewayResult(
                    ok=True,
                    method="sendMessage",
                    status_code=200,
                    response_json={
                        "ok": True,
                        "result": {"message_id": 9911},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now + timedelta(milliseconds=3),
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.SENT)

        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=second.id,
                    worker_id=second.worker_id,
                    lease_token=second.lease_token,
                    now=now + timedelta(milliseconds=4),
                )
            )
            await db.commit()
        async with self.Session() as db:
            first_row = await db.get(TelegramDeliveryJobRecord, first.id)
        self.assertFalse(first_row.rate_limit_probe)

    async def test_429_window_uses_lock_order_not_captured_provider_time(self):
        await self._enqueue("linearized-429-a", destination="private:linear-a")
        await self._enqueue("linearized-429-b", destination="private:linear-b")
        base = utc_now()
        first = await self._claim("linearized-worker-a", now=base)
        second = await self._claim(
            "linearized-worker-b",
            now=base + timedelta(milliseconds=1),
        )
        for job in (first, second):
            async with self.Session() as db:
                self.assertTrue(
                    await mark_telegram_delivery_dispatch_started(
                        db,
                        current_server="foreign",
                        job_id=job.id,
                        worker_id=job.worker_id,
                        lease_token=job.lease_token,
                        now=base + timedelta(milliseconds=2),
                    )
                )
                await db.commit()

        async def persist(job, *, provider_time, linearized_at, retry_after):
            async with self.Session() as db:
                with patch(
                    "core.services.telegram_delivery_queue_service."
                    "telegram_delivery_database_now",
                    new=AsyncMock(side_effect=(provider_time, linearized_at)),
                ):
                    decision = await resolve_telegram_delivery_result(
                        db,
                        current_server="foreign",
                        job_id=job.id,
                        worker_id=job.worker_id,
                        lease_token=job.lease_token,
                        result=TelegramGatewayResult(
                            ok=False,
                            method="sendMessage",
                            status_code=429,
                            response_json={
                                "ok": False,
                                "error_code": 429,
                                "parameters": {"retry_after": retry_after},
                            },
                        ),
                        retry_after_safety_seconds=0.1,
                        retry_base_seconds=1,
                        retry_max_seconds=300,
                        # Keep the test insensitive to a loaded local PostgreSQL;
                        # the assertion is about DB linearization ordering.
                        global_rate_limit_window_seconds=30.0,
                    )
                await db.commit()
                return decision

        # The later-captured provider result commits first. The older result
        # linearizes second and must still observe the first committed 429.
        later = await persist(
            second,
            provider_time=base + timedelta(seconds=1),
            linearized_at=base + timedelta(seconds=2),
            retry_after=120,
        )
        older = await persist(
            first,
            provider_time=base,
            linearized_at=base + timedelta(seconds=3),
            retry_after=5,
        )
        self.assertIsNone(later.bot_cooldown_until)
        self.assertEqual(
            older.bot_cooldown_until,
            base + timedelta(seconds=121.1),
        )
        self.assertEqual(older.next_retry_at, older.bot_cooldown_until)

    async def test_durable_destination_gate_uses_latest_active_429_deadline(self):
        first = await self._enqueue("max-429-first", destination="private:max-429")
        second = await self._enqueue("max-429-second", destination="private:max-429")
        third = await self._enqueue("max-429-third", destination="private:max-429")
        now = utc_now()
        async with self.Session() as db:
            await db.execute(
                update(TelegramDeliveryJobRecord)
                .where(TelegramDeliveryJobRecord.id == first.job.id)
                .values(
                    state=TelegramDeliveryState.PENDING_RETRY,
                    next_retry_at=now + timedelta(seconds=10),
                    outcome_reason="telegram_rate_limited",
                    dispatch_started_at=now,
                )
            )
            await db.execute(
                update(TelegramDeliveryJobRecord)
                .where(TelegramDeliveryJobRecord.id == second.job.id)
                .values(
                    state=TelegramDeliveryState.PENDING_RETRY,
                    next_retry_at=now + timedelta(seconds=20),
                    outcome_reason="telegram_rate_limited",
                    dispatch_started_at=now,
                )
            )
            await db.commit()

        # Bypass the claim scheduler deliberately to exercise the final
        # serializable dispatch boundary against the latest blocker deadline.
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, third.job.id)
            job.state = TelegramDeliveryState.LEASED
            job.worker_id = "max-429-worker"
            job.lease_token = 1
            job.lease_until = now + timedelta(seconds=30)
            job.attempt_count = 1
            await db.commit()
        async with self.Session() as db:
            with self.assertRaises(TelegramDeliveryDispatchDeferredError) as raised:
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=job.id,
                    worker_id=job.worker_id,
                    lease_token=job.lease_token,
                    now=now,
                )
            await db.rollback()

        self.assertEqual(
            raised.exception.cooldown_until,
            now + timedelta(seconds=20),
        )
        self.assertAlmostEqual(
            raised.exception.retry_after_seconds,
            20.0,
            places=5,
        )

    async def test_worker_defers_same_destination_without_second_gateway_call(self):
        await self._enqueue("durable-worker-first", destination="private:rate-worker")
        second = await self._enqueue(
            "durable-worker-second",
            destination="private:rate-worker",
        )
        now = utc_now()
        first = await self._claim("durable-worker-first", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            first_decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 127},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now + timedelta(milliseconds=10),
            )
            await db.commit()

        gateway = AsyncMock()

        async def freshness_validator(_db, _job, _now):
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=_AllowLimiter(),
                worker_id="durable-gate-worker",
                recover_leases=False,
            )

        self.assertEqual(first_decision.outcome, TelegramDeliveryOutcome.RETRY_PENDING)
        self.assertEqual(report.status_counts, {})
        self.assertEqual(report.processed_count, 0)
        gateway.assert_not_awaited()
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, second.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING)
        self.assertIsNone(persisted.outcome_reason)

    async def test_durable_429_gate_does_not_stall_different_destination(self):
        await self._enqueue("different-gate-first", destination="private:blocked")
        allowed = await self._enqueue(
            "different-gate-second",
            destination="private:allowed",
        )
        now = utc_now()
        first = await self._claim("different-gate-first", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 127},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now + timedelta(milliseconds=10),
            )
            await db.commit()

        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 9081}},
            )
        )

        async def freshness_validator(_db, _job, _now):
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=_AllowLimiter(),
                worker_id="different-gate-worker",
                recover_leases=False,
            )

        self.assertEqual(report.status_counts, {"sent": 1})
        gateway.assert_awaited_once()
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, allowed.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.SENT)

    async def test_claim_skips_higher_priority_channel_resource_with_durable_gate(self):
        channel_destination = "channel:-1001"
        await self._enqueue(
            "resource-gate-existing",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination=channel_destination,
            destination_class=TelegramDestinationClass.CHANNEL,
            payload={"chat_id": -1001, "text": "existing"},
        )
        now = utc_now()
        first = await self._claim("resource-gate-first", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
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
                now=now + timedelta(milliseconds=10),
            )
            await db.commit()

        blocked = await self._enqueue(
            "resource-gate-higher-priority",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination=channel_destination,
            destination_class=TelegramDestinationClass.CHANNEL,
            payload={"chat_id": -1001, "text": "blocked"},
        )
        private = await self._enqueue(
            "resource-gate-private-progress",
            destination="private:resource-progress",
        )
        claimed = await self._claim(
            "resource-aware-worker",
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(int(claimed.id), int(private.job.id))

        async with self.Session() as db:
            blocked_record = await db.get(TelegramDeliveryJobRecord, blocked.job.id)
        self.assertEqual(blocked_record.state, TelegramDeliveryState.PENDING)

    async def test_startup_rehydrates_committed_429_before_claims(self):
        await self._enqueue("rehydrate-429", destination="private:rehydrate")
        now = utc_now()
        job = await self._claim("rehydrate-worker", now=now)
        async with self.Session() as db:
            await mark_telegram_delivery_dispatch_started(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                now=now,
            )
            await db.commit()
        async with self.Session() as db:
            await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "parameters": {"retry_after": 127},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now + timedelta(milliseconds=10),
            )
            await db.commit()

        limiter = _RecordingLimiter()
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            restored = await rehydrate_telegram_delivery_limiter_state(limiter)

        self.assertEqual(restored.restored_count, 1)
        self.assertEqual(len(limiter.observations), 1)
        observation, decision, _observed_at = limiter.observations[0]
        self.assertEqual(observation.destination_key, "private:rehydrate")
        self.assertEqual(decision.reason, "telegram_rate_limited")
        self.assertEqual(
            decision.destination_cooldown_until,
            observation.next_retry_at,
        )

    async def test_distinct_429s_persist_and_rehydrate_bot_wide_cooldown(self):
        await self._enqueue("bot-cooldown-first", destination="private:rate-a")
        await self._enqueue("bot-cooldown-second", destination="private:rate-b")
        base = utc_now() - timedelta(seconds=10)

        first = await self._claim("bot-cooldown-worker-a", now=base)
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=first.id,
                    worker_id=first.worker_id,
                    lease_token=first.lease_token,
                    now=base,
                )
            )
            await db.commit()
        async with self.Session() as db:
            first_decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=first.id,
                worker_id=first.worker_id,
                lease_token=first.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 5},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                global_rate_limit_window_seconds=2.0,
                now=base,
            )
            await db.commit()
        self.assertIsNone(first_decision.bot_cooldown_until)

        second_time = base + timedelta(seconds=1)
        second = await self._claim("bot-cooldown-worker-b", now=second_time)
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=second.id,
                    worker_id=second.worker_id,
                    lease_token=second.lease_token,
                    now=second_time,
                )
            )
            await db.commit()
        async with self.Session() as db:
            second_decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=second.id,
                worker_id=second.worker_id,
                lease_token=second.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=429,
                    response_json={
                        "ok": False,
                        "error_code": 429,
                        "description": "Too Many Requests",
                        "parameters": {"retry_after": 120},
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                global_rate_limit_window_seconds=2.0,
                now=second_time,
            )
            await db.commit()

        expected_until = second_time + timedelta(seconds=120.1)
        self.assertEqual(second_decision.bot_cooldown_until, expected_until)
        self.assertEqual(second_decision.next_retry_at, expected_until)
        async with self.Session() as db:
            first_row = await db.get(TelegramDeliveryJobRecord, first.id)
            second_row = await db.get(TelegramDeliveryJobRecord, second.id)
        self.assertGreaterEqual(first_row.last_rate_limited_at, base)
        self.assertEqual(first_row.last_rate_limit_until, base + timedelta(seconds=5.1))
        self.assertIsNone(first_row.bot_cooldown_until)
        self.assertEqual(second_row.bot_cooldown_until, expected_until)
        self.assertEqual(second_row.next_retry_at, expected_until)

        limiter = _RecordingLimiter()
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            restored = await rehydrate_telegram_delivery_limiter_state(limiter)

        # The first destination deadline expired before this simulated restart;
        # the durable bot deadline on the second row remains sufficient.
        self.assertEqual(restored.restored_count, 1)
        self.assertEqual(limiter.bot_extensions, [("primary", expected_until)])
        self.assertEqual(len(limiter.observations), 1)
        self.assertEqual(
            limiter.observations[0][1].bot_cooldown_until,
            expected_until,
        )

    async def test_same_destination_or_outside_window_does_not_create_bot_cooldown(self):
        await self._enqueue("same-rate-first", destination="private:same-rate")
        await self._enqueue("same-rate-second", destination="private:same-rate")
        await self._enqueue("late-distinct-rate", destination="private:late-rate")
        base = utc_now() - timedelta(seconds=10)

        async def persist_429(job, *, at):
            async with self.Session() as db:
                self.assertTrue(
                    await mark_telegram_delivery_dispatch_started(
                        db,
                        current_server="foreign",
                        job_id=job.id,
                        worker_id=job.worker_id,
                        lease_token=job.lease_token,
                        now=at,
                    )
                )
                await db.commit()
            async with self.Session() as db:
                decision = await resolve_telegram_delivery_result(
                    db,
                    current_server="foreign",
                    job_id=job.id,
                    worker_id=job.worker_id,
                    lease_token=job.lease_token,
                    result=TelegramGatewayResult(
                        ok=False,
                        method="sendMessage",
                        status_code=429,
                        response_json={
                            "ok": False,
                            "error_code": 429,
                            "parameters": {"retry_after": 1},
                        },
                    ),
                    retry_after_safety_seconds=0.0,
                    retry_base_seconds=1,
                    retry_max_seconds=300,
                    global_rate_limit_window_seconds=2.0,
                    now=at,
                )
                await db.commit()
                return decision

        first = await self._claim("same-rate-worker-a", now=base)
        self.assertIsNone((await persist_429(first, at=base)).bot_cooldown_until)
        async with self.Session() as db:
            await db.execute(
                update(TelegramDeliveryJobRecord)
                .where(TelegramDeliveryJobRecord.id == first.id)
                .values(
                    state=TelegramDeliveryState.SENT,
                    next_retry_at=None,
                    terminal_at=base,
                )
            )
            await db.commit()
        second_time = base + timedelta(seconds=1, milliseconds=1)
        second = await self._claim("same-rate-worker-b", now=second_time)
        self.assertIsNone(
            (await persist_429(second, at=second_time)).bot_cooldown_until
        )

        async with self.Session() as db:
            await db.execute(
                update(TelegramDeliveryJobRecord)
                .where(
                    TelegramDeliveryJobRecord.id.in_((first.id, second.id))
                )
                .values(
                    state=TelegramDeliveryState.SENT,
                    next_retry_at=None,
                    terminal_at=second_time,
                )
            )
            await db.commit()
        late_time = base + timedelta(seconds=4)
        late = await self._claim("late-rate-worker", now=late_time)
        late_decision = await persist_429(late, at=late_time)
        self.assertIsNone(late_decision.bot_cooldown_until)

    async def test_hard_pause_evidence_rehydrates_and_blocks_its_exact_scope(self):
        cases = (
            {
                "name": "bot",
                "status": 401,
                "description": "Unauthorized",
                "expected_outcome": TelegramDeliveryOutcome.BOT_PAUSED,
                "candidate": {
                    "source_id": "hard-bot-candidate",
                    "destination": "private:other-bot-destination",
                    "bot_identity": "primary",
                },
                "control": {
                    "source_id": "hard-bot-other-lane-control",
                    "feeder": TelegramFeederKind.OFFER_EDIT,
                    "action": TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                    "destination": "channel:other-bot-control",
                    "destination_class": TelegramDestinationClass.CHANNEL,
                    "method": "editMessageText",
                    "payload": {
                        "chat_id": -1003,
                        "message_id": 46,
                        "text": "expired",
                    },
                    "bot_identity": "channel_editor",
                },
            },
            {
                "name": "destination",
                "status": 403,
                "description": "Forbidden",
                "expected_outcome": TelegramDeliveryOutcome.DESTINATION_PAUSED,
                "blocker_overrides": {
                    "destination": "channel:hard-destination",
                    "destination_class": TelegramDestinationClass.CHANNEL,
                    "payload": {"chat_id": -1001, "text": "block"},
                },
                "candidate": {
                    "source_id": "hard-destination-candidate",
                    "feeder": TelegramFeederKind.OFFER_CONTROL,
                    "action": TelegramDeliveryAction.OFFER_PUBLISH,
                    "destination": "channel:hard-destination",
                    "destination_class": TelegramDestinationClass.CHANNEL,
                    "payload": {"chat_id": -1001, "text": "candidate"},
                    "bot_identity": "primary",
                },
                "control": {
                    "source_id": "hard-destination-other-control",
                    "feeder": TelegramFeederKind.OFFER_EDIT,
                    "action": TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                    "destination": "channel:hard-destination",
                    "destination_class": TelegramDestinationClass.CHANNEL,
                    "method": "editMessageText",
                    "payload": {
                        "chat_id": -1001,
                        "message_id": 44,
                        "text": "expired",
                    },
                    "bot_identity": "channel_editor",
                },
            },
            {
                "name": "gateway",
                "status": 404,
                "description": "Not Found: method endpoint",
                "expected_outcome": TelegramDeliveryOutcome.GATEWAY_PAUSED,
                "candidate": {
                    "source_id": "hard-gateway-candidate",
                    "feeder": TelegramFeederKind.OFFER_EDIT,
                    "action": TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
                    "destination": "channel:unrelated-gateway-destination",
                    "destination_class": TelegramDestinationClass.CHANNEL,
                    "method": "editMessageText",
                    "payload": {
                        "chat_id": -1002,
                        "message_id": 45,
                        "text": "expired",
                    },
                    "bot_identity": "channel_editor",
                },
            },
        )

        async def freshness_validator(_db, _job, _now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        for case in cases:
            with self.subTest(scope=case["name"]):
                async with self.maintenance_engine.begin() as connection:
                    await connection.execute(
                        text(
                            "TRUNCATE TABLE telegram_delivery_runtime_gates, "
                            "telegram_delivery_jobs "
                            "RESTART IDENTITY CASCADE"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER SEQUENCE telegram_delivery_jobs_enqueued_seq_seq "
                            "RESTART WITH 1"
                        )
                    )

                blocker_overrides = dict(case.get("blocker_overrides", {}))
                await self._enqueue(
                    f"hard-{case['name']}-blocker",
                    **blocker_overrides,
                )
                candidate_args = dict(case["candidate"])
                candidate_source = candidate_args.pop("source_id")
                candidate = await self._enqueue(candidate_source, **candidate_args)
                now = utc_now()
                blocker = await self._claim(
                    f"hard-{case['name']}-worker",
                    now=now,
                    bot_identity="primary",
                )
                async with self.Session() as db:
                    self.assertTrue(
                        await mark_telegram_delivery_dispatch_started(
                            db,
                            current_server="foreign",
                            job_id=blocker.id,
                            worker_id=blocker.worker_id,
                            lease_token=blocker.lease_token,
                            now=now,
                        )
                    )
                    await db.commit()
                async with self.Session() as db:
                    decision = await resolve_telegram_delivery_result(
                        db,
                        current_server="foreign",
                        job_id=blocker.id,
                        worker_id=blocker.worker_id,
                        lease_token=blocker.lease_token,
                        result=TelegramGatewayResult(
                            ok=False,
                            method="sendMessage",
                            status_code=case["status"],
                            response_json={
                                "ok": False,
                                "error_code": case["status"],
                                "description": case["description"],
                            },
                        ),
                        retry_after_safety_seconds=0.1,
                        retry_base_seconds=1,
                        retry_max_seconds=300,
                        now=now,
                    )
                    await db.commit()
                self.assertEqual(decision.outcome, case["expected_outcome"])

                limiter = _RecordingLimiter()
                with patch(
                    "core.telegram_delivery_queue_worker.AsyncSessionLocal",
                    self.Session,
                ), patch(
                    "core.telegram_delivery_queue_worker.current_server",
                    return_value="foreign",
                ):
                    restored = await rehydrate_telegram_delivery_limiter_state(
                        limiter
                    )
                self.assertEqual(restored.restored_count, 1)
                self.assertEqual(
                    limiter.observations[0][1].outcome,
                    case["expected_outcome"],
                )

                gateway = AsyncMock()
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
                        bot_identity=str(candidate.job.bot_identity),
                        limit=1,
                        freshness_validator=freshness_validator,
                        lifecycle_feedback=_NoopLifecycleFeedback(),
                        gateway_call=gateway,
                        dispatch_limiter=limiter,
                        worker_id=f"hard-{case['name']}-candidate-worker",
                        recover_leases=False,
                    )

                self.assertEqual(
                    report.status_counts,
                    {},
                )
                self.assertEqual(report.processed_count, 0)
                gateway.assert_not_awaited()

                control_args = case.get("control")
                if control_args is not None:
                    control_kwargs = dict(control_args)
                    control_source = control_kwargs.pop("source_id")
                    control = await self._enqueue(
                        control_source,
                        **control_kwargs,
                    )

                    async def successful_gateway(method, _payload, **_kwargs):
                        return TelegramGatewayResult(
                            ok=True,
                            method=method,
                            status_code=200,
                            response_json={
                                "ok": True,
                                "result": {"message_id": 9901},
                            },
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
                        control_report = await run_telegram_delivery_queue_cycle(
                            bot_identity=str(control.job.bot_identity),
                            limit=1,
                            freshness_validator=freshness_validator,
                            lifecycle_feedback=_NoopLifecycleFeedback(),
                            gateway_call=successful_gateway,
                            dispatch_limiter=limiter,
                            worker_id=f"hard-{case['name']}-control-worker",
                            recover_leases=False,
                        )

                    self.assertEqual(control_report.status_counts, {"sent": 1})

    async def test_preflight_rate_limit_is_durable_and_blocks_claim_until_deadline(self):
        now = utc_now()
        async with self.Session() as db:
            gate = await record_telegram_preflight_rate_limit(
                db,
                current_server="foreign",
                bot_identity="primary",
                retry_after_seconds=7,
                safety_seconds=0.1,
                now=now,
            )
            await db.commit()
        expected_until = now + timedelta(seconds=7.1)
        self.assertEqual(gate.cooldown_until, expected_until)

        await self._enqueue("preflight-rate-limit-candidate")
        self.assertIsNone(
            await self._claim("preflight-rate-limit-worker", now=now)
        )
        claimed = await self._claim(
            "preflight-rate-limit-worker",
            now=expected_until + timedelta(milliseconds=1),
        )
        self.assertIsNotNone(claimed)

        limiter = _RecordingLimiter()
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ), patch(
            "core.telegram_delivery_queue_worker.utc_now",
            return_value=now + timedelta(seconds=1),
        ):
            report = await rehydrate_telegram_delivery_limiter_state(limiter)
        self.assertEqual(report.blocked_bot_identities, ("primary",))
        self.assertEqual(limiter.bot_extensions, [("primary", expected_until)])

        credentials = self._credential_registry()
        async with self.Session() as db:
            changed = await mark_telegram_preflight_gate_active(
                db,
                current_server="foreign",
                bot_identity="primary",
                report=self._preflight_report(credentials, ("primary",)),
                now=expected_until + timedelta(seconds=1),
            )
            await db.commit()
        self.assertTrue(changed)
        async with self.Session() as db:
            active_gate = await db.get(TelegramDeliveryRuntimeGate, "bot:primary")
        self.assertEqual(active_gate.state, TELEGRAM_RUNTIME_GATE_ACTIVE)
        self.assertIsNone(active_gate.cooldown_until)

    async def test_malformed_preflight_429_persists_bounded_database_cooldown(self):
        now = utc_now()
        async with self.Session() as db:
            gate = await record_telegram_preflight_rate_limit(
                db,
                current_server="foreign",
                bot_identity="primary",
                retry_after_seconds=3,
                safety_seconds=0.1,
                retry_after_source="bounded_malformed_fallback",
                now=now,
            )
            await db.commit()
        self.assertEqual(
            gate.reason_code,
            "telegram_preflight_rate_limited_malformed_fallback",
        )
        self.assertEqual(gate.retry_after_seconds, 3)
        self.assertEqual(gate.cooldown_until, now + timedelta(seconds=3.1))
        await self._enqueue("preflight-malformed-rate-limit-candidate")
        self.assertIsNone(
            await self._claim(
                "preflight-malformed-rate-limit-worker",
                now=now + timedelta(seconds=1),
            )
        )
        async with self.Session() as db:
            persisted_gate = await db.get(TelegramDeliveryRuntimeGate, "bot:primary")
        self.assertEqual(
            persisted_gate.cooldown_until,
            now + timedelta(seconds=3.1),
        )

    async def test_bot_pause_gate_resume_is_crash_safe_idempotent_and_audited(self):
        blocked_job, decision = await self._persist_hard_pause(
            "runtime-bot-pause",
            status_code=401,
            description="Unauthorized",
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.BOT_PAUSED)
        credentials = self._credential_registry()
        preflight = AsyncMock(
            return_value=self._preflight_report(credentials, ("primary",))
        )
        settings = SimpleNamespace(
            telegram_delivery_queue_retry_after_safety_seconds=0.1,
        )
        failing_limiter = _ResumeRecordingLimiter(fail_bot_resume=True)
        request_id = "runtime-bot-resume-request-0001"

        with self.assertRaisesRegex(
            TelegramRuntimeGateResumeIncompleteError,
            "redis_not_cleared",
        ):
            await resume_telegram_runtime_gate(
                session_factory=self.Session,
                current_server="foreign",
                settings=settings,
                credential_registry=credentials,
                dispatch_limiter=failing_limiter,
                scope="bot",
                bot_identity="primary",
                request_id=request_id,
                requested_by="operator@example.test",
                preflight_runner=preflight,
            )

        async with self.Session() as db:
            gate = await db.get(TelegramDeliveryRuntimeGate, "bot:primary")
            row = await db.get(TelegramDeliveryJobRecord, blocked_job.id)
        self.assertEqual(gate.state, TELEGRAM_RUNTIME_GATE_DATABASE_APPLIED)
        self.assertEqual(gate.resumed_job_ids, [blocked_job.id])
        self.assertNotEqual(gate.requested_by_hash, "operator@example.test")
        self.assertNotIn("operator@example.test", repr(gate.attempt_history))
        self.assertEqual(row.state, TelegramDeliveryState.PENDING_RETRY)

        limiter = _ResumeRecordingLimiter()
        report = await resume_telegram_runtime_gate(
            session_factory=self.Session,
            current_server="foreign",
            settings=settings,
            credential_registry=credentials,
            dispatch_limiter=limiter,
            scope="bot",
            bot_identity="primary",
            request_id=request_id,
            requested_by="operator@example.test",
            preflight_runner=preflight,
        )
        self.assertEqual(report.state, TELEGRAM_RUNTIME_GATE_ACTIVE)
        self.assertEqual(report.resumed_job_ids, (blocked_job.id,))
        self.assertTrue(report.idempotent_replay)
        self.assertEqual(limiter.resumed_bots, ["primary"])

        replay = await resume_telegram_runtime_gate(
            session_factory=self.Session,
            current_server="foreign",
            settings=settings,
            credential_registry=credentials,
            dispatch_limiter=limiter,
            scope="bot",
            bot_identity="primary",
            request_id=request_id,
            requested_by="operator@example.test",
            preflight_runner=preflight,
        )
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(replay.resumed_job_ids, (blocked_job.id,))
        self.assertEqual(limiter.resumed_bots, ["primary"])

    async def test_provider_pause_gate_and_job_transition_share_one_transaction(self):
        await self._enqueue("runtime-gate-rollback")
        now = utc_now()
        claimed = await self._claim("runtime-gate-rollback-worker", now=now)
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=claimed.id,
                    worker_id=claimed.worker_id,
                    lease_token=claimed.lease_token,
                    now=now,
                )
            )
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=claimed.id,
                worker_id=claimed.worker_id,
                lease_token=claimed.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="sendMessage",
                    status_code=401,
                    response_json={
                        "ok": False,
                        "error_code": 401,
                        "description": "Unauthorized",
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now,
            )
            self.assertEqual(decision.outcome, TelegramDeliveryOutcome.BOT_PAUSED)
            await db.rollback()

        async with self.Session() as db:
            gate = await db.get(TelegramDeliveryRuntimeGate, "bot:primary")
            row = await db.get(TelegramDeliveryJobRecord, claimed.id)
        self.assertIsNone(gate)
        self.assertEqual(row.state, TelegramDeliveryState.LEASED)

    async def test_gateway_pause_gate_resume_releases_both_lanes_once(self):
        blocked_job, decision = await self._persist_hard_pause(
            "runtime-gateway-pause",
            status_code=404,
            description="Not Found: method endpoint",
        )
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.GATEWAY_PAUSED)
        credentials = self._credential_registry(editor_enabled=True)
        identities = ("primary", "channel_editor")
        preflight = AsyncMock(
            return_value=self._preflight_report(credentials, identities)
        )
        limiter = _ResumeRecordingLimiter()
        report = await resume_telegram_runtime_gate(
            session_factory=self.Session,
            current_server="foreign",
            settings=SimpleNamespace(
                telegram_delivery_queue_retry_after_safety_seconds=0.1,
            ),
            credential_registry=credentials,
            dispatch_limiter=limiter,
            scope="gateway",
            bot_identity=None,
            request_id="runtime-gateway-resume-request-0001",
            requested_by="on-call",
            preflight_runner=preflight,
        )
        self.assertEqual(report.resumed_job_ids, (blocked_job.id,))
        self.assertEqual(limiter.gateway_resume_count, 1)
        preflight.assert_awaited_once()
        self.assertEqual(
            preflight.await_args.kwargs["bot_identities"],
            identities,
        )

    async def test_runtime_resume_persists_preflight_429_and_fences_other_request(self):
        await self._persist_hard_pause(
            "runtime-resume-preflight-rate-limit",
            status_code=401,
            description="Unauthorized",
        )
        credentials = self._credential_registry()
        rate_limited = AsyncMock(
            side_effect=TelegramDeliveryPreflightRateLimitedError(
                "telegram_preflight_rate_limited:primary:getMe",
                retry_after_seconds=17,
                bot_identity="primary",
                method="getMe",
            )
        )
        kwargs = {
            "session_factory": self.Session,
            "current_server": "foreign",
            "settings": SimpleNamespace(
                telegram_delivery_queue_retry_after_safety_seconds=0.1,
            ),
            "credential_registry": credentials,
            "dispatch_limiter": _ResumeRecordingLimiter(),
            "scope": "bot",
            "bot_identity": "primary",
            "request_id": "runtime-rate-limit-request-0001",
            "requested_by": "on-call",
            "preflight_runner": rate_limited,
        }
        with self.assertRaises(TelegramDeliveryPreflightRateLimitedError):
            await resume_telegram_runtime_gate(**kwargs)

        async with self.Session() as db:
            gate = await db.get(TelegramDeliveryRuntimeGate, "bot:primary")
        self.assertEqual(gate.state, "resume_requested")
        self.assertEqual(gate.reason_code, "telegram_preflight_rate_limited")
        self.assertGreater(gate.cooldown_until, utc_now())

        with self.assertRaisesRegex(
            TelegramRuntimeGateResumeIncompleteError,
            "preflight_cooldown_active",
        ):
            await resume_telegram_runtime_gate(**kwargs)
        self.assertEqual(rate_limited.await_count, 1)

        with self.assertRaisesRegex(
            TelegramRuntimeGateConflictError,
            "resume_conflict",
        ):
            await resume_telegram_runtime_gate(
                **{
                    **kwargs,
                    "request_id": "runtime-rate-limit-request-0002",
                }
            )

    async def test_committed_429_survives_observe_crash_and_restart_without_resend(self):
        first = await self._enqueue(
            "observe-crash-first",
            destination="private:observe-crash",
        )
        second = await self._enqueue(
            "observe-crash-second",
            destination="private:observe-crash",
        )
        gateway_first = AsyncMock(
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

        async def freshness_validator(_db, _job, _now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        worker_patches = (
            patch(
                "core.telegram_delivery_queue_worker.AsyncSessionLocal",
                self.Session,
            ),
            patch(
                "core.telegram_delivery_queue_worker.current_server",
                return_value="foreign",
            ),
            patch(
                "core.telegram_delivery_queue_worker.configured_telegram_delivery_runtime",
                return_value=self._queue_runtime(),
            ),
        )
        with worker_patches[0], worker_patches[1], worker_patches[2]:
            with self.assertRaisesRegex(
                TelegramDeliveryLimiterUnavailableError,
                "synthetic_observe_failure_after_commit",
            ):
                await run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=gateway_first,
                    dispatch_limiter=_ObserveFailingLimiter(),
                    worker_id="observe-crash-worker-a",
                    recover_leases=False,
                )

        gateway_first.assert_awaited_once()
        async with self.Session() as db:
            persisted_first = await db.get(
                TelegramDeliveryJobRecord,
                first.job.id,
            )
        self.assertEqual(
            persisted_first.state,
            TelegramDeliveryState.PENDING_RETRY,
        )
        self.assertEqual(
            persisted_first.outcome_reason,
            "telegram_rate_limited",
        )

        restarted_limiter = _RecordingLimiter()
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            restored = await rehydrate_telegram_delivery_limiter_state(
                restarted_limiter
            )
        self.assertEqual(restored.restored_count, 1)
        self.assertEqual(len(restarted_limiter.observations), 1)

        gateway_second = AsyncMock()
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway_second,
                dispatch_limiter=restarted_limiter,
                worker_id="observe-crash-worker-b",
                recover_leases=False,
            )

        self.assertEqual(report.status_counts, {})
        self.assertEqual(report.processed_count, 0)
        gateway_second.assert_not_awaited()
        gateway_first.assert_awaited_once()
        async with self.Session() as db:
            persisted_second = await db.get(
                TelegramDeliveryJobRecord,
                second.job.id,
            )
        self.assertEqual(
            persisted_second.state,
            TelegramDeliveryState.PENDING,
        )

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

    async def test_quarantined_freshness_result_is_terminal_and_releases_lease(self):
        await self._enqueue("canonical-identity-mismatch")
        job = await self._claim("worker-quarantine", now=utc_now())
        async with self.Session() as db:
            may_dispatch = await apply_telegram_delivery_freshness_result(
                db,
                current_server="foreign",
                job_id=job.id,
                worker_id=job.worker_id,
                lease_token=job.lease_token,
                decision=TelegramFreshnessDecision(
                    TelegramFreshnessOutcome.QUARANTINED,
                    reason="canonical_identity_mismatch",
                ),
            )
            await db.commit()

        self.assertFalse(may_dispatch)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.QUARANTINED)
        self.assertEqual(persisted.outcome_reason, "canonical_identity_mismatch")
        self.assertIsNotNone(persisted.terminal_at)
        self.assertIsNone(persisted.worker_id)
        self.assertIsNone(persisted.lease_until)

    async def test_offer_freshness_uses_real_authoritative_rows_and_version(self):
        public_id = "ofr_stage3_freshness_pg"
        commodity_name = "stage3-freshness-commodity"
        channel_id = -100123456700
        await self._reset_authoritative_fixture(
            (
                "DELETE FROM offer_publication_states WHERE offer_public_id = :public_id",
                {"public_id": public_id},
            ),
            (
                "DELETE FROM offers WHERE offer_public_id = :public_id",
                {"public_id": public_id},
            ),
            ("DELETE FROM commodities WHERE name = :name", {"name": commodity_name}),
        )
        async with self.Session() as db:
            commodity = Commodity(name=commodity_name)
            db.add(commodity)
            await db.flush()
            offer = Offer(
                offer_public_id=public_id,
                home_server="foreign",
                offer_type=OfferType.SELL,
                settlement_type=SettlementType.CASH,
                commodity_id=commodity.id,
                commodity=commodity,
                quantity=10,
                remaining_quantity=10,
                price=72_500_000,
                is_wholesale=True,
                status=OfferStatus.ACTIVE,
            )
            db.add(offer)
            await db.flush()
            state = build_offer_publication_state(
                offer,
                OfferPublicationSurface.TELEGRAM_CHANNEL,
                status=OfferPublicationStatus.SENT,
            )
            state.surface_resource_id = "900"
            state.telegram_chat_id = channel_id
            state.telegram_message_id = 900
            db.add(state)
            await db.flush()

            payload = {
                "chat_id": channel_id,
                "message_id": 900,
                "text": build_offer_channel_message(offer),
                "reply_markup": build_offer_channel_reply_markup(offer),
            }
            enqueued = await enqueue_telegram_delivery_job(
                db,
                current_server="foreign",
                feeder=TelegramFeederKind.OFFER_EDIT,
                source_natural_id=public_id,
                source_version=offer.version_id,
                action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                bot_identity="channel_editor",
                destination_key=telegram_channel_destination_key(channel_id),
                destination_class=TelegramDestinationClass.CHANNEL,
                method="editMessageText",
                payload=payload,
                template_version="offer-channel-v1",
                source_order_at=offer.created_at,
            )
            await db.commit()
            job = enqueued.job

        async with self.Session() as db:
            current = await validate_offer_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
                expected_channel_id=channel_id,
            )
        self.assertEqual(current.outcome, TelegramFreshnessOutcome.SEND)

        async with self.Session() as db:
            offer = (
                await db.execute(
                    select(Offer).where(Offer.offer_public_id == public_id)
                )
            ).scalar_one()
            offer.remaining_quantity = 5
            await db.commit()
        async with self.Session() as db:
            stale = await validate_offer_telegram_delivery_freshness(
                db,
                job,
                utc_now(),
                expected_channel_id=channel_id,
            )
        self.assertEqual(stale.outcome, TelegramFreshnessOutcome.RECLASSIFY)
        self.assertEqual(
            stale.replacement_action,
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
        )

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
        await self._enqueue("unstarted", destination="private:lease-1")
        await self._enqueue(
            "started-send",
            destination="private:lease-2",
            payload={"chat_id": 1002, "text": "started-send"},
        )
        await self._enqueue(
            "started-edit",
            feeder=TelegramFeederKind.TIMED_BOT,
            action=TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
            destination="private:lease-3",
            method="editMessageReplyMarkup",
            payload={"chat_id": 1003, "message_id": 11, "reply_markup": {"inline_keyboard": []}},
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
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

    async def test_worker_persists_and_clears_probe_marker_around_gateway_call(self):
        enqueued = await self._enqueue("worker-rate-limit-probe-marker")
        observed = {}

        async def freshness_validator(_db, _job, _now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        async def gateway_call(method, _payload, **_kwargs):
            async with self.Session() as db:
                persisted = await db.get(
                    TelegramDeliveryJobRecord,
                    enqueued.job.id,
                )
                observed["dispatch_started"] = (
                    persisted.dispatch_started_at is not None
                )
                observed["rate_limit_probe"] = persisted.rate_limit_probe
            return TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 778}},
            )

        limiter = _ProbeLimiter()
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway_call,
                dispatch_limiter=limiter,
                worker_id="worker-probe-marker-test",
                recover_leases=False,
            )

        self.assertEqual(report.status_counts, {"sent": 1})
        self.assertEqual(
            observed,
            {"dispatch_started": True, "rate_limit_probe": True},
        )
        self.assertEqual(len(limiter.observations), 1)
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.SENT)
        self.assertFalse(persisted.rate_limit_probe)

    async def test_worker_revalidates_after_limiter_before_gateway_io(self):
        enqueued = await self._enqueue("stale-during-limiter-admission")
        gateway = AsyncMock()
        validation_count = 0

        async def freshness_validator(db, job, now):
            nonlocal validation_count
            validation_count += 1
            if validation_count == 1:
                return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)
            return TelegramFreshnessDecision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_became_terminal_during_admission",
            )

        limiter = _ProbeLimiter()
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=limiter,
                worker_id="worker-final-freshness-test",
            )

        self.assertEqual(validation_count, 2)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.status_counts, {"superseded": 1})
        gateway.assert_not_awaited()
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, enqueued.job.id)
        self.assertEqual(persisted.state, TelegramDeliveryState.SUPERSEDED)
        self.assertIsNone(persisted.dispatch_started_at)
        self.assertEqual(len(limiter.observations), 1)
        self.assertEqual(
            limiter.observations[0][1].reason,
            "rate_limit_probe_cancelled_before_dispatch",
        )

    async def test_two_slots_do_not_start_new_provider_call_after_retained_fact(self):
        blocked_candidate = await self._enqueue("cross-slot-blocked-candidate")
        provider_candidate = await self._enqueue("cross-slot-provider-candidate")
        blocked_claimed = asyncio.Event()
        provider_fact_retained = asyncio.Event()
        allow_provider_fact_commit = asyncio.Event()

        class _WaitForRetainedFactLimiter(_AllowLimiter):
            async def acquire(self, _job, *, now):
                blocked_claimed.set()
                await asyncio.wait_for(provider_fact_retained.wait(), timeout=2)
                return TelegramDeliveryDispatchAdmission(allowed=True)

        async def freshness_validator(_db, _job, _now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        async def delayed_provider_record(db, **kwargs):
            if int(kwargs["job_id"]) == int(provider_candidate.job.id):
                # The worker installs its role gate immediately before calling
                # this persistence function.
                provider_fact_retained.set()
                await asyncio.wait_for(allow_provider_fact_commit.wait(), timeout=2)
            return await record_telegram_delivery_provider_outcome(db, **kwargs)

        blocked_gateway = AsyncMock()
        provider_gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 771}},
            )
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
        ), patch(
            "core.telegram_delivery_queue_worker.record_telegram_delivery_provider_outcome",
            side_effect=delayed_provider_record,
        ):
            blocked_task = asyncio.create_task(
                run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=blocked_gateway,
                    dispatch_limiter=_WaitForRetainedFactLimiter(),
                    worker_id="cross-slot-blocked-worker",
                    recover_leases=False,
                )
            )
            await asyncio.wait_for(blocked_claimed.wait(), timeout=2)
            provider_task = asyncio.create_task(
                run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=provider_gateway,
                    dispatch_limiter=_AllowLimiter(),
                    worker_id="cross-slot-provider-worker",
                    recover_leases=False,
                )
            )
            try:
                blocked_report = await asyncio.wait_for(blocked_task, timeout=2)
                self.assertEqual(
                    blocked_report.status_counts,
                    {"provider_fact_persistence_wait": 1},
                )
                blocked_gateway.assert_not_awaited()
            finally:
                allow_provider_fact_commit.set()
            provider_report = await asyncio.wait_for(provider_task, timeout=2)

        self.assertEqual(provider_report.status_counts, {"sent": 1})
        provider_gateway.assert_awaited_once()
        async with self.Session() as db:
            blocked = await db.get(
                TelegramDeliveryJobRecord, blocked_candidate.job.id
            )
            provider = await db.get(
                TelegramDeliveryJobRecord, provider_candidate.job.id
            )
        self.assertEqual(blocked.state, TelegramDeliveryState.PENDING_RETRY)
        self.assertEqual(
            blocked.outcome_reason,
            "provider_fact_persistence_wait_before_dispatch",
        )
        self.assertIsNone(blocked.last_error_class)
        self.assertIsNone(blocked.dispatch_started_at)
        self.assertEqual(provider.state, TelegramDeliveryState.SENT)

    async def test_complete_freshness_router_runs_at_both_worker_boundaries(self):
        enqueued = await self._enqueue("worker-complete-router")
        delegate = AsyncMock(
            return_value=TelegramFreshnessDecision(
                TelegramFreshnessOutcome.SEND,
                reason="authoritative_current",
            )
        )
        router = TelegramDeliveryFreshnessRegistry(
            {
                action: delegate
                for action in DURABLE_TELEGRAM_DELIVERY_ACTIONS
            }
        ).build_lane_router("primary")
        gateway = AsyncMock(
            return_value=TelegramGatewayResult(
                ok=True,
                method="sendMessage",
                status_code=200,
                response_json={
                    "ok": True,
                    "result": {"message_id": 991},
                },
            )
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
                freshness_validator=router,
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=_AllowLimiter(),
                worker_id="worker-complete-router-test",
            )

        self.assertEqual(delegate.await_count, 2)
        self.assertEqual(report.status_counts, {"sent": 1})
        gateway.assert_awaited_once()
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                enqueued.job.id,
            )
        self.assertEqual(persisted.state, TelegramDeliveryState.SENT)
        self.assertEqual(persisted.telegram_message_id, 991)

    async def test_market_freshness_blocks_transition_changed_after_limiter(self):
        channel_id = -100123456
        transition_at = utc_now() - timedelta(seconds=10)
        dedupe_key = market_channel_notice_dedupe_key(
            transition=MARKET_NOTICE_TRANSITION_OPENED,
            transition_at=transition_at,
            notice_text=MARKET_OPENED_CHANNEL_NOTICE,
        )
        await self._reset_authoritative_fixture(
            ("DELETE FROM market_channel_notice_receipts", {}),
            ("DELETE FROM market_runtime_state", {}),
        )
        async with self.Session() as db:
            db.add(
                MarketRuntimeState(
                    id=1,
                    is_open=True,
                    active_web_notice_visible=True,
                    offers_since_last_open=0,
                    last_transition_at=transition_at,
                )
            )
            db.add(
                MarketChannelNoticeReceipt(
                    dedupe_key=dedupe_key,
                    transition=MARKET_NOTICE_TRANSITION_OPENED,
                    transition_at=transition_at,
                    notice_text=MARKET_OPENED_CHANNEL_NOTICE,
                    channel_id=str(channel_id),
                    status=MARKET_NOTICE_STATUS_PENDING,
                )
            )
            await db.commit()

        enqueued = await self._enqueue(
            dedupe_key,
            feeder=TelegramFeederKind.MARKET_STATUS,
            action=TelegramDeliveryAction.MARKET_TRANSITION,
            destination=telegram_channel_destination_key(channel_id),
            destination_class=TelegramDestinationClass.CHANNEL,
            method="sendMessage",
            payload={
                "chat_id": channel_id,
                "text": MARKET_OPENED_CHANNEL_NOTICE,
            },
            freshness_deadline=market_channel_notice_freshness_deadline(
                transition_at
            ),
            template_version=MARKET_NOTICE_TEMPLATE_VERSION,
            campaign_id=MARKET_NOTICE_CAMPAIGN_ID,
            run_id=None,
        )
        async with self.Session() as db:
            receipt = (
                await db.execute(
                    select(MarketChannelNoticeReceipt).where(
                        MarketChannelNoticeReceipt.dedupe_key == dedupe_key
                    )
                )
            ).scalar_one()
            receipt.queue_job_id = int(enqueued.job.id)
            receipt.queue_handed_off_at = utc_now()
            await db.commit()
        enqueued.job.template_version = MARKET_NOTICE_TEMPLATE_VERSION
        enqueued.job.campaign_id = MARKET_NOTICE_CAMPAIGN_ID
        enqueued.job.run_id = None
        validator = MarketTelegramDeliveryFreshnessValidator(channel_id)
        async with self.Session() as db:
            current = await validate_market_telegram_delivery_freshness(
                db,
                enqueued.job,
                utc_now(),
                expected_channel_id=channel_id,
            )
        self.assertEqual(current.outcome, TelegramFreshnessOutcome.SEND)

        Session = self.Session

        class _CloseMarketAfterFirstFreshness(_AllowLimiter):
            async def acquire(self, _job, *, now):
                async with Session() as db:
                    state = await db.get(MarketRuntimeState, 1)
                    state.is_open = False
                    state.last_transition_at = now
                    await db.commit()
                return TelegramDeliveryDispatchAdmission(allowed=True)

        gateway = AsyncMock()
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
                freshness_validator=validator,
                lifecycle_feedback=_NoopLifecycleFeedback(),
                gateway_call=gateway,
                dispatch_limiter=_CloseMarketAfterFirstFreshness(),
                worker_id="worker-market-freshness-race-test",
            )

        self.assertEqual(report.status_counts, {"superseded": 1})
        gateway.assert_not_awaited()
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                enqueued.job.id,
            )
        self.assertEqual(persisted.state, TelegramDeliveryState.SUPERSEDED)
        self.assertEqual(
            persisted.outcome_reason,
            "market_freshness_transition_no_longer_current",
        )

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
                    lifecycle_feedback=_NoopLifecycleFeedback(),
                    gateway_call=primary_gateway,
                    dispatch_limiter=_AllowLimiter(),
                    worker_id="primary-lane-worker",
                    recover_leases=False,
                ),
                run_telegram_delivery_queue_cycle(
                    bot_identity="channel_editor",
                    limit=1,
                    freshness_validator=freshness_validator,
                    lifecycle_feedback=_NoopLifecycleFeedback(),
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

    async def test_editor_403_does_not_block_primary_publication_for_same_channel(self):
        channel_destination = "channel:editor-permission-isolation"
        editor = await self._enqueue(
            "editor-forbidden",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            destination=channel_destination,
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 10, "text": "expired"},
            bot_identity="channel_editor",
        )
        primary = await self._enqueue(
            "primary-publication-after-editor-403",
            feeder=TelegramFeederKind.OFFER_CONTROL,
            action=TelegramDeliveryAction.OFFER_PUBLISH,
            destination=channel_destination,
            destination_class=TelegramDestinationClass.CHANNEL,
            payload={"chat_id": -1001, "text": "new offer"},
        )
        another_editor = await self._enqueue(
            "editor-still-gated",
            feeder=TelegramFeederKind.OFFER_EDIT,
            action=TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
            destination=channel_destination,
            destination_class=TelegramDestinationClass.CHANNEL,
            method="editMessageText",
            payload={"chat_id": -1001, "message_id": 11, "text": "expired"},
            bot_identity="channel_editor",
        )
        now = utc_now()
        claimed_editor = await self._claim(
            "editor-403-worker",
            now=now,
            bot_identity="channel_editor",
        )
        self.assertEqual(claimed_editor.id, editor.job.id)
        async with self.Session() as db:
            self.assertTrue(
                await mark_telegram_delivery_dispatch_started(
                    db,
                    current_server="foreign",
                    job_id=claimed_editor.id,
                    worker_id=claimed_editor.worker_id,
                    lease_token=claimed_editor.lease_token,
                    now=now,
                )
            )
            decision = await resolve_telegram_delivery_result(
                db,
                current_server="foreign",
                job_id=claimed_editor.id,
                worker_id=claimed_editor.worker_id,
                lease_token=claimed_editor.lease_token,
                result=TelegramGatewayResult(
                    ok=False,
                    method="editMessageText",
                    status_code=403,
                    response_json={
                        "ok": False,
                        "error_code": 403,
                        "description": "Forbidden: bot lacks edit permission",
                    },
                ),
                retry_after_safety_seconds=0.1,
                retry_base_seconds=1,
                retry_max_seconds=300,
                now=now,
            )
            await db.commit()
        self.assertEqual(decision.outcome, TelegramDeliveryOutcome.DESTINATION_PAUSED)

        claimed_primary = await self._claim("primary-after-editor-403", now=now)
        self.assertEqual(claimed_primary.id, primary.job.id)
        claimed_editor_again = await self._claim(
            "editor-after-editor-403",
            now=now,
            bot_identity="channel_editor",
        )
        self.assertIsNone(claimed_editor_again)
        async with self.Session() as db:
            persisted = await db.get(
                TelegramDeliveryJobRecord,
                another_editor.job.id,
            )
        self.assertEqual(persisted.state, TelegramDeliveryState.PENDING)

    async def test_reserved_m0_slot_delivers_later_m0_while_general_call_is_slow(self):
        slow = await self._enqueue(
            "slow-lower-priority",
            destination="private:slow-recipient",
            payload={"chat_id": 1001, "text": "slow-lower-priority"},
        )
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()
        m0_finished = asyncio.Event()

        async def gateway(method, payload, **_kwargs):
            if payload.get("text") == "slow-lower-priority":
                slow_started.set()
                await release_slow.wait()
                return TelegramGatewayResult(
                    ok=True,
                    method=method,
                    status_code=200,
                    response_json={"ok": True, "result": {"message_id": 501}},
                )
            m0_finished.set()
            return TelegramGatewayResult(
                ok=True,
                method=method,
                status_code=200,
                response_json={"ok": True, "result": True},
            )

        async def freshness_validator(_db, _job, _now):
            return TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)

        lane = SimpleNamespace(
            bot_identity="primary",
            freshness_validator=freshness_validator,
            lifecycle_feedback=_NoopLifecycleFeedback(),
            gateway_call=gateway,
            dispatch_limiter=_AllowLimiter(),
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
        ), patch(
            "core.telegram_delivery_queue_worker.assert_background_job_authority"
        ), patch.object(
            queue_worker.settings,
            "telegram_delivery_queue_primary_concurrency",
            2,
        ), patch.object(
            queue_worker.settings,
            "telegram_delivery_queue_primary_m0_reserved_concurrency",
            1,
        ), patch.object(
            queue_worker.settings,
            "telegram_delivery_queue_worker_interval_seconds",
            0.1,
        ):
            general_task = asyncio.create_task(
                run_telegram_delivery_queue_cycle(
                    bot_identity="primary",
                    limit=1,
                    freshness_validator=freshness_validator,
                    lifecycle_feedback=lane.lifecycle_feedback,
                    gateway_call=gateway,
                    dispatch_limiter=lane.dispatch_limiter,
                    worker_id="primary-general-hol-test",
                    recover_leases=False,
                )
            )
            try:
                await asyncio.wait_for(slow_started.wait(), timeout=2)
                m0 = await self._enqueue(
                    "later-m0-callback",
                    action=TelegramDeliveryAction.CALLBACK_DEADLINE,
                    destination="private:callback-recipient",
                    method="answerCallbackQuery",
                    payload={"callback_query_id": "later-m0"},
                    deadline=utc_now() + timedelta(seconds=2),
                )
                reserved_report = await asyncio.wait_for(
                    run_telegram_delivery_queue_cycle(
                        bot_identity="primary",
                        limit=1,
                        freshness_validator=freshness_validator,
                        lifecycle_feedback=lane.lifecycle_feedback,
                        gateway_call=gateway,
                        dispatch_limiter=lane.dispatch_limiter,
                        worker_id="primary-reserved-m0-hol-test",
                        recover_leases=False,
                        maximum_effective_priority=0,
                    ),
                    timeout=2,
                )
                self.assertEqual(reserved_report.processed_count, 1)
                self.assertTrue(m0_finished.is_set())
                self.assertFalse(release_slow.is_set())
            finally:
                release_slow.set()
                await asyncio.wait_for(general_task, timeout=2)

            async with self.Session() as db:
                persisted_slow = await db.get(
                    TelegramDeliveryJobRecord,
                    slow.job.id,
                )
                persisted_m0 = await db.get(
                    TelegramDeliveryJobRecord,
                    m0.job.id,
                )

        self.assertEqual(persisted_slow.state, TelegramDeliveryState.SENT)
        self.assertEqual(persisted_m0.state, TelegramDeliveryState.SENT)

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
                    lifecycle_feedback=_NoopLifecycleFeedback(),
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
                lifecycle_feedback=_NoopLifecycleFeedback(),
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
                    lifecycle_feedback=_NoopLifecycleFeedback(),
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
