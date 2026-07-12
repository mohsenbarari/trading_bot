from pathlib import Path
import os
import signal
import unittest
from unittest.mock import MagicMock, patch

from scripts import run_registration_scratch_suite as scratch_runner
from scripts.run_registration_scratch_suite import (
    RUNTIME_DATABASE_DENYLIST,
    SUITES,
    RegistrationScratchSuiteError,
    _admin_engine,
    _database_snapshot,
    _migration_command,
    _raise_termination,
    _render,
    _run,
    _sync_url,
    _test_command,
)


class RegistrationScratchSuiteStaticTests(unittest.TestCase):
    def test_wrapper_is_no_deps_and_never_invokes_migration_service(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("docker compose run --rm --no-deps", wrapper)
        self.assertNotIn(" compose up", wrapper)
        self.assertNotIn(" run --rm migration", wrapper)
        self.assertIn("STAGE9_SCRATCH_DATABASES_ALLOWED=true", wrapper)
        self.assertIn("ENVIRONMENT=test", wrapper)
        self.assertIn('postgres_name="trading_bot_stage9_postgres_$$"', wrapper)
        self.assertIn("-v /var/lib/postgresql/data", wrapper)
        self.assertIn('"$data_mount_name" == *postgres_data', wrapper)
        self.assertIn("Stage 9 PostgreSQL disposable resource", wrapper)
        self.assertIn('docker rm -fv "$postgres_name"', wrapper)
        self.assertIn('-e DATABASE_URL="$runtime_async_url"', wrapper)
        self.assertIn('-e SYNC_DATABASE_URL="$runtime_sync_url"', wrapper)

    def test_runner_requires_test_environment_and_explicit_opt_in(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.py"
        ).read_text(encoding="utf-8")
        self.assertIn("STAGE9_SCRATCH_DATABASES_ALLOWED", source)
        self.assertIn('{"test", "testing", "ci"}', source)
        self.assertIn("signal.SIGTERM", source)
        self.assertIn("_raise_termination", source)

    def test_every_suite_uses_an_allowlisted_scratch_prefix(self):
        self.assertEqual(
            {prefix for prefix, _env_name, _module in SUITES},
            {
                "stage1_migration",
                "stage1_counter",
                "stage2_registration",
                "stage3_registration",
                "stage4_registration",
            },
        )
        self.assertIn("trading_bot_db", RUNTIME_DATABASE_DENYLIST)

    def test_wrapper_coverage_mode_writes_only_to_mounted_tmp(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("STAGE9_COVERAGE_FILE=/app/tmp/stage9-postgres-coverage/.coverage", wrapper)
        self.assertIn("PYTHONPATH=/app/stage9-test-packages-py311", wrapper)
        self.assertIn('-v "$repo_root/tmp:/app/tmp"', wrapper)
        self.assertIn(
            '-v "$repo_root/tmp/stage9-site-packages-py311:/app/stage9-test-packages-py311:ro"',
            wrapper,
        )
        self.assertIn(
            'find "$repo_root/tmp/stage9-postgres-coverage" -maxdepth 1 -type f '
            "-name '.coverage.*' -delete",
            wrapper,
        )

    def test_coverage_mode_collects_branch_data(self):
        command = _test_command("tests.example", coverage_file="/tmp/.coverage")
        self.assertIn("--branch", command)
        self.assertIn("--parallel-mode", command)
        migration = _migration_command("head", coverage_file="/tmp/.coverage")
        self.assertIn("--branch", migration)
        self.assertIn("--parallel-mode", migration)

    def test_wrapper_coverage_mode_instruments_the_orchestrator(self):
        wrapper = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "run_registration_scratch_suite.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("runner_command=(", wrapper)
        self.assertIn("python -m coverage run --branch --parallel-mode", wrapper)
        self.assertIn('bot "${runner_command[@]}"', wrapper)

    def test_helper_commands_urls_and_signal_failure_are_bounded(self):
        raw = "postgresql+asyncpg://user:pass@db:5433/stage9_runtime"
        sync = _sync_url(raw)
        self.assertEqual(sync.drivername, "postgresql+psycopg2")
        self.assertIn("pass", _render(sync))
        self.assertEqual(
            _test_command("tests.example", coverage_file=""),
            [scratch_runner.sys.executable, "-m", "unittest", "-v", "tests.example"],
        )
        self.assertEqual(
            _migration_command("head", coverage_file=""),
            [
                scratch_runner.sys.executable,
                "scripts/run_guarded_scratch_alembic.py",
                "upgrade",
                "head",
            ],
        )
        with self.assertRaisesRegex(RegistrationScratchSuiteError, "terminated_by_signal"):
            _raise_termination(signal.SIGTERM, None)

    def test_database_snapshot_reads_revision_and_always_disposes(self):
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            MagicMock(scalar_one=MagicMock(return_value="stage9_runtime")),
            MagicMock(scalar_one=MagicMock(return_value=True)),
            MagicMock(scalar_one_or_none=MagicMock(return_value="head-a")),
            MagicMock(scalar_one=MagicMock(return_value=3)),
        ]
        with patch.object(scratch_runner, "create_engine", return_value=engine):
            self.assertEqual(
                _database_snapshot("postgresql://user:pass@db/stage9_runtime"),
                ("stage9_runtime", "head-a", 3),
            )
        engine.dispose.assert_called_once()

        engine.reset_mock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.side_effect = [
            MagicMock(scalar_one=MagicMock(return_value="stage9_runtime")),
            MagicMock(scalar_one=MagicMock(return_value=False)),
            MagicMock(scalar_one=MagicMock(return_value=0)),
        ]
        with patch.object(scratch_runner, "create_engine", return_value=engine):
            self.assertEqual(
                _database_snapshot("postgresql://user:pass@db/stage9_runtime"),
                ("stage9_runtime", None, 0),
            )

    def test_admin_engine_rejects_system_database_and_uses_autocommit(self):
        for raw in (
            "postgresql://user:pass@db/postgres",
            "postgresql://user:pass@db/template0",
            "postgresql://user:pass@db/",
        ):
            with self.subTest(raw=raw), self.assertRaises(RegistrationScratchSuiteError):
                _admin_engine(raw)
        with patch.object(scratch_runner, "create_engine", return_value="engine") as create:
            self.assertEqual(
                _admin_engine("postgresql://user:pass@db/stage9_runtime"),
                "engine",
            )
        self.assertEqual(create.call_args.args[0].database, "postgres")
        self.assertEqual(create.call_args.kwargs["isolation_level"], "AUTOCOMMIT")

    def test_run_raises_only_for_nonzero_child_status(self):
        with patch.object(
            scratch_runner.subprocess,
            "run",
            return_value=MagicMock(returncode=0),
        ):
            _run(["ok"], env={})
        with patch.object(
            scratch_runner.subprocess,
            "run",
            return_value=MagicMock(returncode=9),
        ):
            with self.assertRaisesRegex(RegistrationScratchSuiteError, "exit code 9"):
                _run(["bad", "command"], env={})

    def _safe_environment(self):
        return {
            "DATABASE_URL": "postgresql+asyncpg://user:pass@db/stage9_runtime",
            "TRADING_BOT_EXPECTED_CHECKOUT": str(scratch_runner.REPO_ROOT),
            "STAGE9_SCRATCH_DATABASES_ALLOWED": "true",
            "ENVIRONMENT": "test",
        }

    def test_main_environment_guards_fail_before_database_creation(self):
        cases = [
            ({}, "DATABASE_URL"),
            ({"DATABASE_URL": "postgresql://db/stage9_runtime"}, "opt-in"),
            (
                {
                    "DATABASE_URL": "postgresql://db/stage9_runtime",
                    "STAGE9_SCRATCH_DATABASES_ALLOWED": "true",
                    "ENVIRONMENT": "production",
                },
                "unsafe environment",
            ),
            (
                {
                    "DATABASE_URL": "postgresql://db/stage9_runtime",
                    "STAGE9_SCRATCH_DATABASES_ALLOWED": "true",
                    "ENVIRONMENT": "test",
                    "TRADING_BOT_EXPECTED_CHECKOUT": "/tmp/not-this-checkout",
                },
                "checkout mismatch",
            ),
        ]
        for environment, _message in cases:
            with self.subTest(message=_message), patch.dict(os.environ, environment, clear=True), patch.object(
                scratch_runner,
                "_database_snapshot",
            ) as snapshot:
                self.assertEqual(scratch_runner.main(), 2)
                snapshot.assert_not_called()

    def test_main_rejects_unexpected_runtime_database(self):
        with patch.dict(os.environ, self._safe_environment(), clear=True), patch.object(
            scratch_runner,
            "_database_snapshot",
            return_value=("postgres", None, 0),
        ), patch.object(scratch_runner, "_admin_engine") as admin:
            self.assertEqual(scratch_runner.main(), 2)
            admin.assert_not_called()

    def _run_main_with_mocks(
        self, *, existing=(), child_error=None, after=None, coverage_file=""
    ):
        before = ("stage9_runtime", None, 0)
        environment = self._safe_environment()
        if coverage_file:
            environment["STAGE9_COVERAGE_FILE"] = coverage_file
        admin = MagicMock()
        connection = admin.connect.return_value.__enter__.return_value
        query_result = MagicMock()
        query_result.scalars.return_value = set(existing)
        connection.execute.return_value = query_result
        snapshots = [before, before if after is None else after]
        run_side_effect = child_error
        with patch.dict(os.environ, environment, clear=True), patch.object(
            scratch_runner,
            "_database_snapshot",
            side_effect=snapshots,
        ), patch.object(scratch_runner, "_admin_engine", return_value=admin), patch.object(
            scratch_runner.secrets,
            "token_hex",
            return_value="abcdef123456",
        ), patch.object(scratch_runner, "_run", side_effect=run_side_effect) as run, patch.object(
            scratch_runner.signal,
            "getsignal",
            side_effect=lambda value: f"handler-{value}",
        ), patch.object(scratch_runner.signal, "signal") as set_signal:
            result = scratch_runner.main()
        return result, admin, connection, run, set_signal

    def test_main_success_creates_runs_and_drops_every_scratch_database(self):
        result, admin, connection, run, set_signal = self._run_main_with_mocks(
            coverage_file="/tmp/.coverage"
        )
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, len(SUITES) * 2)
        sql = "\n".join(str(item.args[0]) for item in connection.exec_driver_sql.call_args_list)
        for prefix, _env_name, _module in SUITES:
            self.assertIn(f'CREATE DATABASE "{prefix}_abcdef123456"', sql)
            self.assertIn(f'DROP DATABASE IF EXISTS "{prefix}_abcdef123456"', sql)
        admin.dispose.assert_called_once()
        self.assertEqual(set_signal.call_count, 4)
        self.assertTrue(
            all(
                item.kwargs["env"].get("COVERAGE_FILE") == "/tmp/.coverage"
                for item in run.call_args_list
            )
        )

    def test_main_cleans_up_after_collision_or_child_failure_and_rejects_snapshot_drift(self):
        collision, admin, _connection, run, _signals = self._run_main_with_mocks(
            existing={"stage1_migration_abcdef123456"}
        )
        self.assertEqual(collision, 1)
        run.assert_not_called()
        admin.dispose.assert_called_once()

        failed, admin, _connection, run, _signals = self._run_main_with_mocks(
            child_error=RegistrationScratchSuiteError("child failed")
        )
        self.assertEqual(failed, 1)
        self.assertEqual(run.call_count, 1)
        admin.dispose.assert_called_once()

        drifted, _admin, _connection, _run_mock, _signals = self._run_main_with_mocks(
            after=("stage9_runtime", "unexpected", 1)
        )
        self.assertEqual(drifted, 2)


if __name__ == "__main__":
    unittest.main()
