import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import build_staging_two_server_full_matrix_manifest as manifest_builder
from scripts import run_staging_two_server_full_matrix as runner


class StagingTwoServerFullMatrixTests(unittest.TestCase):
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
        args = runner.parse_args(["--expected-branch", "staging/object-storage-artifact-bridge"])

        self.assertEqual(args.expected_branch, "staging/object-storage-artifact-bridge")

    def test_expected_branch_default_remains_candidate_guard(self):
        args = runner.parse_args([])

        self.assertEqual(args.expected_branch, "candidate/sync-parity-hardening")

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
