from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_three_site_staging_convergence_observer as convergence_observer
from scripts import run_three_site_sync_timing_observer as timing_observer


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_LIVE_COMMANDS = (
    "release",
    "check-local",
    "deploy-foreign",
    "bootstrap-iran",
    "configure-nginx",
    "issue-cert",
    "build-release",
    "sync-project",
    "ship-images",
    "load-images",
    "deploy-iran",
    "inspect-shared-data",
    "seed-shared-data",
    "healthcheck",
)

RETIRED_MAKE_TARGETS = (
    "up",
    "deploy",
    "frontend",
    "iran",
    "foreign",
    "sync-recover",
    "sync-health",
    "sync-health-iran",
    "sync-health-sample",
    "sync-health-monitor-install",
    "down",
    "logs",
    "logs-api",
    "logs-bot",
    "logs-jobs",
    "logs-follow",
    "metrics",
    "logs-iran",
    "restart",
    "restart-iran",
    "status",
    "production-release",
    "production-deployment-restart",
    "production-release-gate",
    "production-data-hygiene",
    "production-data-hygiene-iran",
    "production-backup-foreign",
    "production-backup-iran",
    "production-backup-all",
    "production-alerts",
    "production-alerts-monitor-install",
    "production-recoverability-report",
    "production-recoverability-drill",
    "production-online-help",
    "production-online-check",
    "production-online-bootstrap",
    "production-online-nginx",
    "production-online-cert",
    "production-online-build",
    "production-online-sync",
    "production-online-ship-images",
    "production-online-load-images",
    "production-online-deploy",
    "production-online-inspect-shared",
    "production-online-seed-shared",
    "production-online-health",
    "production-read-path-query-plans",
    "production-read-path-attribution",
    "production-benchmark-baseline",
    "production-benchmark-quick",
    "production-benchmark-targeted",
    "production-benchmark-full",
    "production-load-runner-bootstrap",
    "production-load-fixtures",
    "production-load-realistic",
    "production-load-sampler",
    "production-load-pool-matrix",
    "production-full-matrix-run",
    "production-full-matrix-plan",
)

RETIRED_STAGING_SHELL_DRIVERS = (
    ("scripts/run_staging_comprehensive_load_matrix.sh", "comprehensive matrix"),
    ("scripts/run_staging_dual_role_load.sh", "dual-role load"),
    ("scripts/run_staging_role_trading_e2e_gate.sh", "role/trading E2E gate"),
)


class LegacyProductionExecutionFenceTests(unittest.TestCase):
    def test_deploy_sh_stops_before_deployment_configuration_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "deploy-config-was-sourced"
            manifest = root / "untrusted.env"
            manifest.write_text(f"touch {marker}\n", encoding="utf-8")
            completed = subprocess.run(
                ["bash", "./deploy.sh", "all"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "DEPLOY_MANIFEST": str(manifest)},
            )
            output = completed.stdout + completed.stderr
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Legacy two-site deploy.sh is retired", output)
            self.assertFalse(marker.exists())

    def test_direct_sync_recovery_stops_before_host_or_configuration_access(self):
        completed = subprocess.run(
            ["bash", "./scripts/recover_cross_server_sync.sh"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "IRAN_HOST": "legacy-host.invalid",
                "IRAN_USER": "root",
                "IRAN_SSH_PORT": "22",
                "IRAN_PROJECT_DIR": "/legacy",
            },
        )

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 2)
        self.assertIn("Legacy two-site sync recovery is retired", output)

    def test_direct_staging_matrix_shell_drivers_stop_before_environment_or_docker_access(self):
        for script, label in RETIRED_STAGING_SHELL_DRIVERS:
            with self.subTest(script=script):
                completed = subprocess.run(
                    ["bash", f"./{script}"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                    env={
                        **os.environ,
                        "STAGING_APP_PORT": "99999",
                        "STAGING_PROJECT_NAME": "untrusted-project",
                        "E2E_BACKEND_BASE_URL": "http://legacy-host.invalid",
                    },
                )
                output = completed.stdout + completed.stderr
                self.assertEqual(completed.returncode, 2)
                self.assertIn("retired and hard-disabled", output, label)

    def test_retired_staging_matrix_shell_driver_help_never_initializes_runtime(self):
        for script, _label in RETIRED_STAGING_SHELL_DRIVERS:
            with self.subTest(script=script):
                completed = subprocess.run(
                    ["bash", f"./{script}", "--help"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertIn("retired", completed.stdout)

    def test_every_legacy_subcommand_stops_before_manifest_source_or_host_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "manifest-was-sourced"
            manifest = root / "untrusted.env"
            manifest.write_text(f"touch {marker}\n", encoding="utf-8")

            for command in LEGACY_LIVE_COMMANDS:
                with self.subTest(command=command):
                    completed = subprocess.run(
                        [
                            "bash",
                            "./scripts/production_deploy_online.sh",
                            "--manifest",
                            str(manifest),
                            command,
                        ],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    output = completed.stdout + completed.stderr
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("Legacy two-site production flow is retired", output)
                    self.assertFalse(marker.exists(), f"manifest was sourced for {command}")

    def test_make_aliases_are_message_only_and_never_source_a_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "make-manifest-was-sourced"
            manifest = root / "untrusted.env"
            manifest.write_text(f"touch {marker}\n", encoding="utf-8")
            env = {
                "DEPLOY_MANIFEST": str(manifest),
                "MANIFEST": str(manifest),
            }

            for target in RETIRED_MAKE_TARGETS:
                with self.subTest(target=target):
                    completed = subprocess.run(
                        ["make", "--no-print-directory", target],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                        env={**os.environ, **env},
                    )
                    output = completed.stdout + completed.stderr
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("retired and hard-disabled", output)
                    self.assertFalse(marker.exists(), f"manifest was sourced for make {target}")

    def test_make_production_release_is_a_hard_disabled_entrypoint(self):
        completed = subprocess.run(
            ["make", "--no-print-directory", "production-release"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Legacy two-site production release is retired", output)

    def test_legacy_help_remains_available_without_a_manifest(self):
        completed = subprocess.run(
            ["bash", "./scripts/production_deploy_online.sh", "--manifest", "/missing.env", "help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("This script now accepts only help.", completed.stdout)

    def test_retired_three_site_observer_clis_stop_before_config_or_output_access(self):
        campaign_id = "11111111-1111-4111-8111-111111111111"
        release_sha = "a" * 40
        plan_sha256 = "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "untrusted-config.json"
            config.write_text('{"must_not_be_read": true}\n', encoding="utf-8")
            for script, arguments, status in (
                (
                    "scripts/run_three_site_sync_timing_observer.py",
                    (
                        "--campaign-id", campaign_id,
                        "--release-sha", release_sha,
                        "--scenario-id", "three_site_sync_timing_steady_state",
                        "--config", str(config),
                        "--probe-manifest", str(root / "probe.json"),
                        "--output", str(root / "timing-output.json"),
                    ),
                    "blocked_legacy_three_site_sync_timing_observer_retired",
                ),
                (
                    "scripts/run_three_site_staging_convergence_observer.py",
                    (
                        "--campaign-id", campaign_id,
                        "--release-sha", release_sha,
                        "--plan-sha256", plan_sha256,
                        "--config", str(config),
                        "--output", str(root / "convergence-output.json"),
                    ),
                    "blocked_legacy_three_site_convergence_observer_retired",
                ),
            ):
                with self.subTest(script=script):
                    completed = subprocess.run(
                        [sys.executable, script, *arguments],
                        cwd=REPO_ROOT,
                        capture_output=True,
                        text=True,
                        check=False,
                        env={
                            **os.environ,
                            "THREE_SITE_STAGING_EXECUTE": "1",
                            "LEGACY_EXECUTION_CONFIRMATION": "allow",
                        },
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn(status, completed.stderr)
                    self.assertEqual(config.read_text(encoding="utf-8"), '{"must_not_be_read": true}\n')
                    self.assertFalse((root / "timing-output.json").exists())
                    self.assertFalse((root / "convergence-output.json").exists())

    def test_retired_three_site_observer_import_helpers_stop_before_external_calls(self):
        with patch.object(timing_observer.subprocess, "run") as run:
            for callback in (
                lambda: timing_observer._run_json(["/usr/bin/ssh"], timeout=1, label="test"),
                lambda: timing_observer._clock({}, "bot_fi"),
                lambda: timing_observer._snapshot(
                    {}, "bot_fi", correlation_prefix="matrix:timing:test:", clock={}
                ),
                lambda: timing_observer.observe(config={}, manifest={}, scenario_id="ignored"),
            ):
                with self.assertRaises(timing_observer.LegacyThreeSiteSyncTimingObserverRetiredError):
                    callback()
            run.assert_not_called()

        with (
            patch.object(convergence_observer, "_run_json") as run_json,
            patch.object(convergence_observer, "object_storage_config") as storage_config,
            patch.object(convergence_observer, "_client") as client,
            patch.object(convergence_observer, "write_secure_atomic_bytes") as write,
        ):
            for callback in (
                lambda: convergence_observer._finland_snapshot({}, "bot_fi"),
                lambda: convergence_observer._put_descriptor({}, client=object(), bucket="x", key="y"),
                lambda: convergence_observer._iran_snapshot({}, client=object(), bucket="x", key="y"),
                lambda: convergence_observer._write_snapshot(Path("/tmp"), "bot_fi", {}),
                lambda: convergence_observer.observe(config={}, output=Path("/tmp/output.json")),
            ):
                with self.assertRaises(
                    convergence_observer.LegacyThreeSiteConvergenceObserverRetiredError
                ):
                    callback()
            run_json.assert_not_called()
            storage_config.assert_not_called()
            client.assert_not_called()
            write.assert_not_called()
