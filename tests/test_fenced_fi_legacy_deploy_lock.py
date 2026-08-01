from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "deploy.sh"
PRODUCTION_DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"
MAKEFILE = REPO_ROOT / "Makefile"
MIGRATION_SCRIPT = REPO_ROOT / "run_migration.sh"
FOREIGN_ROOT_COMPOSE = REPO_ROOT / "docker-compose.yml"
IRAN_ROOT_COMPOSE = REPO_ROOT / "docker-compose.iran.yml"
FENCED_FI_LOCK = "/var/lib/trading-bot-three-site/writer-terms/fenced-fi-cutover-deployment.lock"
FENCED_FI_RUNTIME_RECEIPT = "/var/lib/trading-bot-three-site/writer-terms/fenced-fi-runtime-receipt.json"
LOCAL_DEVELOPMENT_PROFILE = "legacy-local-development"
LOCAL_DEVELOPMENT_ACK = "I_UNDERSTAND_THIS_IS_LOCAL_DEVELOPMENT_ONLY"


def function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index(f"\n{next_name}() {{", start)
    return source[start:end]


class FencedFiLegacyDeployLockTests(unittest.TestCase):
    def test_legacy_foreign_deploy_holds_the_exact_fenced_cutover_lock_first(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        lock_guard = function_body(
            source,
            "acquire_fenced_fi_cutover_deployment_lock",
            "append_pip_platform_args",
        )
        foreign_deploy = function_body(source, "deploy_foreign", "run_post_full_deploy_sync_recovery")

        self.assertIn(
            f'FENCED_FI_CUTOVER_DEPLOYMENT_LOCK="{FENCED_FI_LOCK}"', source
        )
        self.assertIn(
            f'FENCED_FI_RUNTIME_RECEIPT="{FENCED_FI_RUNTIME_RECEIPT}"', source
        )
        self.assertNotIn(
            'FENCED_FI_CUTOVER_DEPLOYMENT_LOCK="${', source
        )
        self.assertIn('exec {FENCED_FI_CUTOVER_LOCK_FD}>>"$FENCED_FI_CUTOVER_DEPLOYMENT_LOCK"', lock_guard)
        self.assertIn('flock -n "$FENCED_FI_CUTOVER_LOCK_FD"', lock_guard)
        self.assertIn("! -O \"$FENCED_FI_CUTOVER_DEPLOYMENT_LOCK\"", lock_guard)
        self.assertIn("8#$lock_mode & 077", lock_guard)
        self.assertIn("$lock_links\" != \"1", lock_guard)
        self.assertIn("$lock_dir_uid\" != \"0", lock_guard)
        self.assertIn("$lock_dir_gid\" != \"0", lock_guard)
        self.assertIn("$lock_uid\" != \"0", lock_guard)
        self.assertIn("$lock_gid\" != \"0", lock_guard)
        self.assertIn("fenced FI lock is missing from its trusted parent", lock_guard)
        self.assertIn('[[ -e "$FENCED_FI_RUNTIME_RECEIPT" || -L "$FENCED_FI_RUNTIME_RECEIPT" ]]', lock_guard)
        self.assertIn("fenced WebApp-FI runtime receipt marks the generic deployment path retired", lock_guard)

        self.assertLess(
            foreign_deploy.index("acquire_fenced_fi_cutover_deployment_lock"),
            foreign_deploy.index("ensure_local_host_timezone"),
        )
        self.assertLess(
            foreign_deploy.index("acquire_fenced_fi_cutover_deployment_lock"),
            foreign_deploy.index('"Foreign database migration"'),
        )
        self.assertIn("The subshell owns the shared flock descriptor", foreign_deploy)

    def test_generic_root_compose_deploy_is_permanently_retired_before_any_build_or_config_work(self) -> None:
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        early_guard = function_body(
            source,
            "assert_legacy_generic_root_compose_deploy_fenced",
            "assert_legacy_foreign_runtime_not_retired",
        )
        target_gate = source.split('case "$TARGET" in', 1)[1].split("esac", 1)[0]

        self.assertIn("legacy generic root-Compose deployment is retired", early_guard)
        self.assertIn("Fail before a build, host check, Docker", early_guard)
        self.assertIn("foreign)\n        assert_legacy_generic_root_compose_deploy_fenced", target_gate)

        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT), "foreign"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, msg=result.stderr or result.stdout)
        self.assertIn("legacy generic root-Compose deployment is retired", result.stderr)
        self.assertNotIn("IRAN_HOST is required", result.stderr)

    def test_online_wrapper_delegates_foreign_deploy_to_the_guarded_entrypoint(self) -> None:
        source = PRODUCTION_DEPLOY_SCRIPT.read_text(encoding="utf-8")
        foreign_deploy = function_body(source, "deploy_foreign", "sync_project")

        self.assertIn('(cd "$LOCAL_PROJECT_DIR" && bash ./deploy.sh foreign)', foreign_deploy)
        self.assertNotIn("run --rm --no-deps migration", foreign_deploy)
        self.assertNotIn("docker compose", foreign_deploy)

    def test_deploy_script_remains_valid_bash(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY_SCRIPT)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_generic_root_compose_shortcuts_are_permanently_retired(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        migration = MIGRATION_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(f"override FENCED_FI_RUNTIME_RECEIPT := {FENCED_FI_RUNTIME_RECEIPT}", makefile)
        self.assertIn("legacy-generic-fi-runtime-fenced:", makefile)
        self.assertIn("generic root-Compose FI runtime is retired for the three-site architecture", makefile)
        self.assertIn("legacy-root-compose-deploy-retired: legacy-generic-fi-runtime-fenced", makefile)
        for target in ("deploy", "frontend", "iran", "foreign"):
            with self.subTest(deploy_target=target):
                self.assertIn(f"{target}: legacy-root-compose-deploy-retired", makefile)
        for target in (
            "sync-health",
            "restore-default-commodities",
            "dev-admin",
            "create-superadmin",
            "change-password",
            "reset-sessions",
            "metrics",
            "restart",
            "status",
            "production-data-hygiene",
        ):
            with self.subTest(target=target):
                self.assertIn(f"{target}: legacy-generic-fi-runtime-fenced", makefile)

        self.assertIn("generic root-Compose migration is retired for the three-site architecture", migration)
        self.assertIn("exit 2", migration)
        self.assertNotIn("docker compose", migration)

        migration_result = subprocess.run(
            ["bash", str(MIGRATION_SCRIPT)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(migration_result.returncode, 2, msg=migration_result.stderr or migration_result.stdout)
        self.assertIn("generic root-Compose migration is retired", migration_result.stderr)

        make_result = subprocess.run(
            ["make", "foreign"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(make_result.returncode, 2, msg=make_result.stderr or make_result.stdout)
        self.assertIn("generic root-Compose FI runtime is retired", make_result.stderr)

    def test_root_compose_is_retired_by_default_and_local_opt_in_cannot_be_mistaken_for_target_runtime(self) -> None:
        """A raw root Compose command must not revive an unfenced writer.

        This is intentionally a static deployment test: rendering or starting
        Compose would be an operational action.  The invariant is stronger
        than a Make/deploy wrapper because it applies when an operator calls
        either root Compose file directly or explicitly selects one service.
        """

        cases = (
            (
                FOREIGN_ROOT_COMPOSE,
                ("app", "bot", "sync_worker", "migration", "db", "redis", "tileserver"),
            ),
            (
                IRAN_ROOT_COMPOSE,
                ("app", "sync_worker", "migration", "db", "redis"),
            ),
        )
        for compose_path, retired_services in cases:
            with self.subTest(compose=compose_path.name):
                payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
                services = payload["services"]

                # A bare `docker compose up` selects only unprofiled services.
                # It therefore reaches only the no-I/O guard and cannot
                # create a DB, apply migration, run a bot, or open sync.
                self.assertEqual(
                    {
                        name
                        for name, service in services.items()
                        if not service.get("profiles")
                    },
                    {"legacy_root_runtime_guard"},
                )
                guard = services["legacy_root_runtime_guard"]
                self.assertEqual(guard["pull_policy"], "never")
                self.assertEqual(guard["restart"], "no")
                self.assertNotIn("depends_on", guard)
                self.assertIn("ENVIRONMENT", guard["command"])
                self.assertIn("TRADING_BOT_LEGACY_LOCAL_DEVELOPMENT_ACK", guard["command"])
                self.assertIn(LOCAL_DEVELOPMENT_ACK, guard["command"])
                self.assertIn("retired for three-site production", guard["command"])

                for service_name in retired_services:
                    with self.subTest(service=service_name):
                        service = services[service_name]
                        self.assertEqual(service["profiles"], [LOCAL_DEVELOPMENT_PROFILE])
                        self.assertEqual(
                            service["depends_on"]["legacy_root_runtime_guard"]["condition"],
                            "service_completed_successfully",
                        )

                # Explicitly selecting a profiled process can otherwise bypass
                # Compose's default profile selection.  Every executable
                # application/schema/sync process repeats the local-only
                # acknowledgement before it can issue DML or open a worker.
                executable_services = {"app", "sync_worker", "migration"}
                if "bot" in retired_services:
                    executable_services.add("bot")
                for service_name in executable_services:
                    with self.subTest(executable_service=service_name):
                        command = services[service_name]["command"]
                        self.assertIn("ENVIRONMENT", command)
                        self.assertIn("TRADING_BOT_LEGACY_LOCAL_DEVELOPMENT_ACK", command)
                        self.assertIn(LOCAL_DEVELOPMENT_ACK, command)
                        self.assertIn("exit 2", command)

    def test_directly_selected_legacy_process_commands_reject_before_their_dml_or_worker_exec(self) -> None:
        """Model `compose up app`/`compose run --no-deps` without Docker.

        A service explicitly named on the Compose CLI can bypass profile
        *selection*, so its own command must reject a production environment.
        We execute only the small shell guard with no ACK.  If it were to pass,
        the following `exec` would try to start the real process and the test
        would fail; the expected exit 2 therefore proves it stops first.
        """

        cases = (
            (FOREIGN_ROOT_COMPOSE, ("app", "bot", "sync_worker", "migration")),
            (IRAN_ROOT_COMPOSE, ("app", "sync_worker", "migration")),
        )
        for compose_path, service_names in cases:
            payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
            for service_name in service_names:
                with self.subTest(compose=compose_path.name, service=service_name):
                    # Compose turns $$ into a literal $ for the container.
                    # This local shell invocation has no Docker, database,
                    # socket, network, or volume boundary.
                    command = payload["services"][service_name]["command"].replace("$$", "$")
                    environment = os.environ.copy()
                    environment["ENVIRONMENT"] = "production"
                    environment.pop("TRADING_BOT_LEGACY_LOCAL_DEVELOPMENT_ACK", None)
                    result = subprocess.run(
                        ["sh", "-ec", command],
                        cwd=REPO_ROOT,
                        env=environment,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2, msg=result.stderr or result.stdout)
                    self.assertIn("local-development only", result.stderr)


if __name__ == "__main__":
    unittest.main()
