from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.services.telegram_delivery_observability_service import (
    TelegramQueueHealthThresholds,
    build_telegram_delivery_shadow_plan,
    inspect_telegram_delivery_queue_health,
)
from core.services.telegram_delivery_queue_service import enqueue_telegram_delivery_job
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.utils import utc_now
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_delivery_provider_outcome import (
    TELEGRAM_PROVIDER_OUTCOME_PENDING,
    TelegramDeliveryProviderOutcomeRecord,
)
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryObservabilityPostgresTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_url, _ = DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "head")

    async def asyncSetUp(self):
        _, async_url = DATABASE_URLS
        self.engine = create_async_engine(async_url, pool_pre_ping=True)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE telegram_delivery_provider_outcomes, "
                    "telegram_delivery_reconciliation_evidence, "
                    "telegram_delivery_runtime_gates, telegram_delivery_jobs "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _job(
        self,
        db,
        *,
        key: str,
        run_id: str = "observability-run",
        bot_identity: str = "primary",
        freshness_deadline_at=None,
    ) -> TelegramDeliveryJobRecord:
        editor = bot_identity == "channel_editor"
        result = await enqueue_telegram_delivery_job(
            db,
            current_server="foreign",
            feeder=(
                TelegramFeederKind.OFFER_EDIT
                if editor
                else TelegramFeederKind.ADMIN_SYSTEM
            ),
            source_natural_id=f"observability:{key}",
            source_version=1,
            action=(
                TelegramDeliveryAction.EXPIRED_OFFER_EDIT
                if editor
                else TelegramDeliveryAction.GENERAL_ANNOUNCEMENT
            ),
            bot_identity=bot_identity,
            destination_key=(
                "channel:-1009988776655" if editor else f"private:{key}:998877"
            ),
            destination_class=(
                TelegramDestinationClass.CHANNEL
                if editor
                else TelegramDestinationClass.PRIVATE
            ),
            method="editMessageText" if editor else "sendMessage",
            payload=(
                {
                    "chat_id": -1009988776655,
                    "message_id": len(key) + 10,
                    "text": "synthetic edit",
                }
                if editor
                else {"chat_id": 998877, "text": "synthetic message"}
            ),
            template_version="observability-test-v1",
            freshness_deadline_at=freshness_deadline_at,
            source_order_at=utc_now() if editor else None,
            run_id=run_id,
        )
        return result.job

    async def _seed_unhealthy_snapshot(self):
        now = utc_now()
        async with self.Session() as db:
            ready = await self._job(db, key="raw-user-1001")
            ready.created_at = now - timedelta(seconds=20)
            ready.provider_attempt_count = 3
            ready.last_rate_limited_at = now - timedelta(seconds=5)
            ready.last_retry_after_seconds = 4

            editor = await self._job(
                db,
                key="raw-channel-2002",
                bot_identity="channel_editor",
            )
            editor.created_at = now - timedelta(seconds=8)

            missed = await self._job(
                db,
                key="missed-3003",
                freshness_deadline_at=now - timedelta(seconds=1),
            )
            ambiguous = await self._job(db, key="ambiguous-4004")
            ambiguous.state = TelegramDeliveryState.AMBIGUOUS
            unresolved = await self._job(db, key="unresolved-5005")
            unresolved.state = TelegramDeliveryState.AMBIGUOUS_UNRESOLVED
            blocked = await self._job(db, key="blocked-6006")
            blocked.state = TelegramDeliveryState.BLOCKED_DESTINATION
            failed = await self._job(db, key="failed-7007")
            failed.state = TelegramDeliveryState.TERMINAL_FAILED
            failed.terminal_at = now
            leased = await self._job(db, key="leased-8008")
            leased.state = TelegramDeliveryState.LEASED
            leased.lease_until = now - timedelta(seconds=2)
            leased.worker_id = "synthetic-worker"

            outside = await self._job(
                db,
                key="must-not-appear-9009",
                run_id="different-run",
            )
            outside.created_at = now - timedelta(days=1)

            db.add(
                TelegramDeliveryProviderOutcomeRecord(
                    job_id=ready.id,
                    lease_token=1,
                    worker_id="synthetic-worker",
                    bot_identity="primary",
                    method="sendMessage",
                    transport_phase="response_received",
                    gateway_ok=False,
                    provider_status_code=503,
                    outcome_hash="c" * 64,
                    apply_state=TELEGRAM_PROVIDER_OUTCOME_PENDING,
                    apply_attempt_count=0,
                    created_at=now - timedelta(seconds=40),
                )
            )
            db.add(
                TelegramDeliveryRuntimeGate(
                    gate_key="gateway:telegram",
                    scope="gateway",
                    bot_identity=None,
                    state="blocked",
                    reason_code="synthetic_observability_test",
                )
            )
            await db.commit()

    async def test_health_is_run_scoped_aggregate_and_emits_stop_reasons(self):
        await self._seed_unhealthy_snapshot()
        now = utc_now()
        async with self.Session() as db:
            report = await inspect_telegram_delivery_queue_health(
                db,
                current_server="foreign",
                run_id="observability-run",
                now=now,
            )

        self.assertEqual(report["scope"], "run")
        self.assertEqual(report["decision"], "stop")
        self.assertGreaterEqual(report["ready_depth"], 3)
        self.assertGreaterEqual(report["oldest_ready_age_seconds"], 19)
        self.assertEqual(report["ambiguous_count"], 2)
        self.assertEqual(report["unresolved_count"], 1)
        self.assertEqual(report["blocked_count"], 1)
        self.assertEqual(report["terminal_failure_count"], 1)
        self.assertEqual(report["expired_lease_count"], 1)
        self.assertEqual(report["missed_freshness_count"], 1)
        self.assertEqual(report["pending_provider_outcome_count"], 1)
        self.assertIn("provider_outcome_apply_age_stop", report["stop_reasons"])
        rendered = json.dumps(report, sort_keys=True)
        for raw_identity in (
            "raw-user-1001",
            "raw-channel-2002",
            "must-not-appear-9009",
            "-1009988776655",
            "998877",
        ):
            self.assertNotIn(raw_identity, rendered)

    async def test_shadow_plan_is_read_only_bounded_and_hides_raw_identity(self):
        await self._seed_unhealthy_snapshot()
        async with self.Session() as db:
            before = (
                await db.execute(
                    select(
                        TelegramDeliveryJobRecord.id,
                        TelegramDeliveryJobRecord.state,
                        TelegramDeliveryJobRecord.attempt_count,
                        TelegramDeliveryJobRecord.lease_token,
                    ).order_by(TelegramDeliveryJobRecord.id)
                )
            ).all()
            plan = await build_telegram_delivery_shadow_plan(
                db,
                current_server="foreign",
                run_id="observability-run",
                limit_per_lane=10,
            )
            after = (
                await db.execute(
                    select(
                        TelegramDeliveryJobRecord.id,
                        TelegramDeliveryJobRecord.state,
                        TelegramDeliveryJobRecord.attempt_count,
                        TelegramDeliveryJobRecord.lease_token,
                    ).order_by(TelegramDeliveryJobRecord.id)
                )
            ).all()

        self.assertEqual(before, after)
        self.assertEqual(plan["database_mutations"], 0)
        self.assertEqual(plan["provider_calls"], 0)
        self.assertGreaterEqual(plan["candidate_count"], 2)
        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("raw-user-1001", rendered)
        self.assertNotIn("raw-channel-2002", rendered)
        self.assertNotIn("destination_key", rendered)
        self.assertTrue(all(len(item["correlation_hash"]) == 64 for item in plan["candidates"]))

    async def test_age_threshold_transitions_continue_warning_and_stop(self):
        now = utc_now()
        async with self.Session() as db:
            job = await self._job(db, key="age-transition", run_id="age-run")
            job.created_at = now - timedelta(seconds=20)
            await db.commit()

        async with self.Session() as db:
            warning = await inspect_telegram_delivery_queue_health(
                db,
                current_server="foreign",
                run_id="age-run",
                thresholds=TelegramQueueHealthThresholds(
                    warning_oldest_ready_age_seconds=10,
                    stop_oldest_ready_age_seconds=30,
                ),
                now=now,
            )
            continuing = await inspect_telegram_delivery_queue_health(
                db,
                current_server="foreign",
                run_id="age-run",
                thresholds=TelegramQueueHealthThresholds(
                    warning_oldest_ready_age_seconds=25,
                    stop_oldest_ready_age_seconds=30,
                ),
                now=now,
            )
            stopped = await inspect_telegram_delivery_queue_health(
                db,
                current_server="foreign",
                run_id="age-run",
                thresholds=TelegramQueueHealthThresholds(
                    warning_oldest_ready_age_seconds=5,
                    stop_oldest_ready_age_seconds=15,
                ),
                now=now,
            )

        self.assertEqual(warning["decision"], "warning")
        self.assertEqual(warning["alerts"][0]["code"], "oldest_ready_age_warning")
        self.assertEqual(continuing["decision"], "continue")
        self.assertEqual(stopped["decision"], "stop")
        self.assertIn("oldest_ready_age_stop", stopped["stop_reasons"])

    async def test_cli_uses_read_only_transaction_and_writes_clean_evidence(self):
        async with self.Session() as db:
            job = await self._job(db, key="cli-clean", run_id="cli-run")
            job_id = int(job.id)
            await db.commit()
        _, async_url = DATABASE_URLS
        database_name = str(make_url(async_url).database)
        repo = Path(__file__).resolve().parents[1]
        script = repo / "scripts" / "report_telegram_delivery_queue_health.py"
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "health.json"
            env = dict(os.environ)
            env["TELEGRAM_QUEUE_OBSERVABILITY_DATABASE_URL"] = str(
                os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL")
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--environment",
                    "synthetic-test",
                    "--expected-database-name",
                    database_name,
                    "--run-id",
                    "cli-run",
                    "--include-shadow",
                    "--warning-oldest-ready-age",
                    "1000",
                    "--stop-oldest-ready-age",
                    "2000",
                    "--report",
                    str(report_path),
                ],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            output = json.loads(result.stdout)
            written = json.loads(report_path.read_text(encoding="utf-8"))
            scan = json.loads(
                report_path.with_suffix(".json.security-scan.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(written["environment"], "synthetic-test")
        self.assertEqual(written["database_mode"], "transaction_read_only")
        self.assertEqual(written["provider_network_calls"], 0)
        self.assertEqual(output["evidence"]["security_scan_status"], "clean")
        self.assertEqual(scan["status"], "clean")
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, job_id)
            self.assertEqual(persisted.state, TelegramDeliveryState.PENDING)
            self.assertEqual(persisted.attempt_count, 0)
            self.assertEqual(persisted.lease_token, 0)


if __name__ == "__main__":
    unittest.main()
