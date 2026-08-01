"""Release-0 regressions for retirement of the historical two-site surface."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml

from core.legacy_two_server_full_matrix_fence import (
    LEGACY_TWO_SERVER_FULL_MATRIX_RETIREMENT_REASON,
    LegacyTwoServerFullMatrixRetiredError,
    assert_legacy_two_server_full_matrix_retired,
    blocked_legacy_two_server_full_matrix_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_shell(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(path), *args],
        cwd=REPO_ROOT,
        env={"PATH": os.environ.get("PATH", "")},
        text=True,
        capture_output=True,
        check=False,
    )


def function_node(path: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is missing from {path}")


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


class LegacyTwoSiteDeploymentRetirementTests(unittest.TestCase):
    def test_historical_shell_entrypoints_fail_before_configuration_or_peer_access(self) -> None:
        cases = (
            (REPO_ROOT / "deploy.sh", ("all",), "blocked before configuration, Docker, or peer network access"),
            (
                REPO_ROOT / "scripts" / "production_deploy_online.sh",
                ("--manifest", "/tmp/release0-does-not-exist.env", "release"),
                "blocked before manifest, Docker, or peer network access",
            ),
            (
                REPO_ROOT / "scripts" / "recover_cross_server_sync.sh",
                (),
                "blocked before configuration, Docker, or peer network access",
            ),
            (REPO_ROOT / "run_migration.sh", (), "is retired for the three-site architecture"),
        )

        for path, args, expected in cases:
            with self.subTest(path=path.name):
                result = run_shell(path, *args)
                rendered = result.stdout + result.stderr
                self.assertEqual(result.returncode, 2, rendered)
                self.assertIn(expected, rendered)
                self.assertNotIn("IRAN_HOST is required", rendered)
                self.assertNotIn("Manifest not found", rendered)

    def test_recovery_help_is_inert_and_describes_the_replacement(self) -> None:
        result = run_shell(REPO_ROOT / "scripts" / "recover_cross_server_sync.sh", "--help")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("legacy direct cross-server recovery path is retired", result.stdout)
        self.assertIn("Storage pull", result.stdout)

    def test_root_compose_surfaces_are_profiled_and_guarded(self) -> None:
        expected_services = {
            "docker-compose.yml": ("app", "bot", "sync_worker", "migration", "db", "redis", "tileserver"),
            "docker-compose.iran.yml": ("app", "sync_worker", "migration", "db", "redis"),
        }
        acknowledgement = "I_UNDERSTAND_THIS_IS_LOCAL_DEVELOPMENT_ONLY"

        for filename, services in expected_services.items():
            with self.subTest(compose=filename):
                payload = yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))
                declared = payload["services"]
                guard = declared["legacy_root_runtime_guard"]
                self.assertNotIn("profiles", guard)
                self.assertEqual(guard["pull_policy"], "never")
                self.assertIn(acknowledgement, str(guard["command"]))

                for service_name in services:
                    service = declared[service_name]
                    self.assertIn("legacy-local-development", service.get("profiles") or [])
                    self.assertIn("legacy_root_runtime_guard", service.get("depends_on") or {})
                    self.assertEqual(service.get("restart"), "no")

        foreign_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("IRAN_PUBLIC_DOMAIN", foreign_compose)
        self.assertNotIn("extra_hosts:", foreign_compose)

    def test_direct_cli_data_paths_are_fenced_before_peer_primitives(self) -> None:
        specs = (
            ("scripts/dev_admin.py", "forward_remote_session_reset", "assert_legacy_direct_fi_ir_transport_retired"),
            ("scripts/seed_shared_sync_tables.py", "send_items", "assert_legacy_direct_fi_ir_transport_retired"),
            ("scripts/sync_repair_tool.py", "_target_url", "assert_legacy_direct_fi_ir_transport_retired"),
            ("scripts/sync_repair_tool.py", "_send_items", "assert_legacy_direct_fi_ir_transport_retired"),
            (
                "scripts/trading_core_probe_worker.py",
                "push_prefix_change_logs_to_peer",
                "assert_legacy_direct_fi_ir_transport_retired",
            ),
        )
        forbidden_module_tokens = {
            "scripts/dev_admin.py": ("httpx", "peer_server_url_for"),
            "scripts/seed_shared_sync_tables.py": ("httpx", "peer_server_url_for", "default_peer_server_url"),
            "scripts/sync_repair_tool.py": ("urllib.request", "peer_server_url_for", "default_peer_server_url"),
            "scripts/trading_core_probe_worker.py": ("httpx", "default_peer_server_url", "/api/sync/receive"),
        }

        for path, function_name, fence_name in specs:
            with self.subTest(path=path, function=function_name):
                function = function_node(path, function_name)
                calls = [
                    dotted_name(call.func)
                    for call in ast.walk(function)
                    if isinstance(call, ast.Call)
                ]
                self.assertIn(fence_name, calls)

        for path, tokens in forbidden_module_tokens.items():
            source = (REPO_ROOT / path).read_text(encoding="utf-8")
            for token in tokens:
                with self.subTest(path=path, token=token):
                    self.assertNotIn(token, source)

    def test_old_full_matrix_and_rollout_entrypoints_are_cli_blocked(self) -> None:
        paths = (
            "scripts/run_staging_two_server_full_matrix.py",
            "scripts/run_production_full_matrix.py",
            "scripts/plan_production_full_matrix.py",
            "scripts/run_sync_parity_stage9_production_rollout.py",
            "scripts/run_worker_pool_matrix.py",
            "scripts/run_stage_l_pool_matrix.py",
        )
        for path in paths:
            with self.subTest(path=path):
                main = function_node(path, "main")
                called = [
                    dotted_name(call.func)
                    for call in ast.walk(main)
                    if isinstance(call, ast.Call)
                ]
                self.assertIn("blocked_legacy_two_server_full_matrix_payload", called)
                returns = [node.value.value for node in ast.walk(main) if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)]
                self.assertIn(2, returns)

    def test_iran_backup_transfer_is_fenced_before_ssh_or_scp(self) -> None:
        for function_name in ("backup_role", "pull_iran_files"):
            with self.subTest(function=function_name):
                function = function_node("scripts/run_production_backup.py", function_name)
                calls = [
                    dotted_name(call.func)
                    for call in ast.walk(function)
                    if isinstance(call, ast.Call)
                ]
                self.assertIn("assert_legacy_direct_fi_ir_transport_retired", calls)

        source = (REPO_ROOT / "scripts/run_production_backup.py").read_text(encoding="utf-8")
        self.assertNotIn('"scp"', source)
        self.assertNotIn("remote_args", source)

    def test_full_matrix_payload_is_stable_and_non_secret(self) -> None:
        with self.assertRaises(LegacyTwoServerFullMatrixRetiredError):
            assert_legacy_two_server_full_matrix_retired(
                component="test", operation="external action"
            )

        payload = blocked_legacy_two_server_full_matrix_payload(component="test")
        self.assertEqual(payload["status"], "blocked_legacy_two_server_full_matrix_retired")
        self.assertEqual(payload["error"], LEGACY_TWO_SERVER_FULL_MATRIX_RETIREMENT_REASON)
        self.assertNotIn("token", json.dumps(payload).lower())


if __name__ == "__main__":
    unittest.main()
