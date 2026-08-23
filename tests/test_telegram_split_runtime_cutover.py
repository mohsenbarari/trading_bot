"""Fail-closed split forward and rollback state machine."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.schema_revision import CANONICAL_SCHEMA_HEAD
from core.telegram_bot_split_compose_operator import (
    CommandResult,
    ComposeSplitOperator,
)
from core.telegram_bot_split_cutover import (
    SPLIT_ROLLBACK_CONFIRM,
    SPLIT_START_CONFIRM,
    FORWARD_STEPS,
    InMemorySplitOperator,
    SplitCutoverController,
    require_confirmation,
    SplitCutoverError,
)
from core.telegram_central_poller_owner import TELEGRAM_CENTRAL_POLLER_LOCK_KEY
from core.telegram_delivery_queue_owner import TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY
from core.telegram_dispatch_latency_pool import compose_pool_for_bot_role


REPO_ROOT = Path(__file__).resolve().parents[1]


class TelegramSplitRuntimeCutoverTests(unittest.TestCase):
    def test_forward_success_requires_every_postcheck(self):
        operator = InMemorySplitOperator.successful()
        report = SplitCutoverController(operator).forward()
        self.assertTrue(report.ok)
        self.assertIsNone(report.rollback_ok)
        self.assertEqual(report.completed_steps, list(FORWARD_STEPS))
        self.assertIn("bot_executor", operator.started)
        self.assertIn("bot", operator.started)
        self.assertEqual(operator.queue_owners, 1)
        self.assertEqual(operator.central_pollers, 1)
        self.assertFalse(operator.owns_queue["bot"])
        self.assertFalse(operator.owns_central["bot_executor"])

    def test_success_is_not_declared_before_postchecks(self):
        operator = InMemorySplitOperator.successful()
        operator.fail_on = "unknown"
        report = SplitCutoverController(operator).forward()
        self.assertFalse(report.ok)
        self.assertNotIn("success", report.completed_steps)
        self.assertTrue(report.rollback_ok)

    def test_executor_failure_rolls_back(self):
        operator = InMemorySplitOperator.successful()
        operator.fail_on = "start_bot_executor"
        report = SplitCutoverController(operator).forward()
        self.assertFalse(report.ok)
        self.assertTrue(report.rollback_ok)
        self.assertIn("cutover_failed_rollback_succeeded", report.reasons)
        self.assertTrue(report.jobs_preserved)

    def test_missing_or_duplicate_queue_owner_fails(self):
        missing = InMemorySplitOperator.successful()
        missing.fail_on = "queue_owner_count"
        missing_report = SplitCutoverController(missing).forward()
        self.assertFalse(missing_report.ok)
        duplicate = InMemorySplitOperator.successful()
        original_count = duplicate.queue_owner_count
        seen = {"n": 0}

        def two():
            seen["n"] += 1
            if seen["n"] == 1:
                return 2
            return original_count()

        duplicate.queue_owner_count = two
        report = SplitCutoverController(duplicate).forward()
        self.assertFalse(report.ok)
        self.assertTrue(report.rollback_ok)
        duplicate.queue_owner_count = original_count

    def test_primary_failure_and_central_poller_errors(self):
        primary = InMemorySplitOperator.successful()
        primary.fail_on = "start_bot"
        report = SplitCutoverController(primary).forward()
        self.assertFalse(report.ok)
        self.assertTrue(report.rollback_ok)
        poller = InMemorySplitOperator.successful()
        poller.fail_on = "central_poller_count"
        report = SplitCutoverController(poller).forward()
        self.assertFalse(report.ok)

    def test_wrong_sha_role_and_crash_loop(self):
        sha = InMemorySplitOperator.successful()
        sha.schema = "ff5a6b7c8d9e"
        report = SplitCutoverController(sha).forward()
        self.assertFalse(report.ok)
        self.assertIn("telegram_split_schema_head_mismatch", report.reasons[0])
        crash = InMemorySplitOperator.successful()
        crash.crash_loops.add("bot_executor")
        report = SplitCutoverController(crash).forward()
        self.assertFalse(report.ok)
        self.assertIn("crash_loop", report.reasons[0])

    def test_rollback_success_preserves_jobs(self):
        operator = InMemorySplitOperator.successful()
        report = SplitCutoverController(operator).rollback()
        self.assertTrue(report.ok)
        self.assertTrue(report.jobs_preserved)
        self.assertFalse(operator.purged_jobs)
        self.assertEqual(operator.roles["bot"], "all")

    def test_rollback_failure_is_reported(self):
        operator = InMemorySplitOperator.successful()
        operator.jobs_intact = False
        report = SplitCutoverController(operator).rollback()
        self.assertFalse(report.ok)
        self.assertIn("jobs_not_preserved", report.reasons[0])

    def test_all_plus_executor_and_duplicates_are_blocked(self):
        operator = InMemorySplitOperator.successful()
        operator.running["bot"] = True
        operator.roles["bot"] = "all"
        with self.assertRaisesRegex(SplitCutoverError, "all_plus_executor"):
            operator.start_service("bot_executor", role="executor", split_enabled=True)
        operator = InMemorySplitOperator.successful()
        operator.start_service("bot_executor", role="executor", split_enabled=True)
        with self.assertRaisesRegex(SplitCutoverError, "two_executors"):
            operator.start_service("bot_executor", role="executor", split_enabled=True)
        operator.start_service("bot", role="primary", split_enabled=True)
        with self.assertRaisesRegex(SplitCutoverError, "two_primaries"):
            operator.start_service("bot", role="primary", split_enabled=True)

    def test_confirmation_and_cli(self):
        with self.assertRaises(SplitCutoverError):
            require_confirmation("nope", SPLIT_START_CONFIRM)
        require_confirmation(SPLIT_START_CONFIRM, SPLIT_START_CONFIRM)
        env = os.environ.copy()
        env["APP_ENV_FILE"] = str(REPO_ROOT / "config/unit-test.env.example")
        refused = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts/telegram_bot_split_cutover.py"), "forward"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
        self.assertEqual(refused.returncode, 2)
        ok = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/telegram_bot_split_cutover.py"),
                "forward",
                "--confirm",
                SPLIT_START_CONFIRM,
                "--operator",
                "memory",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        payload = json.loads(ok.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_head"], CANONICAL_SCHEMA_HEAD)
        compose_refused = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/telegram_bot_split_cutover.py"),
                "forward",
                "--confirm",
                SPLIT_START_CONFIRM,
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
            env=env,
        )
        self.assertEqual(compose_refused.returncode, 2)
        self.assertIn("staging_project", compose_refused.stdout)

    def test_cutover_help_does_not_load_runtime_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "unrelated.env"
            env_file.write_text("UNRELATED_RELEASE_KEY=true\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts/telegram_bot_split_cutover.py"),
                    "--help",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=str(REPO_ROOT),
                env={**os.environ, "APP_ENV_FILE": str(env_file)},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fail-closed Telegram split cutover", result.stdout)

    def test_deploy_script_exposes_official_commands(self):
        text = (REPO_ROOT / "scripts/deploy_staging.sh").read_text(encoding="utf-8")
        self.assertIn("start-split-bot-runtime", text)
        self.assertIn("rollback-split-bot-runtime", text)
        self.assertIn(SPLIT_START_CONFIRM, text)
        self.assertIn(SPLIT_ROLLBACK_CONFIRM, text)
        self.assertIn("telegram_bot_split_cutover.py", text)

    def test_bot_pool_assignment_is_role_aware(self):
        self.assertEqual(compose_pool_for_bot_role("primary")["db_pool_size"], 12)
        self.assertEqual(compose_pool_for_bot_role("all")["db_pool_size"], 15)
        self.assertEqual(compose_pool_for_bot_role("executor")["db_pool_size"], 15)

    def test_compose_operator_refuses_production_and_preserves_jobs(self):
        with self.assertRaisesRegex(SplitCutoverError, "refuses_production"):
            ComposeSplitOperator(
                lambda *args, **kwargs: CommandResult(),
                project_name="trading_bot_production",
                compose_file="compose.yml",
                env_file=".env",
                expected_sha="abc",
            )
        world = _FakeComposeWorld()
        operator = ComposeSplitOperator(
            world,
            project_name="trading_bot_staging",
            compose_file="deploy/staging/docker-compose.staging.yml",
            env_file=".env.staging",
            expected_sha="testsha",
        )
        report = SplitCutoverController(operator).forward()
        self.assertTrue(report.ok, report.reasons)
        joined = "\n".join(" ".join(args) for args in world.calls)
        self.assertIn("staging-bot-executor", joined)
        self.assertIn("up -d --no-build bot_executor", joined)
        self.assertIn("up -d --no-build bot", joined)
        self.assertNotIn("delete from telegram_delivery", joined.lower())
        self.assertFalse(operator.purged_jobs)

    def test_compose_operator_keeps_both_bot_profiles_visible_during_inspection(self):
        world = _FakeComposeWorld()
        operator = ComposeSplitOperator(
            world,
            project_name="trading_bot_staging",
            compose_file="deploy/staging/docker-compose.staging.yml",
            env_file=".env.staging",
            expected_sha="testsha",
        )

        topology = operator.record_topology()

        self.assertEqual(topology["role"], "all")
        compose_calls = [args for args in world.calls if "compose" in args]
        self.assertTrue(compose_calls)
        for args in compose_calls:
            self.assertIn("staging-bot", args)
            self.assertIn("staging-bot-executor", args)

    def test_compose_operator_detects_crash_loop_and_rolls_back(self):
        world = _FakeComposeWorld()
        world.crash_executor = True
        operator = ComposeSplitOperator(
            world,
            project_name="trading_bot_staging",
            compose_file="compose.yml",
            env_file=".env.staging",
            expected_sha="testsha",
            stable_attempts=1,
        )
        report = SplitCutoverController(operator).forward()
        self.assertFalse(report.ok)
        self.assertIn("crash_loop", report.reasons[0])
        self.assertTrue(report.rollback_ok)
        self.assertEqual(world.roles.get("bot"), "all")

    def test_harness_is_module_only(self):
        harness = (REPO_ROOT / "scripts/run_telegram_split_runtime_harness.py").read_text(
            encoding="utf-8"
        )
        probe = (REPO_ROOT / "scripts/probe_telegram_split_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("python -m scripts.run_telegram_split_runtime_harness", harness)
        self.assertIn("python -m scripts.probe_telegram_split_runtime", probe)
        help_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.run_telegram_split_runtime_harness",
                "--help",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(REPO_ROOT),
            env={
                **os.environ,
                "APP_ENV_FILE": str(REPO_ROOT / "config/unit-test.env.example"),
            },
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)


class _FakeComposeWorld:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.roles = {"bot": "all"}
        self.split = {"bot": False}
        self.running = {"bot": True, "foreign_app": True}
        self.restarts = {}
        self.queue_owners = 1
        self.central_pollers = 1
        self.schema = CANONICAL_SCHEMA_HEAD
        self.jobs = 4
        self.crash_executor = False
        self.image = "sha256:abc"

    def _inspect(self, name: str) -> dict:
        role = self.roles.get(name, "")
        service = "app" if name in {"app", "foreign_app"} else "bot"
        command = ["uvicorn", "main:app"] if service == "app" else ["python", "run_bot.py"]
        return {
            "Image": self.image,
            "State": {
                "Running": bool(self.running.get(name)),
                "RestartCount": int(self.restarts.get(name, 0)),
                "Status": "restarting" if self.restarts.get(name, 0) >= 2 else "running",
                "Health": {"Status": "healthy"},
            },
            "Config": {
                "Env": [
                    f"TELEGRAM_BOT_RUNTIME_ROLE={role}",
                    f"TELEGRAM_BOT_SPLIT_ENABLED={'true' if self.split.get(name) else 'false'}",
                    "RELEASE_SHA=testsha",
                    f"TRADING_BOT_SERVICE={service}",
                ],
                "Cmd": command,
                "Labels": {"com.docker.compose.service": name},
            },
        }

    def __call__(self, args, *, env=None):
        self.calls.append(list(args))
        if args[:2] == ["docker", "inspect"]:
            name = str(args[-1]).removeprefix("cid-")
            if name not in self.running:
                return CommandResult(returncode=1, stdout="[]")
            return CommandResult(stdout=json.dumps([self._inspect(name)]))
        if "compose" in args and "ps" in args and "-q" in args:
            name = args[-1]
            if name in self.running:
                return CommandResult(stdout=f"cid-{name}\n")
            return CommandResult(stdout="")
        if "up" in args and "-d" in args:
            name = args[-1]
            role = str((env or {}).get("STAGING_TELEGRAM_BOT_RUNTIME_ROLE") or "all")
            split = str((env or {}).get("STAGING_TELEGRAM_BOT_SPLIT_ENABLED") or "false")
            self.running[name] = True
            self.roles[name] = role
            self.split[name] = split.lower() == "true"
            if self.crash_executor and name == "bot_executor":
                self.restarts[name] = 3
            if role == "executor":
                self.queue_owners = 1
            elif role == "primary":
                self.central_pollers = 1
            elif role == "all":
                self.queue_owners = 1
                self.central_pollers = 1
            return CommandResult()
        if "stop" in args or ("rm" in args and "-f" in args):
            token = "stop" if "stop" in args else "rm"
            for name in args[args.index(token) + 1 :]:
                if name == "-f":
                    continue
                self.running.pop(name, None)
                if name == "bot_executor":
                    self.queue_owners = 0
                if name == "bot":
                    self.central_pollers = 0
                    if self.roles.get("bot") == "all":
                        self.queue_owners = 0
            return CommandResult()
        sql = args[-1] if args else ""
        if "telegram_delivery_jobs" in sql:
            return CommandResult(stdout=str(self.jobs))
        if "alembic_version" in sql:
            return CommandResult(stdout=self.schema)
        queue_obj = str(TELEGRAM_DELIVERY_QUEUE_OWNER_LOCK_KEY & 0xFFFFFFFF)
        central_obj = str(TELEGRAM_CENTRAL_POLLER_LOCK_KEY & 0xFFFFFFFF)
        if queue_obj in sql:
            return CommandResult(stdout=str(self.queue_owners))
        if central_obj in sql:
            return CommandResult(stdout=str(self.central_pollers))
        return CommandResult()


if __name__ == "__main__":
    unittest.main()
