import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import build_staging_two_server_full_matrix_manifest as manifest_builder
from scripts import run_staging_two_server_full_matrix as runner


class StagingTwoServerFullMatrixTests(unittest.TestCase):
    def test_iran_ssh_defaults_match_current_host(self):
        args = runner.parse_args([])

        self.assertEqual(args.iran_ssh_host, "root@65.109.220.59")
        self.assertEqual(args.iran_ssh_port, "37067")
        self.assertEqual(args.iran_app_container, "trading_bot_staging_iran-app-1")

    def test_expected_branch_can_be_selected_for_a_candidate_validation(self):
        args = runner.parse_args(["--expected-branch", "candidate/iran-server-replacement-20260710"])

        self.assertEqual(args.expected_branch, "candidate/iran-server-replacement-20260710")

    def test_remote_shell_and_scp_commands_include_iran_ssh_port(self):
        args = runner.parse_args(["--iran-ssh-host", "root@example", "--iran-ssh-port", "37067"])

        self.assertEqual(
            runner.remote_shell_command(args, "echo ok"),
            ["ssh", "-p", "37067", "root@example", "cd /srv/trading-bot/staging-iran && echo ok"],
        )
        self.assertEqual(
            runner.scp_from_iran(args, "/remote/file.json", Path("/tmp/local.json")),
            ["scp", "-P", "37067", "root@example:/remote/file.json", "/tmp/local.json"],
        )
        self.assertEqual(
            runner.scp_to_iran(args, Path("/tmp/local.json"), "/remote/file.json"),
            ["scp", "-P", "37067", "/tmp/local.json", "root@example:/remote/file.json"],
        )

    def test_manifest_is_complete_controlled_and_branch_change_tagged(self):
        manifest = manifest_builder.build_manifest(prefix="FMX_STAGE_UNIT_20260629_")
        errors = manifest_builder.validate_manifest(manifest)

        self.assertEqual(errors, [])
        self.assertEqual(manifest["environment"], "staging_two_server")
        self.assertFalse(manifest["mutates_production"])
        self.assertEqual(manifest["summary"]["total_manifest_scenarios"], 5611)
        self.assertEqual(manifest["summary"]["branch_change_regression_scenarios"], 56)
        self.assertTrue(manifest["summary"]["controlled_no_pressure"])

        stress_records = manifest["sections"]["production_stress_overlay"]
        self.assertLessEqual(max(item["min_parallel_requests"] for item in stress_records), 12)
        self.assertEqual({item["target_rps_floor"] for item in stress_records}, {0})

        counts = manifest["summary"]["branch_change_area_counts"]
        for area in manifest_builder.BRANCH_CHANGE_REQUIREMENTS:
            self.assertGreater(counts.get(area, 0), 0, area)

    def test_expected_branch_can_be_overridden_for_staging_continuation(self):
        args = runner.parse_args(["--expected-branch", "candidate/staging-merge-validation"])

        self.assertEqual(args.expected_branch, "candidate/staging-merge-validation")

    def test_expected_branch_default_remains_candidate_guard(self):
        args = runner.parse_args([])

        self.assertEqual(args.expected_branch, "candidate/sync-parity-hardening")

    def test_preflight_fails_when_release_label_is_not_current_commit(self):
        args = runner.parse_args(["--expected-release-sha", "wrong-release"])
        with patch.object(runner, "run_git_value") as git_value, patch.object(
            runner, "check_tls", return_value=runner.CheckResult("tls", "passed", "ok")
        ), patch.object(
            runner, "check_http_json", return_value=runner.CheckResult("http", "passed", "ok")
        ), patch.object(
            runner, "check_foreign_public_surface_guard", return_value=runner.CheckResult("guard", "passed", "ok")
        ), patch.object(
            runner, "check_internal_ingress_without_basic_auth", return_value=runner.CheckResult("ingress", "passed", "ok")
        ), patch.object(
            runner, "check_container_runtime_identity", return_value=runner.CheckResult("runtime", "passed", "ok")
        ), patch.object(
            runner, "check_storage_identity_separation", return_value=runner.CheckResult("storage", "passed", "ok")
        ), patch.object(
            runner, "capture_parity", return_value={"status": "passed"}
        ), patch.object(
            runner, "capture_sync_health", return_value={"status": "passed"}
        ):
            git_value.side_effect = lambda command: {
                ("branch", "--show-current"): args.expected_branch,
                ("rev-parse", "HEAD"): "current-commit",
                ("status", "--short", "--branch"): "",
            }.get(tuple(command), "")
            checks = runner.preflight_checks(
                args,
                manifest_builder.build_manifest(prefix="FMX_STAGE_UNIT_RELEASE_BINDING_"),
            )

        binding = next(check for check in checks if check.name == "release_commit_binding")
        self.assertEqual(binding.status, "failed")

    def test_plan_writes_equivalent_agent_log_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            original_claude_root = runner.CLAUDE_LOG_ROOT
            original_chatgpt_root = runner.CHATGPT_LOG_ROOT
            try:
                runner.CLAUDE_LOG_ROOT = tmp_path / "claude" / "full_matrix_logs"
                runner.CHATGPT_LOG_ROOT = tmp_path / "chatgpt" / "full_matrix_logs"
                args = runner.parse_args(
                    [
                        "--mode",
                        "plan",
                        "--run-id",
                        "S2FM-UNIT",
                        "--prefix",
                        "FMX_STAGE_UNIT_20260629_",
                        "--artifact-dir",
                        str(tmp_path / "artifacts"),
                    ]
                )
                payload = runner.build_plan(args)
            finally:
                runner.CLAUDE_LOG_ROOT = original_claude_root
                runner.CHATGPT_LOG_ROOT = original_chatgpt_root

            self.assertEqual(payload["summary"]["status"], "plan_ready")
            for root in (tmp_path / "claude" / "full_matrix_logs", tmp_path / "chatgpt" / "full_matrix_logs"):
                log_dir = root / "S2FM-UNIT"
                self.assertTrue((log_dir / "README.md").exists())
                self.assertTrue((log_dir / "run-metadata.json").exists())
                self.assertTrue((log_dir / "manifest.json").exists())
                self.assertTrue((log_dir / "scenario-results.jsonl").exists())
                self.assertTrue((log_dir / "summary.json").exists())
                self.assertTrue((log_dir / "redaction-report.json").exists())

    def test_json_artifacts_are_sanitized_before_publication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "artifact.json"
            runner.write_json(
                path,
                {
                    "api_key": "plain-secret-value",
                    "nested": {"mobile_number": "09370809280"},
                    "text": "BOT_TOKEN=1234567890:abcdefghijklmnopqrstuvwxyz",
                },
            )
            text = path.read_text(encoding="utf-8")

            self.assertIn("[REDACTED]", text)
            self.assertNotIn("plain-secret-value", text)
            self.assertNotIn("09370809280", text)
            self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)
            self.assertFalse(runner.detect_forbidden_secret_like_values(Path(tmpdir))["detected"])

    def test_expected_branch_can_be_overridden_for_candidate_release_gates(self):
        args = runner.parse_args(["--expected-branch", "candidate/webapp-ui-ux-unification"])

        self.assertEqual(args.expected_branch, "candidate/webapp-ui-ux-unification")

    def test_iran_ssh_port_defaults_to_current_staging_port(self):
        args = runner.parse_args([])

        self.assertEqual(args.iran_ssh_port, "37067")

    def test_remote_shell_and_scp_commands_include_iran_ssh_port(self):
        args = runner.parse_args(["--iran-ssh-host", "root@example", "--iran-ssh-port", "37067"])

        self.assertEqual(
            runner.remote_shell_command(args, "echo ok"),
            ["ssh", "-p", "37067", "root@example", "cd /srv/trading-bot/staging-iran && echo ok"],
        )
        self.assertEqual(
            runner.scp_from_iran(args, "/remote/file.json", Path("/tmp/local.json")),
            ["scp", "-P", "37067", "root@example:/remote/file.json", "/tmp/local.json"],
        )
        self.assertEqual(
            runner.scp_to_iran(args, Path("/tmp/local.json"), "/remote/file.json"),
            ["scp", "-P", "37067", "/tmp/local.json", "root@example:/remote/file.json"],
        )

    def test_cleanup_zero_gate_fails_on_remaining_rows(self):
        result = runner.CommandResult(
            name="final_cleanup_zero_check_iran",
            command=["cleanup"],
            status="passed",
            returncode=0,
            elapsed_seconds=0.1,
            stdout_path="stdout",
            stderr_path="stderr",
            json_payload={"planned_counts": {"users": 1}, "deleted_redis_keys": 0},
        )

        with self.assertRaises(RuntimeError):
            runner.assert_cleanup_dry_run_zero([result], label="final_cleanup_zero_check")

    def test_observability_clean_gate_fails_on_unsynced_backlog(self):
        result = runner.CommandResult(
            name="observability_iran_after",
            command=["observability"],
            status="passed",
            returncode=0,
            elapsed_seconds=0.1,
            stdout_path="stdout",
            stderr_path="stderr",
            json_payload={"sync": {"unsynced_change_log_count": 2}, "worker_backlog": {"unsynced_change_logs": 2}},
        )

        with self.assertRaises(RuntimeError):
            runner.assert_observability_clean([result], label="post_trade_catchup")

    def test_sync_health_capture_fails_on_nonzero_queue(self):
        def fake_fetch(url, *_args, **_kwargs):
            server_mode = "foreign" if "/foreign-sync/" in url else "iran"
            return (
                200,
                {
                    "server_mode": server_mode,
                    "status": "ok",
                    "unsynced_change_log_count": 0,
                    "redis_queues": {"sync:outbound": 1, "sync:retry": 0},
                    "parity_status": {
                        "fresh": True,
                        "comparison_status": "non_business_difference",
                        "business_drift_count": 0,
                        "critical_drift_count": 0,
                        "duplicate_identity_count": 0,
                        "incomplete_count": 0,
                        "truncated_table_count": 0,
                    },
                },
                "{}",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.fetch_observability_json",
            side_effect=fake_fetch,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                observability_api_key="key",
                iran_base_url="https://staging.gold-trade.ir",
                foreign_base_url="https://staging.362514.ir",
                basic_auth_user=None,
                basic_auth_password=None,
            )
            payload = runner.capture_sync_health(args, label="after", require_fresh_parity=True)

        self.assertEqual(payload["status"], "failed")
        self.assertIn("iran", payload["failed_peers"])
        self.assertIn("sync:outbound=1", "; ".join(payload["gate_failures"]["iran"]))

    def test_sync_health_capture_fails_on_stale_final_parity(self):
        def fake_fetch(url, *_args, **_kwargs):
            server_mode = "foreign" if "/foreign-sync/" in url else "iran"
            return (
                200,
                {
                    "server_mode": server_mode,
                    "status": "ok",
                    "unsynced_change_log_count": 0,
                    "redis_queues": {"sync:outbound": 0, "sync:retry": 0},
                    "parity_status": {
                        "fresh": False,
                        "comparison_status": "stale",
                        "latest_comparison": {
                            "business_drift_count": 0,
                            "critical_drift_count": 0,
                            "duplicate_identity_count": 0,
                            "incomplete_count": 0,
                            "truncated_table_count": 0,
                        },
                    },
                },
                "{}",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.fetch_observability_json",
            side_effect=fake_fetch,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                observability_api_key="key",
                iran_base_url="https://staging.gold-trade.ir",
                foreign_base_url="https://staging.362514.ir",
                basic_auth_user=None,
                basic_auth_password=None,
            )
            payload = runner.capture_sync_health(args, label="after", require_fresh_parity=True)

        self.assertEqual(payload["status"], "failed")
        self.assertIn("parity_status.fresh=false", "; ".join(payload["gate_failures"]["iran"]))

    def test_capture_parity_uses_foreign_sync_paths_and_validates_roles(self):
        calls = []

        def fake_fetch(url, _key, *, method="GET", **_kwargs):
            calls.append((method, url))
            if method == "POST":
                return (200, {"stored": True}, "{}")
            server_mode = "foreign" if "/foreign-sync/" in url else "iran"
            return (
                200,
                {
                    "status": "ok",
                    "server_mode": server_mode,
                    "release_sha": "abc123",
                    "mode": "quick",
                    "snapshot_at": "2026-07-08T16:03:48Z",
                    "table_count": 0,
                    "tables": {},
                },
                "{}",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.fetch_observability_json",
            side_effect=fake_fetch,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                observability_api_key="key",
                iran_base_url="https://staging.gold-trade.ir",
                foreign_base_url="https://staging.362514.ir",
                basic_auth_user=None,
                basic_auth_password=None,
                expected_release_sha="abc123",
                parity_mode="quick",
                parity_max_rows_per_table=5000,
            )
            payload = runner.capture_parity(args, label="before")

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["snapshots"]["iran"]["snapshot"]["server_mode"], "iran")
        self.assertEqual(payload["snapshots"]["foreign"]["snapshot"]["server_mode"], "foreign")
        get_urls = [url for method, url in calls if method == "GET"]
        post_urls = [url for method, url in calls if method == "POST"]
        self.assertIn("https://staging.gold-trade.ir/api/sync/parity/snapshot?mode=quick&max_rows_per_table=5000", get_urls)
        self.assertIn(
            "https://staging.362514.ir/foreign-sync/api/sync/parity/snapshot?mode=quick&max_rows_per_table=5000",
            get_urls,
        )
        self.assertIn("https://staging.gold-trade.ir/api/sync/parity/status", post_urls)
        self.assertIn("https://staging.362514.ir/foreign-sync/api/sync/parity/status", post_urls)
        self.assertNotIn(
            "https://staging.362514.ir/api/sync/parity/snapshot?mode=quick&max_rows_per_table=5000",
            get_urls,
        )

    def test_capture_parity_fails_when_foreign_snapshot_returns_iran_mode(self):
        def fake_fetch(url, _key, *, method="GET", **_kwargs):
            if method == "POST":
                return (200, {"stored": True}, "{}")
            return (
                200,
                {
                    "status": "ok",
                    "server_mode": "iran",
                    "release_sha": "abc123",
                    "mode": "quick",
                    "snapshot_at": "2026-07-08T16:03:48Z",
                    "table_count": 0,
                    "tables": {},
                },
                "{}",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.fetch_observability_json",
            side_effect=fake_fetch,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                observability_api_key="key",
                iran_base_url="https://staging.gold-trade.ir",
                foreign_base_url="https://staging.362514.ir",
                basic_auth_user=None,
                basic_auth_password=None,
                expected_release_sha="abc123",
                parity_mode="quick",
                parity_max_rows_per_table=5000,
            )
            payload = runner.capture_parity(args, label="before")

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failed_peers"], ["foreign"])
        self.assertEqual(
            payload["snapshots"]["foreign"]["server_mode_mismatch"],
            {"expected": "foreign", "observed": "iran"},
        )
        self.assertNotIn("comparison", payload)

    def test_execute_records_parity_before_final_sync_health(self):
        call_order = []

        def fake_capture_parity(_args, *, label):
            call_order.append(f"parity_{label}")
            return {"status": "passed"}

        def fake_capture_sync_health(_args, *, label, require_fresh_parity=False):
            call_order.append(f"sync_health_{label}_{require_fresh_parity}")
            return {"status": "passed"}

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {runner.EXECUTION_CONFIRM_ENV: runner.EXECUTION_CONFIRM_VALUE},
        ), patch(
            "scripts.run_staging_two_server_full_matrix.run_preflight",
            return_value=(
                {
                    "summary": {"status": "preflight_passed"},
                    "manifest": {"summary": {"total_manifest_scenarios": 5611}},
                },
                0,
            ),
        ), patch(
            "scripts.run_staging_two_server_full_matrix.run_driver_suite",
            return_value={
                "status": "passed",
                "scenario_total": 1,
                "result_counts": {"passed": 1},
                "failed_scenarios": [],
                "manifest_total": 5611,
            },
        ), patch(
            "scripts.run_staging_two_server_full_matrix.capture_parity",
            side_effect=fake_capture_parity,
        ), patch(
            "scripts.run_staging_two_server_full_matrix.capture_sync_health",
            side_effect=fake_capture_sync_health,
        ), patch(
            "scripts.run_staging_two_server_full_matrix.publish_agent_logs",
        ):
            args = SimpleNamespace(artifact_dir=Path(tmpdir), run_id="S2FM-UNIT")
            _payload, exit_code = runner.run_execute(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(call_order, ["parity_after", "sync_health_after_True"])

    def test_storage_identity_separation_fails_when_backends_match(self):
        def fake_run_json_command(command, *, timeout_seconds=10.0):
            server_mode = "iran" if "iran-app" in " ".join(command) else "foreign"
            return (
                0,
                {
                    "environment": "staging",
                    "server_mode": server_mode,
                    "release_sha": "abc123",
                    "database_identity_hash": "same-db",
                    "redis_identity_hash": "same-redis",
                    "redis_errors": 0,
                },
                "{}",
                "",
            )

        args = SimpleNamespace(
            iran_ssh_host="root@example",
            iran_app_container="iran-app",
            foreign_app_container="foreign-app",
        )
        with patch("scripts.run_staging_two_server_full_matrix.run_json_command", side_effect=fake_run_json_command):
            result = runner.check_storage_identity_separation("storage", args=args)

        self.assertEqual(result.status, "failed")
        self.assertIn("database identities match", result.detail)
        self.assertIn("Redis identities match", result.detail)

    def test_storage_identity_probe_uses_physical_postgres_cluster_identifier(self):
        script = runner.storage_identity_python()

        self.assertIn("pg_control_system()", script)
        self.assertIn("system_identifier", script)
        self.assertNotIn("inet_server_addr", script)

    def test_storage_identity_separation_passes_when_backends_differ(self):
        def fake_run_json_command(command, *, timeout_seconds=10.0):
            server_mode = "iran" if "iran-app" in " ".join(command) else "foreign"
            suffix = "iran" if server_mode == "iran" else "foreign"
            return (
                0,
                {
                    "environment": "staging",
                    "server_mode": server_mode,
                    "release_sha": "abc123",
                    "database_identity_hash": f"db-{suffix}",
                    "redis_identity_hash": f"redis-{suffix}",
                    "redis_errors": 0,
                },
                "{}",
                "",
            )

        args = SimpleNamespace(
            iran_ssh_host="root@example",
            iran_ssh_port="37067",
            iran_workdir="/srv/trading-bot/staging-iran",
            iran_app_container="iran-app",
            foreign_app_container="foreign-app",
        )
        with patch("scripts.run_staging_two_server_full_matrix.run_json_command", side_effect=fake_run_json_command):
            result = runner.check_storage_identity_separation("storage", args=args)

        self.assertEqual(result.status, "passed")

    def test_capture_parity_uses_role_specific_sync_routes(self):
        requested_urls = []

        def fake_fetch(url, *_args, method="GET", **_kwargs):
            requested_urls.append((method, url))
            if method == "POST":
                return 200, {"status": "recorded"}, "{}"
            server_mode = "foreign" if "/foreign-sync/" in url else "iran"
            return (
                200,
                {
                    "schema_version": 1,
                    "server_mode": server_mode,
                    "release_sha": "abc123",
                    "snapshot_at": "2026-07-10T00:00:00Z",
                    "mode": "deep",
                    "table_count": 0,
                    "tables": {},
                },
                "{}",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.fetch_observability_json",
            side_effect=fake_fetch,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                observability_api_key="key",
                iran_base_url="https://staging.gold-trade.ir",
                foreign_base_url="https://staging.362514.ir",
                basic_auth_user=None,
                basic_auth_password=None,
                parity_mode="deep",
                parity_max_rows_per_table=5000,
                expected_release_sha="abc123",
            )
            payload = runner.capture_parity(args, label="before")

        self.assertEqual(payload["status"], "passed")
        self.assertIn(
            ("GET", "https://staging.gold-trade.ir/api/sync/parity/snapshot?mode=deep&max_rows_per_table=5000"),
            requested_urls,
        )
        self.assertIn(
            ("GET", "https://staging.362514.ir/foreign-sync/api/sync/parity/snapshot?mode=deep&max_rows_per_table=5000"),
            requested_urls,
        )
        self.assertIn(
            ("POST", "https://staging.gold-trade.ir/api/sync/parity/status"),
            requested_urls,
        )
        self.assertIn(
            ("POST", "https://staging.362514.ir/foreign-sync/api/sync/parity/status"),
            requested_urls,
        )

    def test_driver_refreshes_both_role_plan_barriers_with_same_future_epoch(self):
        calls = []

        def fake_remote_worker(name, **kwargs):
            calls.append(("remote", name, kwargs["worker_args"]))
            return runner.CommandResult(
                name=name,
                command=["remote"],
                status="passed",
                returncode=0,
                elapsed_seconds=0.1,
                stdout_path="remote.stdout",
                stderr_path="remote.stderr",
            )

        def fake_local_worker(name, **kwargs):
            calls.append(("local", name, kwargs["worker_args"]))
            return runner.CommandResult(
                name=name,
                command=["local"],
                status="passed",
                returncode=0,
                elapsed_seconds=0.1,
                stdout_path="local.stdout",
                stderr_path="local.stderr",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.time.time",
            return_value=1000.0,
        ), patch(
            "scripts.run_staging_two_server_full_matrix.run_remote_worker",
            side_effect=fake_remote_worker,
        ), patch(
            "scripts.run_staging_two_server_full_matrix.run_local_worker",
            side_effect=fake_local_worker,
        ):
            results = runner.refresh_role_plan_barriers_on_both_sides(
                args=SimpleNamespace(),
                local_dir=Path(tmpdir),
                remote_dir="/remote/artifacts",
                log_dir=Path(tmpdir) / "logs",
            )

        self.assertEqual(len(results), 2)
        self.assertEqual([call[0] for call in calls], ["remote", "local"])
        barrier_values = []
        for _kind, _name, worker_args in calls:
            self.assertEqual(worker_args[0], "set-role-plan-barrier")
            barrier_values.append(worker_args[worker_args.index("--barrier-epoch") + 1])
        self.assertEqual(barrier_values, ["1030.000000", "1030.000000"])

    def test_foreign_runtime_identity_rejects_local_iran_route(self):
        def fake_run_json_command(_command, *, timeout_seconds=10.0):
            return (
                0,
                {
                    "environment": "staging",
                    "server_mode": "foreign",
                    "service": "staging-bot",
                    "release_sha": "abc123",
                    "frontend_url": "https://staging.gold-trade.ir",
                    "iran_server_url": "http://app:8000",
                    "germany_server_url": "http://foreign_app:8000",
                    "foreign_server_url": "http://foreign_app:8000",
                    "peer_server_url": "",
                    "bot_token_configured": True,
                    "channel_id_configured": True,
                },
                "{}",
                "",
            )

        with patch("scripts.run_staging_two_server_full_matrix.run_json_command", side_effect=fake_run_json_command):
            result = runner.check_container_runtime_identity(
                "foreign_runtime_identity",
                server="foreign",
                expected_server_mode="foreign",
                expected_release="abc123",
                args=SimpleNamespace(foreign_app_container="foreign-app"),
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("IRAN_SERVER_URL points to local compose", result.detail)

    def test_local_load_runner_command_overrides_iran_route(self):
        args = SimpleNamespace(iran_base_url="https://staging.gold-trade.ir")
        command = runner.local_load_runner_command(
            "load_telegram_foreign",
            Path("/tmp/full-matrix-artifacts"),
            ["load-runner-ready", "--role", "telegram_foreign"],
            args=args,
        )

        self.assertIn("IRAN_SERVER_URL=https://staging.gold-trade.ir", command)
        self.assertIn("GERMANY_SERVER_URL=http://foreign_app:8000", command)
        self.assertIn("FOREIGN_SERVER_URL=http://foreign_app:8000", command)

    def test_remote_load_runner_supports_compose_v2_with_legacy_fallback(self):
        args = runner.parse_args(
            [
                "--iran-ssh-host",
                "root@example",
                "--iran-ssh-port",
                "37067",
                "--expected-release-sha",
                "abc123",
            ]
        )

        command = runner.remote_load_runner_command(
            args,
            "load_webapp_iran",
            "/tmp/full-matrix-artifacts",
            ["load-runner-ready", "--role", "webapp_iran"],
        )
        remote_command = command[-1]

        self.assertIn("docker compose version", remote_command)
        self.assertIn("command -v docker-compose", remote_command)
        self.assertIn('"${compose[@]}"', remote_command)
        self.assertNotIn(" STAGING_FRONTEND_DOCKER_DIST_DIR=mini_app_dist_staging docker-compose ", remote_command)

    def test_worker_split_reindexes_each_role_schedule_from_zero(self):
        from scripts import trading_core_probe_worker as worker

        users = [
            worker.LoadUserRef(user_id=1, telegram_id=9001),
            worker.LoadUserRef(user_id=2, telegram_id=9002),
            worker.LoadUserRef(user_id=3, telegram_id=9003),
            worker.LoadUserRef(user_id=4, telegram_id=9004),
        ]
        plan = worker.build_mixed_surface_plan(
            users=users,
            owner_user_id=1,
            total_requests=12,
            telegram_ratio=0.5,
        )
        split = worker.split_mixed_plan_by_role(plan)

        self.assertEqual(
            [spec.index for spec in split["telegram_foreign"]],
            list(range(len(split["telegram_foreign"]))),
        )
        self.assertEqual(
            [spec.index for spec in split["webapp_iran"]],
            list(range(len(split["webapp_iran"]))),
        )
        self.assertGreater(len(split["telegram_foreign"]), 0)
        self.assertGreater(len(split["webapp_iran"]), 0)

    def test_sync_prefix_catchup_targets_all_business_tables(self):
        from scripts import trading_core_probe_worker as worker

        for table in (
            "users",
            "telegram_link_tokens",
            "offers",
            "offer_publication_states",
            "offer_requests",
            "trades",
            "trade_delivery_receipts",
            "telegram_admin_broadcasts",
            "telegram_admin_broadcast_receipts",
            "notifications",
        ):
            self.assertIn(table, worker.TARGETED_SYNC_TABLES)

    def test_sync_prefix_catchup_sends_trades_before_offer_requests(self):
        from scripts import trading_core_probe_worker as worker

        self.assertLess(
            worker.TARGETED_SYNC_TABLES.index("trades"),
            worker.TARGETED_SYNC_TABLES.index("offer_requests"),
        )
        self.assertLess(
            worker.TARGETED_SYNC_TABLES.index("trades"),
            worker.TARGETED_SYNC_TABLES.index("trade_delivery_receipts"),
        )

    def test_driver_sync_catchup_replays_synced_prefix_rows_for_convergence(self):
        calls = []

        def fake_remote_worker(name, **kwargs):
            calls.append(("remote", name, kwargs["worker_args"]))
            return runner.CommandResult(
                name=name,
                command=["remote"],
                status="passed",
                returncode=0,
                elapsed_seconds=0.1,
                stdout_path="remote.stdout",
                stderr_path="remote.stderr",
            )

        def fake_local_worker(name, **kwargs):
            calls.append(("local", name, kwargs["worker_args"]))
            return runner.CommandResult(
                name=name,
                command=["local"],
                status="passed",
                returncode=0,
                elapsed_seconds=0.1,
                stdout_path="local.stdout",
                stderr_path="local.stderr",
            )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.run_remote_worker",
            side_effect=fake_remote_worker,
        ), patch(
            "scripts.run_staging_two_server_full_matrix.run_local_worker",
            side_effect=fake_local_worker,
        ):
            runner.run_sync_catchup(
                args=SimpleNamespace(),
                prefix="FMX_STAGE_UNIT_",
                local_dir=Path(tmpdir),
                remote_dir="/remote/artifacts",
                log_dir=Path(tmpdir) / "logs",
            )

        self.assertEqual(len(calls), 2)
        for _kind, _name, worker_args in calls:
            self.assertIn("--include-synced", worker_args)

    def test_driver_seeds_users_on_iran_and_reuses_them_during_prepare(self):
        scenario = {
            "id": "DRIVER-BOT-UNIT",
            "offer_origin": "bot",
            "request_surface": "mixed",
            "idempotency_mode": "unique",
            "hot_offer_requests": 6,
            "telegram_ratio": 0.5,
            "target_rps": 4,
            "hot_offer_quantity": 5,
            "request_amount": 5,
            "expected_winner_count": 1,
            "expected_remaining_quantity": 0,
            "offer_type": "sell",
            "retail": False,
        }

        seed_args = runner.seed_users_worker_args(scenario, "FMX_STAGE_UNIT_")
        prepare_args = runner.prepare_worker_args(scenario, "FMX_STAGE_UNIT_")

        self.assertEqual(seed_args[0], "seed-dual-role-users")
        self.assertIn("--skip-initial-cleanup", seed_args)
        self.assertIn("--users-artifact", prepare_args)
        self.assertIn("/artifacts/users.seed.json", prepare_args)
        self.assertIn("--skip-initial-cleanup", prepare_args)
        self.assertEqual(runner.scenario_user_count(scenario), 8)

    def test_race_scenarios_use_specialized_worker_and_finalize_artifacts(self):
        manual = next(item for item in runner.DRIVER_SCENARIOS if item.get("race_kind") == "manual_expiry")
        timed = next(item for item in runner.DRIVER_SCENARIOS if item.get("race_kind") == "time_expiry")

        manual_prepare = runner.prepare_worker_args(manual, "FMX_STAGE_UNIT_")
        timed_prepare = runner.prepare_worker_args(timed, "FMX_STAGE_UNIT_")

        self.assertIn("manual_expire_trade_race", manual_prepare)
        self.assertIn("time_expire_trade_race", timed_prepare)
        self.assertIn("--allow-nonterminal-offer", manual_prepare)
        self.assertIn("--offer-time-limit-buffer-minutes", timed_prepare)
        self.assertEqual(
            runner.finalize_race_worker_args(manual),
            ["--manual-expiry-result", "/artifacts/manual-expiry.result.json"],
        )
        self.assertEqual(
            runner.finalize_race_worker_args(timed),
            ["--time-expiry-result", "/artifacts/time-expiry.result.json"],
        )

    def test_race_barrier_has_container_startup_headroom(self):
        self.assertGreaterEqual(runner.RACE_START_BARRIER_DELAY_SECONDS, 3 * 20)
        self.assertGreater(
            runner.RACE_START_BARRIER_DELAY_SECONDS,
            runner.ROLE_START_BARRIER_DELAY_SECONDS,
        )

    def test_seed_and_converge_runs_iran_seed_sync_copy_and_foreign_verify(self):
        def result(name):
            return runner.CommandResult(
                name=name,
                command=[name],
                status="passed",
                returncode=0,
                elapsed_seconds=0.1,
                stdout_path=f"{name}.stdout",
                stderr_path=f"{name}.stderr",
            )

        scenario = {
            "offer_origin": "webapp",
            "hot_offer_requests": 2,
            "telegram_ratio": 0.5,
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            runner,
            "run_remote_worker",
            side_effect=[result("seed_users_on_iran"), result("sync_seeded_users_iran_to_foreign")],
        ) as remote, patch.object(
            runner,
            "run_logged_command",
            return_value=result("copy_seeded_users_iran_to_foreign"),
        ) as copy, patch.object(
            runner,
            "run_local_worker",
            return_value=result("verify_seeded_users_on_foreign"),
        ) as local, patch.object(runner, "scp_from_iran", return_value=["scp"]):
            results = runner.seed_and_converge_dual_role_users(
                args=SimpleNamespace(),
                scenario=scenario,
                prefix="FMX_STAGE_UNIT_",
                local_dir=Path(directory),
                remote_dir="/remote/unit",
                log_dir=Path(directory) / "logs",
            )

        self.assertEqual([item.name for item in results], [
            "seed_users_on_iran",
            "sync_seeded_users_iran_to_foreign",
            "copy_seeded_users_iran_to_foreign",
            "verify_seeded_users_on_foreign",
        ])
        self.assertIn("sync-prefix-catchup", remote.call_args_list[1].kwargs["worker_args"])
        self.assertIn("verify-dual-role-users", local.call_args.kwargs["worker_args"])
        copy.assert_called_once()

    def test_driver_invokes_seed_convergence_before_prepare_and_records_failure(self):
        scenario = {
            "id": "DRIVER-STAGE9-SEED-UNIT",
            "offer_origin": "webapp",
            "request_surface": "mixed",
            "idempotency_mode": "unique",
            "coverage": ["seed_convergence"],
            "hot_offer_requests": 2,
            "telegram_ratio": 0.5,
            "target_rps": 1,
            "hot_offer_quantity": 5,
            "request_amount": 5,
            "expected_winner_count": 1,
            "expected_remaining_quantity": 0,
            "offer_type": "sell",
            "retail": False,
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            runner,
            "run_cleanup_on_both_sides",
            return_value=[],
        ), patch.object(
            runner,
            "assert_cleanup_dry_run_zero",
            return_value={"status": "ok"},
        ), patch.object(runner, "run_observability_snapshots", return_value=[]), patch.object(
            runner,
            "seed_and_converge_dual_role_users",
            return_value=[],
        ) as seed, patch.object(
            runner,
            "run_remote_worker",
            side_effect=RuntimeError("stop after seed"),
        ), patch.object(runner, "read_json_file", return_value={}), patch.object(
            runner,
            "write_json",
        ):
            args = SimpleNamespace(prefix="FMX_STAGE_UNIT_", run_id="S2FM-UNIT")
            result = runner.execute_driver_scenario(
                args,
                scenario,
                suite_dir=Path(directory),
                remote_root="/remote/unit",
            )

        seed.assert_called_once()
        self.assertEqual(result["status"], "failed")
        self.assertIn("stop after seed", result["error"])

    def test_driver_suite_can_filter_to_one_driver_scenario(self):
        calls = []

        def fake_execute_driver_scenario(args, scenario, *, suite_dir, remote_root):
            calls.append(scenario["id"])
            return {
                "scenario_id": scenario["id"],
                "status": "passed",
                "error": "",
                "elapsed_seconds": 0.1,
            }

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "scripts.run_staging_two_server_full_matrix.execute_driver_scenario",
            side_effect=fake_execute_driver_scenario,
        ):
            args = SimpleNamespace(
                artifact_dir=Path(tmpdir),
                iran_workdir="/remote",
                run_id="S2FM-UNIT",
                driver_scenario_id=["DRIVER-BOT-DUPLICATE-REPLAY-BUY"],
                driver_scenario_limit=4,
            )
            result = runner.run_driver_suite(args, {"summary": {"total_manifest_scenarios": 5611}})

        self.assertEqual(calls, ["DRIVER-BOT-DUPLICATE-REPLAY-BUY"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["scenario_total"], 1)


if __name__ == "__main__":
    unittest.main()
