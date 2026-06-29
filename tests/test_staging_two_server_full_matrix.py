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


if __name__ == "__main__":
    unittest.main()
