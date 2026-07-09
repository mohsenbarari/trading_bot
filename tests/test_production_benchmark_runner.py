from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_production_benchmark as runner


class ProductionBenchmarkRunnerTests(unittest.TestCase):
    def test_quick_mode_selects_short_core_tasks_only(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="quick",
                profile=None,
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertIn("baseline_capture", selected)
        self.assertIn("iran_api_config", selected)
        self.assertIn("foreign_peer_api_config", selected)
        self.assertIn("sync_health_iran", selected)
        self.assertIn("observability_overhead", selected)
        self.assertIn("static_delivery_headers", selected)
        self.assertIn("production_healthcheck", selected)
        self.assertNotIn("frontend_e2e_chromium", selected)
        self.assertNotIn("messenger_full", selected)

    def test_targeted_profile_filters_to_requested_profile_and_core_default(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="observability",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["observability_readiness", "observability_gate"])

    def test_full_mode_skips_mutating_tasks_by_default(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="full",
                profile=None,
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertIn("postgres_runtime_tuning", selected)
        self.assertIn("messenger_query_plans", selected)
        self.assertIn("market_trade_query_plans", selected)
        self.assertIn("observability_readiness", selected)
        self.assertIn("deployment_restart_benchmark", selected)
        self.assertNotIn("observability_overhead", selected)
        self.assertNotIn("metrics_targets", selected)
        self.assertNotIn("production_healthcheck", selected)
        self.assertNotIn("trading_core_benchmark", selected)
        self.assertNotIn("frontend_e2e_chromium", selected)
        self.assertNotIn("messenger_full", selected)

    def test_targeted_deployment_profile_runs_p10_benchmark(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="deployment",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["deployment_restart_benchmark"])

    def test_targeted_release_profile_runs_p11_gate(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="release",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["final_release_gate"])

    def test_targeted_db_profile_checks_runtime_tuning_before_query_plans(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="db",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["postgres_runtime_tuning", "messenger_query_plans", "market_trade_query_plans"])

    def test_targeted_redis_profile_checks_runtime_durability(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="redis",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["redis_runtime_durability"])

    def test_targeted_static_profile_checks_nginx_delivery(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="static",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["static_delivery_headers"])

    def test_targeted_workers_profile_runs_worker_matrix_only(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="workers",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["worker_pool_matrix"])

    def test_targeted_sync_profile_runs_health_and_p6_benchmark(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="sync",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["sync_health_iran", "sync_health_foreign_peer", "cross_server_sync_benchmark"])

    def test_targeted_trading_profile_runs_p7_without_global_mutating_flag(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="trading",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["trading_core_benchmark"])

    def test_full_mode_can_include_trading_when_mutating_is_explicit(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="full",
                profile=None,
                include_long=False,
                skip_long=False,
                include_mutating=True,
            )
        ]

        self.assertIn("trading_core_benchmark", selected)

    def test_targeted_frontend_profile_runs_p8_without_full_e2e_by_default(self) -> None:
        settings = {
            "FOREIGN_SERVER_URL": "https://foreign.example",
            "IRAN_HEALTHCHECK_URL": "https://iran.example/api/config",
            "IRAN_HOST": "192.0.2.10",
            "IRAN_PROJECT_DIR": "/srv/app",
            "IRAN_SSH_PORT": "37067",
            "IRAN_SSH_USER": "root",
        }
        tasks = runner.build_tasks(settings=settings, manifest=None, stamp="test", artifact_root=Path("tmp"), target="iran")
        selected = [
            task.task_id
            for task in tasks
            if runner.task_selected(
                task,
                mode="targeted",
                profile="frontend",
                include_long=False,
                skip_long=False,
                include_mutating=False,
            )
        ]

        self.assertEqual(selected, ["frontend_ux_benchmark"])
        self.assertNotIn("frontend_e2e_chromium", selected)

    def test_dry_run_writes_results_without_executing_benchmark_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = runner.main([
                    "--mode",
                    "quick",
                    "--timestamp",
                    "dry-run",
                    "--artifact-root",
                    tmpdir,
                    "--dry-run",
                ])
            result_path = Path(tmpdir) / "dry-run" / "results.json"
            payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["results"])
        self.assertTrue(all(item["status"] == "planned" for item in payload["results"]))


if __name__ == "__main__":
    unittest.main()
