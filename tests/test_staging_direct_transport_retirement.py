from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGING_SCRIPT = REPO_ROOT / "scripts" / "deploy_staging.sh"
STAGING_COMPOSE = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"


def run_staging(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run only a retired-path rejection; these invocations must be inert."""

    env = os.environ.copy()
    for key in ("STAGING_FOREIGN_ONLY", "COMPOSE_PROFILES"):
        env.pop(key, None)
    env.update(
        {
            # A blocked path must return before DNS, Docker, or .env writes.
            "STAGING_DOMAIN": "retired-staging-route.invalid",
            "STAGING_ENABLE_SSL": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(STAGING_SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class StagingDirectTransportRetirementTests(unittest.TestCase):
    def assert_blocked_before_prerequisites(self, result: subprocess.CompletedProcess[str]) -> None:
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, msg=output)
        self.assertIn("legacy direct FI-to-IR staging transport is retired", output)
        self.assertIn("blocked before configuration/network/compose", output)
        self.assertIn("Object Storage and Writer Witness", output)
        self.assertNotIn("docker compose or docker-compose is required", output)
        self.assertNotIn("missing /", output)
        self.assertNotIn("domain=retired-staging-route.invalid", output)

    def test_retired_foreign_only_deploy_fails_before_check_or_env_initialization(self):
        result = run_staging("deploy", extra_env={"STAGING_FOREIGN_ONLY": "1"})

        self.assert_blocked_before_prerequisites(result)

    def test_retired_sync_profile_or_service_fails_before_compose(self):
        blocked_cases = (
            (("up", "--profile", "staging-sync"), {}),
            (("up", "--profile=staging-sync"), {}),
            (("up", "foreign_sync_worker"), {}),
            (("up", "sync_worker"), {}),
            (("up",), {"COMPOSE_PROFILES": "staging-sync"}),
        )

        for args, extra_env in blocked_cases:
            with self.subTest(args=args, extra_env=extra_env):
                self.assert_blocked_before_prerequisites(run_staging(*args, extra_env=extra_env))

    def test_default_deploy_and_up_cannot_start_the_retired_sync_workers(self):
        source = STAGING_SCRIPT.read_text(encoding="utf-8")
        deploy_body = source.split("deploy() {\n", 1)[1].split("\n}\n\ncase ", 1)[0]
        up_case = source.split("    up)\n", 1)[1].split("        ;;", 1)[0]
        deploy_case = source.split("    deploy)\n", 1)[1].split("        ;;", 1)[0]

        self.assertNotIn("staging-sync", deploy_body)
        self.assertNotIn("sync_worker", deploy_body)
        self.assertNotIn("foreign_sync_worker", deploy_body)
        self.assertIn("compose --profile staging-bot up -d --build", deploy_body)
        self.assertIn("compose up -d --build", deploy_body)
        self.assertLess(
            up_case.index("assert_legacy_direct_staging_transport_fenced"),
            up_case.index("ensure_runtime_env_values"),
        )
        self.assertLess(
            deploy_case.index("assert_legacy_direct_staging_transport_fenced"),
            deploy_case.index("deploy"),
        )

    def test_direct_compose_launch_is_local_only_and_fails_closed(self):
        payload = yaml.safe_load(STAGING_COMPOSE.read_text(encoding="utf-8"))
        services = payload["services"]
        app_like_services = (
            "app",
            "foreign_app",
            "sync_worker",
            "foreign_sync_worker",
            "migration",
            "bot",
            "load_telegram_foreign",
            "load_webapp_iran",
        )

        for service_name in app_like_services:
            with self.subTest(service=service_name):
                environment = services[service_name]["environment"]
                self.assertEqual(environment["SINGLE_WRITER_RUNTIME_ENABLED"], "true")
                self.assertEqual(environment["TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH"], "1")
                self.assertFalse(services[service_name].get("extra_hosts"))

        for service_name in (
            "foreign_app",
            "foreign_sync_worker",
            "bot",
            "load_telegram_foreign",
        ):
            with self.subTest(local_iran_target=service_name):
                self.assertEqual(
                    services[service_name]["environment"]["IRAN_SERVER_URL"],
                    "${STAGING_FOREIGN_IRAN_SERVER_URL:-http://app:8000}",
                )

        raw_compose = STAGING_COMPOSE.read_text(encoding="utf-8")
        for forbidden in ("staging.gold-trade.ir", "65.109.220.59", "65.109.216.187"):
            self.assertNotIn(forbidden, raw_compose)


if __name__ == "__main__":
    unittest.main()
