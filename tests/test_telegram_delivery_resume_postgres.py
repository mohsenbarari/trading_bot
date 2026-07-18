import asyncio
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import redis.asyncio as redis_async
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.services.telegram_delivery_queue_service import (
    claim_next_telegram_delivery_job,
    enqueue_telegram_delivery_job,
    load_incomplete_telegram_resume_destination_keys,
)
from core.services.telegram_delivery_resume_service import (
    TELEGRAM_RESUME_COMPLETED,
    TELEGRAM_RESUME_DATABASE_APPLIED,
    TELEGRAM_RESUME_FAILED,
    TelegramDeliveryResumeConflictError,
    TelegramDeliveryResumeEvidenceChangedError,
    TelegramDeliveryResumeIncompleteError,
    TelegramDeliveryResumeValidationError,
    resume_configured_telegram_channel,
)
from core.telegram_delivery_credentials import TelegramDeliveryCredentialRegistry
from core.telegram_delivery_preflight import (
    TelegramDeliveryPreflightFailedError,
    TelegramDeliveryPreflightIdentityReport,
    TelegramDeliveryPreflightReport,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_delivery_queue_limiter import RedisTelegramDeliveryLimiter
from core.telegram_delivery_queue_worker import (
    rehydrate_telegram_delivery_limiter_state,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_resume_operation import TelegramDeliveryResumeOperation


DATABASE_NAME_PATTERN = re.compile(r"^telegram_queue_stage3_[a-z0-9_]+_test$")
DESTINATION_KEY = "channel:-100123"
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _test_database_urls() -> tuple[str, str] | None:
    explicit = str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL", "")).strip()
    if not explicit:
        return None
    target = make_url(explicit)
    if not DATABASE_NAME_PATTERN.fullmatch(str(target.database or "").lower()):
        raise RuntimeError(
            "Telegram resume PostgreSQL tests require a "
            "telegram_queue_stage3_*_test scratch database"
        )
    return (
        target.set(drivername="postgresql+psycopg2").render_as_string(
            hide_password=False
        ),
        target.set(drivername="postgresql+asyncpg").render_as_string(
            hide_password=False
        ),
    )


DATABASE_URLS = _test_database_urls()


def _test_redis_url() -> str | None:
    value = str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL", "")).strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "redis"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port != 56379
        or parsed.path != "/15"
    ):
        raise RuntimeError(
            "Telegram resume Redis test requires isolated localhost port 56379 database 15"
        )
    return value


TEST_REDIS_URL = _test_redis_url()


def _run_alembic(sync_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = os.getcwd()
    env["TRADING_BOT_EXPECTED_ALEMBIC_HEAD"] = "fe30d1e2f4b5"
    result = subprocess.run(
        [sys.executable, "scripts/run_guarded_scratch_alembic.py", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def _settings():
    return SimpleNamespace(
        channel_id=-100123,
        telegram_delivery_queue_expected_channel_id=-100123,
        telegram_delivery_queue_channel_editor_enabled=False,
        telegram_delivery_queue_retry_after_safety_seconds=0.1,
    )


def _registry():
    return TelegramDeliveryCredentialRegistry.from_values(
        primary_token="postgres-primary-secret",
        editor_enabled=False,
    )


def _preflight_report():
    return TelegramDeliveryPreflightReport(
        approved_bot_identities=("primary",),
        channel_fingerprint="channel-fingerprint",
        identities=(
            TelegramDeliveryPreflightIdentityReport(
                bot_identity="primary",
                credential_fingerprint=_registry().fingerprints()["primary"],
                bot_fingerprint="bot-fingerprint",
                channel_fingerprint="channel-fingerprint",
                member_status="administrator",
                effective_permissions=(
                    "can_manage_chat",
                    "can_post_messages",
                    "can_edit_messages",
                ),
            ),
        ),
    )


class _ResumeLimiter:
    def __init__(self, *, fail_clears: int = 0, on_clear=None):
        self.fail_clears = fail_clears
        self.on_clear = on_clear
        self.clear_calls: list[str] = []
        self.bot_cooldowns = []

    async def clear_destination_gate_after_database_resume(self, destination_key):
        self.clear_calls.append(destination_key)
        if self.fail_clears > 0:
            self.fail_clears -= 1
            raise RuntimeError("synthetic redis outage")
        if self.on_clear is not None:
            await self.on_clear()

    async def extend_bot_cooldown(self, bot_identity, *, until):
        self.bot_cooldowns.append((bot_identity, until))


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryResumePostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "telegram_delivery_resume_operations, "
                    "telegram_delivery_jobs RESTART IDENTITY CASCADE"
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

    async def _enqueue(self, source_id: str, *, blocked: bool) -> int:
        async with self.Session() as db:
            result = await enqueue_telegram_delivery_job(
                db,
                current_server="foreign",
                feeder=TelegramFeederKind.ADMIN_SYSTEM,
                source_natural_id=source_id,
                source_version=1,
                action=TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
                bot_identity="primary",
                destination_key=DESTINATION_KEY,
                destination_class=TelegramDestinationClass.CHANNEL,
                method="sendMessage",
                payload={"chat_id": -100123, "text": source_id},
                template_version="resume-test-v1",
            )
            if blocked:
                result.job.state = TelegramDeliveryState.BLOCKED_DESTINATION
                result.job.next_retry_at = NOW
                result.job.dispatch_started_at = NOW
                result.job.provider_ok = False
                result.job.provider_status_code = 403
                result.job.provider_error_code = 403
                result.job.outcome_reason = "telegram_destination_forbidden"
                result.job.updated_at = NOW
            await db.commit()
            return int(result.job.id)

    async def _resume(
        self,
        *,
        request_id: str,
        limiter: _ResumeLimiter,
        preflight_runner,
        requested_by: str = "postgres-on-call",
    ):
        return await resume_configured_telegram_channel(
            session_factory=self.Session,
            current_server="foreign",
            settings=_settings(),
            credential_registry=_registry(),
            dispatch_limiter=limiter,
            request_id=request_id,
            requested_by=requested_by,
            preflight_runner=preflight_runner,
            now_factory=lambda: NOW,
        )

    async def test_success_is_audited_and_idempotent(self):
        job_id = await self._enqueue("resume-success", blocked=True)
        limiter = _ResumeLimiter()
        calls = 0

        async def preflight(**kwargs):
            nonlocal calls
            calls += 1
            self.assertEqual(kwargs["bot_identities"], ("primary",))
            self.assertEqual(kwargs["identity_only_bot_identities"], ())
            return _preflight_report()

        report = await self._resume(
            request_id="resume-postgres-success-0001",
            limiter=limiter,
            preflight_runner=preflight,
        )
        replay = await self._resume(
            request_id="resume-postgres-success-0001",
            limiter=limiter,
            preflight_runner=preflight,
        )

        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
        self.assertEqual(report.resumed_job_ids, (job_id,))
        self.assertTrue(replay.idempotent_replay)
        self.assertEqual(calls, 1)
        self.assertEqual(limiter.clear_calls, [DESTINATION_KEY])
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, job_id)
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            self.assertEqual(job.state, TelegramDeliveryState.PENDING_RETRY)
            self.assertIsNone(job.dispatch_started_at)
            self.assertEqual(job.outcome_reason, "operator_resume_destination")
            self.assertEqual(operation.state, TELEGRAM_RESUME_COMPLETED)
            self.assertEqual(operation.requested_by, "postgres-on-call")
            self.assertEqual(
                operation.preflight_evidence["approved_bot_identities"],
                ["primary"],
            )
            self.assertNotIn("secret", str(operation.preflight_evidence))
            self.assertEqual(
                [event["phase"] for event in operation.attempt_history],
                ["requested", "database_apply", "completed"],
            )
            self.assertNotIn("secret", str(operation.attempt_history))

    async def test_preflight_failure_keeps_pause_and_same_request_can_retry(self):
        job_id = await self._enqueue("resume-preflight-retry", blocked=True)
        limiter = _ResumeLimiter()

        async def fail_preflight(**_kwargs):
            raise TelegramDeliveryPreflightFailedError(
                "telegram_preflight_primary_permissions_missing"
            )

        with self.assertRaises(TelegramDeliveryPreflightFailedError):
            await self._resume(
                request_id="resume-postgres-preflight-0001",
                limiter=limiter,
                preflight_runner=fail_preflight,
            )
        async with self.Session() as db:
            job = await db.get(TelegramDeliveryJobRecord, job_id)
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            self.assertEqual(job.state, TelegramDeliveryState.BLOCKED_DESTINATION)
            self.assertEqual(operation.state, TELEGRAM_RESUME_FAILED)
            self.assertEqual(operation.attempt_count, 1)
        report = await self._resume(
            request_id="resume-postgres-preflight-0001",
            limiter=limiter,
            preflight_runner=lambda **_kwargs: asyncio.sleep(
                0, result=_preflight_report()
            ),
        )
        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
        self.assertEqual(report.attempt_count, 2)
        async with self.Session() as db:
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            failures = [
                event
                for event in operation.attempt_history
                if event["result"] == "failed"
            ]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["phase"], "telegram_preflight")

    async def test_redis_failure_stays_blocked_and_retry_finishes_without_resend(self):
        await self._enqueue("resume-redis-retry", blocked=True)
        limiter = _ResumeLimiter(fail_clears=1)
        preflight = lambda **_kwargs: asyncio.sleep(0, result=_preflight_report())
        with self.assertRaises(TelegramDeliveryResumeIncompleteError):
            await self._resume(
                request_id="resume-postgres-redis-0001",
                limiter=limiter,
                preflight_runner=preflight,
            )
        async with self.Session() as db:
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            incomplete = await load_incomplete_telegram_resume_destination_keys(
                db,
                current_server="foreign",
            )
            self.assertEqual(operation.state, TELEGRAM_RESUME_DATABASE_APPLIED)
            self.assertEqual(incomplete, (DESTINATION_KEY,))
        with patch(
            "core.telegram_delivery_queue_worker.AsyncSessionLocal",
            self.Session,
        ), patch(
            "core.telegram_delivery_queue_worker.current_server",
            return_value="foreign",
        ):
            rehydration = await rehydrate_telegram_delivery_limiter_state(limiter)
        self.assertEqual(
            rehydration.hard_blocked_destination_keys,
            (DESTINATION_KEY,),
        )
        self.assertEqual(rehydration.restored_count, 1)

        pending_id = await self._enqueue("resume-gated-dispatch", blocked=False)
        async with self.Session() as db:
            claimed = await claim_next_telegram_delivery_job(
                db,
                current_server="foreign",
                bot_identity="primary",
                worker_id="resume-gate-worker",
                request_timeout_seconds=5,
                lease_seconds=25,
                now=NOW,
            )
            await db.commit()
        # The resource-aware scheduler now skips a destination with an
        # incomplete resume before lease. The final dispatch marker still
        # repeats this check for claims that linearized before the gate.
        self.assertIsNone(claimed)

        report = await self._resume(
            request_id="resume-postgres-redis-0001",
            limiter=limiter,
            preflight_runner=preflight,
        )
        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
        self.assertEqual(limiter.clear_calls, [DESTINATION_KEY, DESTINATION_KEY])
        self.assertGreater(pending_id, 0)

    async def test_new_pause_during_preflight_forces_another_full_preflight(self):
        await self._enqueue("resume-race-original", blocked=True)
        limiter = _ResumeLimiter()

        async def racing_preflight(**_kwargs):
            await self._enqueue("resume-race-new", blocked=True)
            return _preflight_report()

        with self.assertRaises(TelegramDeliveryResumeEvidenceChangedError):
            await self._resume(
                request_id="resume-postgres-race-0001",
                limiter=limiter,
                preflight_runner=racing_preflight,
            )
        self.assertEqual(limiter.clear_calls, [])
        async with self.Session() as db:
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            paused_count = (
                await db.execute(
                    select(TelegramDeliveryJobRecord).where(
                        TelegramDeliveryJobRecord.state
                        == TelegramDeliveryState.BLOCKED_DESTINATION
                    )
                )
            ).scalars().all()
            self.assertEqual(operation.state, TELEGRAM_RESUME_FAILED)
            self.assertEqual(len(paused_count), 2)

        report = await self._resume(
            request_id="resume-postgres-race-0001",
            limiter=limiter,
            preflight_runner=lambda **_kwargs: asyncio.sleep(
                0, result=_preflight_report()
            ),
        )
        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
        self.assertEqual(len(report.resumed_job_ids), 2)
        self.assertEqual(report.attempt_count, 2)

    async def test_only_one_active_operator_request_owns_destination(self):
        await self._enqueue("resume-operator-race", blocked=True)
        limiter = _ResumeLimiter()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_preflight(**_kwargs):
            entered.set()
            await release.wait()
            return _preflight_report()

        first = asyncio.create_task(
            self._resume(
                request_id="resume-postgres-owner-a-0001",
                limiter=limiter,
                preflight_runner=held_preflight,
                requested_by="operator-a",
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        with self.assertRaises(TelegramDeliveryResumeConflictError):
            await self._resume(
                request_id="resume-postgres-owner-b-0001",
                limiter=limiter,
                preflight_runner=lambda **_kwargs: asyncio.sleep(
                    0, result=_preflight_report()
                ),
                requested_by="operator-b",
            )
        release.set()
        report = await asyncio.wait_for(first, timeout=2)
        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)

    async def test_active_429_refuses_before_preflight_or_control_mutation(self):
        await self._enqueue("resume-429-pause", blocked=True)
        cooldown_id = await self._enqueue("resume-429-cooldown", blocked=False)
        async with self.Session() as db:
            cooldown = await db.get(TelegramDeliveryJobRecord, cooldown_id)
            cooldown.state = TelegramDeliveryState.PENDING_RETRY
            cooldown.outcome_reason = "telegram_rate_limited"
            cooldown.next_retry_at = NOW + timedelta(seconds=120)
            cooldown.updated_at = NOW
            await db.commit()
        preflight_calls = 0

        async def preflight(**_kwargs):
            nonlocal preflight_calls
            preflight_calls += 1
            return _preflight_report()

        limiter = _ResumeLimiter()
        with self.assertRaisesRegex(
            TelegramDeliveryResumeValidationError,
            "destination_cooldown",
        ):
            await self._resume(
                request_id="resume-postgres-active-429-0001",
                limiter=limiter,
                preflight_runner=preflight,
            )
        self.assertEqual(preflight_calls, 0)
        self.assertEqual(limiter.clear_calls, [])
        async with self.Session() as db:
            operation_count = len(
                (
                    await db.execute(select(TelegramDeliveryResumeOperation))
                ).scalars().all()
            )
        self.assertEqual(operation_count, 0)

    async def test_new_pause_after_redis_clear_keeps_operation_incomplete(self):
        await self._enqueue("resume-post-redis-original", blocked=True)

        async def add_new_pause():
            await self._enqueue("resume-post-redis-new", blocked=True)

        limiter = _ResumeLimiter(on_clear=add_new_pause)
        preflight = lambda **_kwargs: asyncio.sleep(0, result=_preflight_report())
        with self.assertRaisesRegex(
            TelegramDeliveryResumeIncompleteError,
            "new_pause_after_redis",
        ):
            await self._resume(
                request_id="resume-postgres-post-redis-0001",
                limiter=limiter,
                preflight_runner=preflight,
            )
        async with self.Session() as db:
            operation = (
                await db.execute(select(TelegramDeliveryResumeOperation))
            ).scalar_one()
            self.assertEqual(operation.state, TELEGRAM_RESUME_DATABASE_APPLIED)
            self.assertEqual(
                operation.attempt_history[-1]["phase"],
                "post_redis_recheck",
            )
        recovery_limiter = _ResumeLimiter()
        report = await self._resume(
            request_id="resume-postgres-post-redis-0001",
            limiter=recovery_limiter,
            preflight_runner=preflight,
        )
        self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
        self.assertEqual(report.attempt_count, 2)

    async def test_physical_table_has_phase_check_and_partial_unique_index(self):
        async with self.Session() as db:
            constraints = set(
                (
                    await db.execute(
                        text(
                            "SELECT conname FROM pg_constraint WHERE conrelid = "
                            "'telegram_delivery_resume_operations'::regclass"
                        )
                    )
                ).scalars()
            )
            indexes = set(
                (
                    await db.execute(
                        text(
                            "SELECT indexname FROM pg_indexes WHERE tablename = "
                            "'telegram_delivery_resume_operations'"
                        )
                    )
                ).scalars()
            )
        self.assertIn(
            "ck_telegram_delivery_resume_operations_phase_timestamps",
            constraints,
        )
        self.assertIn("ux_telegram_delivery_resume_active_destination", indexes)

    async def test_real_redis_gate_is_cleared_only_through_completed_saga(self):
        if not TEST_REDIS_URL:
            self.skipTest("set TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL for real Redis test")
        await self._enqueue("resume-real-redis", blocked=True)
        redis = redis_async.from_url(TEST_REDIS_URL, decode_responses=True)
        try:
            await redis.flushdb()
            limiter = RedisTelegramDeliveryLimiter(
                redis=redis,
                bot_min_interval_seconds=0.2,
                destination_min_interval_seconds=1.0,
                rate_limit_probe_delay_seconds=0.1,
                global_rate_limit_window_seconds=2.0,
                rate_limit_probe_lease_seconds=30.0,
                key_ttl_seconds=60,
                namespace="telegram:delivery:resume-postgres-test",
            )
            redis_job = SimpleNamespace(
                id="resume-real-redis",
                bot_identity="primary",
                destination_key=DESTINATION_KEY,
            )
            await limiter.observe(
                redis_job,
                TelegramDeliveryDecision(
                    outcome=TelegramDeliveryOutcome.DESTINATION_PAUSED,
                    reason="synthetic_destination_pause",
                ),
                now=NOW,
            )
            before = await limiter.acquire(redis_job, now=NOW)
            self.assertFalse(before.allowed)
            report = await self._resume(
                request_id="resume-postgres-real-redis-0001",
                limiter=limiter,
                preflight_runner=lambda **_kwargs: asyncio.sleep(
                    0, result=_preflight_report()
                ),
            )
            after = await limiter.acquire(redis_job, now=NOW)
            self.assertEqual(report.state, TELEGRAM_RESUME_COMPLETED)
            self.assertTrue(after.allowed)
        finally:
            await redis.flushdb()
            await redis.close()


if __name__ == "__main__":
    unittest.main()
