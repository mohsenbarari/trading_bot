"""Tests for typed AssignmentFact origin wiring and fallback in two-site assignment scope."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Sequence, Tuple
import unittest
import yaml

from scripts.two_site_assignment_scope import (
    ASSIGNMENT_CONTRACT_INVALID,
    ASSIGNMENT_EXPIRED,
    TASK_ASSIGNMENT_TOKEN_MISSING,
    TASK_PATH_OUTSIDE_SCOPE,
    AssignmentFact,
    AssignmentScope,
    AssignmentScopeError,
    load_active_assignment_scope,
    load_expected_assignment_scope,
    validate_changed_paths,
    validate_task_scope,
)


def _make_temp_git_repo(
    assignment_id: str = "AE01-TEST-001",
    stage_id: str = "P1-00",
    expires_at: str = "2099-01-01T00:00:00Z",
    allowed_paths: Sequence[str] = ("src/*",),
    forbidden_paths: Sequence[str] = ("src/forbidden/*",),
) -> Tuple[tempfile.TemporaryDirectory, str]:
    td = tempfile.TemporaryDirectory()
    repo_dir = os.path.realpath(td.name)
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    subprocess.run(
        ["git", "init", repo_dir],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )

    exec_order_rel = os.path.join("docs", "refactor", "two-server", "execution-order.yaml")
    exec_order_path = os.path.join(repo_dir, exec_order_rel)
    os.makedirs(os.path.dirname(exec_order_path), exist_ok=True)
    with open(exec_order_path, "w", encoding="utf-8") as f:
        yaml.dump({"stages": [{"id": stage_id, "state": "IN_PROGRESS"}]}, f)
    subprocess.run(
        ["git", "add", exec_order_rel],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=env,
    )

    worktrees_dir = os.path.join(repo_dir, ".local", "two-site-refactor")
    os.makedirs(worktrees_dir, exist_ok=True)
    worktrees_path = os.path.join(worktrees_dir, "worktrees.yaml")
    with open(worktrees_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "integration": {
                    "path": repo_dir,
                    "branch": "main",
                    "active_assignment_id": assignment_id,
                }
            },
            f,
        )

    assignments_dir = os.path.join(worktrees_dir, "assignments")
    os.makedirs(assignments_dir, exist_ok=True)
    assignment_md_path = os.path.join(assignments_dir, f"{assignment_id}.md")
    assignment_yaml = {
        "id": assignment_id,
        "status": "READY",
        "role": "WORKER",
        "integration_branch": "main",
        "external_access": "NONE",
        "expires_at": expires_at,
        "stage_id": stage_id,
        "allowed_paths": list(allowed_paths),
        "forbidden_paths": list(forbidden_paths),
    }
    yaml_text = yaml.dump(assignment_yaml)
    with open(assignment_md_path, "w", encoding="utf-8") as f:
        f.write(f"```yaml\n{yaml_text}\n```\n")

    return td, repo_dir


class TestTwoSiteAssignmentScopeTypedFactsV2(unittest.TestCase):
    def _make_scope(self) -> AssignmentScope:
        return AssignmentScope(
            assignment_id="AE01-20A2-001",
            stage_id="P1-00",
            allowed_paths=("src/*",),
            forbidden_paths=("src/forbidden/*",),
            expires_at="2099-01-01T00:00:00Z",
            sha256="a" * 64,
        )

    def test_direct_legacy_factless_construction(self) -> None:
        err = AssignmentScopeError("direct legacy message")
        self.assertIsInstance(err, AssignmentScopeError)
        self.assertIsNone(err.fact)
        self.assertIsNone(err.code)
        self.assertIsNone(err.path)
        self.assertIsNone(err.fact_code)
        self.assertIsNone(err.fact_path)
        self.assertEqual(str(err), "direct legacy message")

    def test_validate_task_scope_outside_safe_path(self) -> None:
        scope = self._make_scope()
        task = {
            "allowed_files": ["outside/safe_path.py"],
            "goal": f"Perform task under token {scope.assignment_id}",
        }
        with self.assertRaises(AssignmentScopeError) as cm:
            validate_task_scope(scope, task)
        exc = cm.exception
        self.assertEqual(str(exc), "Task allowed_file is outside assignment scope")
        self.assertIsNotNone(exc.fact)
        self.assertEqual(exc.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertEqual(exc.path, "outside/safe_path.py")
        self.assertEqual(exc.fact, AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, "outside/safe_path.py"))

    def test_validate_task_scope_outside_fact_unsafe_path(self) -> None:
        scope = self._make_scope()
        unsafe_cases = ["config/secret.yaml", "keys/token.txt", "data/user@domain.txt"]
        for unsafe_path in unsafe_cases:
            task = {
                "allowed_files": [unsafe_path],
                "goal": f"Goal containing {scope.assignment_id} token",
            }
            with self.assertRaises(AssignmentScopeError) as cm:
                validate_task_scope(scope, task)
            exc = cm.exception
            self.assertEqual(str(exc), "Task allowed_file is outside assignment scope")
            self.assertIsNotNone(exc.fact)
            self.assertEqual(exc.code, TASK_PATH_OUTSIDE_SCOPE)
            self.assertIsNone(exc.path)
            self.assertEqual(exc.fact, AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, None))

    def test_validate_task_scope_missing_token_origin(self) -> None:
        scope = self._make_scope()
        task = {
            "allowed_files": ["src/valid.py"],
            "goal": "A goal text completely omitting the assignment token",
        }
        with self.assertRaises(AssignmentScopeError) as cm:
            validate_task_scope(scope, task)
        exc = cm.exception
        self.assertEqual(str(exc), "task.goal does not contain assignment ID as a bounded token")
        self.assertIsNotNone(exc.fact)
        self.assertEqual(exc.code, TASK_ASSIGNMENT_TOKEN_MISSING)
        self.assertIsNone(exc.path)
        self.assertEqual(exc.fact, AssignmentFact(TASK_ASSIGNMENT_TOKEN_MISSING, None))

    def test_assignment_expired_origin_and_propagation(self) -> None:
        td, repo_dir = _make_temp_git_repo(expires_at="2020-01-01T00:00:00Z")
        try:
            with self.assertRaises(AssignmentScopeError) as cm_active:
                load_active_assignment_scope(repo_dir)
            exc_active = cm_active.exception
            self.assertEqual(str(exc_active), "Assignment has expired")
            self.assertIsNotNone(exc_active.fact)
            self.assertEqual(exc_active.code, ASSIGNMENT_EXPIRED)
            self.assertIsNone(exc_active.path)
            self.assertEqual(exc_active.fact, AssignmentFact(ASSIGNMENT_EXPIRED, None))

            with self.assertRaises(AssignmentScopeError) as cm_expected:
                load_expected_assignment_scope(repo_dir, "AE01-TEST-001", "0" * 64)
            exc_expected = cm_expected.exception
            self.assertEqual(str(exc_expected), "Assignment has expired")
            self.assertIsNotNone(exc_expected.fact)
            self.assertEqual(exc_expected.code, ASSIGNMENT_EXPIRED)
            self.assertIsNone(exc_expected.path)
            self.assertEqual(exc_expected.fact, AssignmentFact(ASSIGNMENT_EXPIRED, None))
        finally:
            td.cleanup()

    def test_generic_fallback_on_public_boundaries(self) -> None:
        scope = self._make_scope()

        with self.assertRaises(AssignmentScopeError) as cm1:
            load_active_assignment_scope("/nonexistent/integration/dir")
        exc1 = cm1.exception
        self.assertEqual(str(exc1), "integration_root directory not found")
        self.assertIsNotNone(exc1.fact)
        self.assertEqual(exc1.code, ASSIGNMENT_CONTRACT_INVALID)
        self.assertIsNone(exc1.path)
        self.assertEqual(exc1.fact, AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None))

        td, repo_dir = _make_temp_git_repo()
        try:
            active = load_active_assignment_scope(repo_dir)
            self.assertEqual(active.assignment_id, "AE01-TEST-001")

            with self.assertRaises(AssignmentScopeError) as cm2:
                load_expected_assignment_scope(repo_dir, "MISMATCH-ID", active.sha256)
            exc2 = cm2.exception
            self.assertEqual(str(exc2), "Active assignment ID does not match expected assignment ID")
            self.assertEqual(exc2.code, ASSIGNMENT_CONTRACT_INVALID)
            self.assertIsNone(exc2.path)
            self.assertEqual(exc2.fact, AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None))

            with self.assertRaises(AssignmentScopeError) as cm3:
                load_expected_assignment_scope(repo_dir, active.assignment_id, "f" * 64)
            exc3 = cm3.exception
            self.assertEqual(str(exc3), "Active assignment sha256 digest does not match expected digest")
            self.assertEqual(exc3.code, ASSIGNMENT_CONTRACT_INVALID)
            self.assertIsNone(exc3.path)
            self.assertEqual(exc3.fact, AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None))
        finally:
            td.cleanup()

        invalid_task_cases = [
            ("not-a-task", "task must be a dict or expose 'allowed_files' and 'goal'"),
            ({"allowed_files": "not-a-list", "goal": f"Goal {scope.assignment_id}"}, "task.allowed_files must be a list or tuple"),
            ({"allowed_files": [], "goal": f"Goal {scope.assignment_id}"}, "task.allowed_files cannot be empty"),
            ({"allowed_files": ["src/a.py", "src/a.py"], "goal": f"Goal {scope.assignment_id}"}, "task.allowed_files contains duplicate paths"),
            ({"allowed_files": ["src/a.py"], "goal": ""}, "task.goal cannot be empty"),
        ]
        for bad_task, expected_msg in invalid_task_cases:
            with self.assertRaises(AssignmentScopeError) as cm4:
                validate_task_scope(scope, bad_task)
            exc4 = cm4.exception
            self.assertEqual(str(exc4), expected_msg)
            self.assertEqual(exc4.code, ASSIGNMENT_CONTRACT_INVALID)
            self.assertIsNone(exc4.path)
            self.assertEqual(exc4.fact, AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None))

        invalid_path_cases = [
            ("not-a-list", "paths must be a list or tuple"),
            ([], "paths cannot be empty"),
            (["src/a.py", "src/a.py"], "paths contains duplicate paths"),
            (["outside/other.py"], "Changed path is outside assignment scope"),
        ]
        for bad_paths, expected_msg in invalid_path_cases:
            with self.assertRaises(AssignmentScopeError) as cm5:
                validate_changed_paths(scope, bad_paths)
            exc5 = cm5.exception
            self.assertEqual(str(exc5), expected_msg)
            self.assertEqual(exc5.code, ASSIGNMENT_CONTRACT_INVALID)
            self.assertIsNone(exc5.path)
            self.assertEqual(exc5.fact, AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None))

    def test_success_values_and_signatures_preserved(self) -> None:
        td, repo_dir = _make_temp_git_repo()
        try:
            scope = load_active_assignment_scope(repo_dir)
            self.assertIsInstance(scope, AssignmentScope)
            expected_scope = load_expected_assignment_scope(repo_dir, scope.assignment_id, scope.sha256)
            self.assertEqual(scope, expected_scope)

            task = {
                "allowed_files": ["src/valid.py"],
                "goal": f"Implementing scope token {scope.assignment_id} safely",
            }
            self.assertIsNone(validate_task_scope(scope, task))
            self.assertIsNone(validate_changed_paths(scope, ["src/valid.py"]))
        finally:
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
