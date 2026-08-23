from pathlib import Path
import json
import os
import subprocess
import sys
import unittest

from core.telegram_bot_runtime_role import TelegramBotRuntimeRoleError
from core.telegram_bot_runtime_topology import assert_telegram_bot_deploy_topology
from tests.test_deployment_surface_guard import compose_service_block


REPO_ROOT = Path(__file__).resolve().parents[1]


class TelegramSplitRuntimeDeployTests(unittest.TestCase):
    def test_default_deploy_stays_combined_all(self):
        staging = (REPO_ROOT / "scripts/deploy_staging.sh").read_text(encoding="utf-8")
        self.assertIn('STAGING_TELEGRAM_BOT_SPLIT_ENABLED="${STAGING_TELEGRAM_BOT_SPLIT_ENABLED:-0}"', staging)
        self.assertIn(
            "compose --profile staging-bot --profile staging-sync up -d --build foreign_app bot foreign_sync_worker",
            staging,
        )
        self.assertIn(
            "compose --profile staging-bot --profile staging-sync up -d --no-build bot",
            staging,
        )
        self.assertIn("start_split_bot_runtime", staging)
        self.assertIn("rollback_split_bot_runtime", staging)
        self.assertIn("staging-bot-executor", staging)
        self.assertIn("bot_executor", staging)

    def test_production_default_writers_stay_app_bot_sync(self):
        production = (REPO_ROOT / "scripts/production_deploy_online.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("for service in app bot sync_worker; do", production)
        self.assertIn("up --no-start --force-recreate --no-deps app bot sync_worker", production)
        self.assertIn("bot_executor", production)
        self.assertIn("profile bot-executor", production)
        self.assertIn("TELEGRAM_BOT_SPLIT_PREFLIGHT_SCRIPT", production)

    def test_compose_does_not_build_two_executor_services(self):
        production = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        staging = (
            REPO_ROOT / "deploy/staging/docker-compose.staging.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(production.count("\n  bot_executor:"), 1)
        self.assertNotIn("\n  bot_publishers:", production)
        self.assertEqual(staging.count("\n  bot_executor:"), 1)
        self.assertNotIn("\n  bot_publishers:", staging)
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            "TELEGRAM_BOT_SPLIT_ENABLED: ${TELEGRAM_BOT_SPLIT_ENABLED:-false}",
            compose,
        )
        self.assertIn("TELEGRAM_BOT_SPLIT_ENABLED: \"true\"", compose)

    def test_preflight_rejects_all_plus_executor(self):
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_deploy_topology(
                split_enabled=True,
                bot_role="all",
                executor_enabled=True,
            )
        with self.assertRaises(TelegramBotRuntimeRoleError):
            assert_telegram_bot_deploy_topology(
                split_enabled=False,
                bot_role="all",
                executor_enabled=True,
            )
        incomplete = assert_telegram_bot_deploy_topology(
            split_enabled=True,
            bot_role="primary",
            executor_enabled=False,
        )
        self.assertTrue(incomplete.can_start)
        self.assertFalse(incomplete.promotable)
        complete = assert_telegram_bot_deploy_topology(
            split_enabled=True,
            bot_role="primary",
            executor_enabled=True,
        )
        self.assertTrue(complete.promotable)

    def test_preflight_script_returns_incomplete_for_primary_without_executor(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/telegram_bot_split_preflight.py"),
                "--bot-role",
                "primary",
                "--split-enabled",
                "true",
                "--executor-enabled",
                "false",
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
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 4)
        self.assertTrue(payload["can_start"])
        self.assertFalse(payload["promotable"])
        self.assertNotIn("token", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
