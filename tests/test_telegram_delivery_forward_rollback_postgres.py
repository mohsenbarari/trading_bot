import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.services.telegram_delivery_queue_service import enqueue_telegram_delivery_job
from core.services.telegram_delivery_rollback_service import (
    inspect_telegram_delivery_forward_rollback_readiness,
)
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
from models.telegram_delivery_resume_operation import TelegramDeliveryResumeOperation
from models.telegram_delivery_runtime_gate import TelegramDeliveryRuntimeGate
from tests.test_telegram_delivery_queue_postgres import DATABASE_URLS, _run_alembic


EXPECTED_HEAD = "b320c1d2e3f4"


@unittest.skipUnless(
    DATABASE_URLS,
    "set TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL to an isolated scratch database",
)
class TelegramDeliveryForwardRollbackPostgresTests(unittest.IsolatedAsyncioTestCase):
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
                    "TRUNCATE TABLE telegram_delivery_resume_operations, "
                    "telegram_delivery_provider_outcomes, "
                    "telegram_delivery_reconciliation_evidence, "
                    "telegram_delivery_runtime_gates, telegram_delivery_jobs "
                    "RESTART IDENTITY CASCADE"
                )
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _job(self, db, *, key: str) -> TelegramDeliveryJobRecord:
        result = await enqueue_telegram_delivery_job(
            db,
            current_server="foreign",
            feeder=TelegramFeederKind.ADMIN_SYSTEM,
            source_natural_id=f"rollback:{key}",
            source_version=1,
            action=TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
            bot_identity="primary",
            destination_key=f"private:rollback:{key}",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload={"chat_id": 771122, "text": "synthetic rollback test"},
            template_version="rollback-test-v1",
        )
        return result.job

    async def _inspect(self, db, **overrides):
        values = {
            "current_server": "foreign",
            "expected_schema_head": EXPECTED_HEAD,
            "execution_owner": "legacy",
            "queue_worker_enabled": False,
            "cutover_ready": False,
            "producer_quiesced": True,
            "migration_stage_skipped": True,
            "backup_manifest_verified": True,
        }
        values.update(overrides)
        return await inspect_telegram_delivery_forward_rollback_readiness(db, **values)

    async def test_terminal_history_is_compatible_with_schema_preserving_legacy_rollback(self):
        async with self.Session() as db:
            terminal = await self._job(db, key="terminal-history")
            terminal.state = TelegramDeliveryState.SENT
            terminal.sent_at = utc_now()
            terminal.terminal_at = utc_now()
            await db.commit()

        async with self.Session() as db:
            report = await self._inspect(db)

        self.assertEqual(report["decision"], "ready")
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["schema_strategy"], "preserve_current_schema")
        self.assertFalse(report["schema_downgrade_allowed"])
        self.assertTrue(report["legacy_workers_enabled"])
        self.assertFalse(report["queue_worker_enabled"])
        self.assertEqual(report["active_job_count"], 0)
        self.assertEqual(report["provider_network_calls"], 0)
        self.assertEqual(report["database_mutations"], 0)

    async def test_every_unresolved_control_plane_class_blocks_rollback(self):
        async with self.Session() as db:
            active = await self._job(db, key="active-private-raw")
            unresolved = await self._job(db, key="unresolved-private-raw")
            unresolved.state = TelegramDeliveryState.AMBIGUOUS_UNRESOLVED
            db.add(
                TelegramDeliveryProviderOutcomeRecord(
                    job_id=active.id,
                    lease_token=1,
                    worker_id="synthetic-worker",
                    bot_identity="primary",
                    method="sendMessage",
                    transport_phase="write_unknown",
                    gateway_ok=False,
                    outcome_hash="e" * 64,
                    apply_state=TELEGRAM_PROVIDER_OUTCOME_PENDING,
                    apply_attempt_count=0,
                )
            )
            db.add(
                TelegramDeliveryResumeOperation(
                    request_id="rollback-request-0001",
                    scope="channel_destination",
                    destination_key="channel:-100771122",
                    bot_identities=["primary"],
                    pause_job_ids=[],
                    pause_evidence_hash="f" * 64,
                    requested_by="synthetic-operator",
                    state="requested",
                    attempt_count=0,
                )
            )
            db.add(
                TelegramDeliveryRuntimeGate(
                    gate_key="gateway:telegram",
                    scope="gateway",
                    bot_identity=None,
                    state="blocked",
                    reason_code="synthetic_rollback_test",
                )
            )
            await db.commit()

        async with self.Session() as db:
            report = await self._inspect(
                db,
                producer_quiesced=False,
                migration_stage_skipped=False,
                backup_manifest_verified=False,
            )

        self.assertEqual(report["decision"], "blocked")
        for blocker in (
            "producer_quiescence_not_confirmed",
            "rollback_migration_stage_not_skipped",
            "rollback_backup_manifest_not_verified",
            "active_queue_jobs_present",
            "unresolved_queue_jobs_present",
            "pending_provider_outcomes_present",
            "incomplete_resume_operations_present",
            "active_runtime_gates_present",
        ):
            self.assertIn(blocker, report["blockers"])
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("active-private-raw", rendered)
        self.assertNotIn("unresolved-private-raw", rendered)
        self.assertNotIn("-100771122", rendered)

    async def test_schema_head_and_legacy_config_mismatch_fail_closed(self):
        async with self.Session() as db:
            report = await self._inspect(
                db,
                expected_schema_head="not-the-current-head",
                execution_owner="queue-v1",
                queue_worker_enabled=True,
                cutover_ready=True,
            )
        self.assertEqual(report["decision"], "blocked")
        self.assertIn("schema_head_mismatch", report["blockers"])
        self.assertIn("legacy_runtime_config_invalid", report["blockers"])

    async def test_cli_is_read_only_and_security_scans_ready_report(self):
        async with self.Session() as db:
            terminal = await self._job(db, key="cli-terminal")
            terminal.state = TelegramDeliveryState.SENT
            terminal.sent_at = utc_now()
            terminal.terminal_at = utc_now()
            terminal_id = int(terminal.id)
            await db.commit()

        _, async_url = DATABASE_URLS
        database_name = str(make_url(async_url).database)
        repo = Path(__file__).resolve().parents[1]
        script = repo / "scripts" / "check_telegram_delivery_forward_rollback.py"
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "rollback.json"
            env = dict(os.environ)
            env["TELEGRAM_QUEUE_ROLLBACK_DATABASE_URL"] = str(
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
                    "--expected-schema-head",
                    EXPECTED_HEAD,
                    "--producer-quiesced",
                    "--migration-stage-skipped",
                    "--backup-manifest-sha256",
                    "d" * 64,
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
            report = json.loads(report_path.read_text(encoding="utf-8"))
            scan = json.loads(
                report_path.with_suffix(".json.security-scan.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["readiness"]["decision"], "ready")
        self.assertEqual(report["database_mode"], "transaction_read_only")
        self.assertEqual(output["evidence"]["security_scan_status"], "clean")
        self.assertEqual(scan["status"], "clean")
        async with self.Session() as db:
            persisted = await db.get(TelegramDeliveryJobRecord, terminal_id)
            self.assertEqual(persisted.state, TelegramDeliveryState.SENT)


if __name__ == "__main__":
    unittest.main()
