#!/usr/bin/env python3
"""Hermetic unit tests for two-site autopilot monitor broker preflight."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_autopilot_monitor_broker_preflight import (
    main,
    run_broker_preflight,
    run_preflight,
)
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    _accepted_checkpoint_matches_repository,
    validate_accepted_repository,
)


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="asg-ae03-test",
        stage_id="P1-00b",
        sha256="a" * 64,
        expires_at="2026-12-31T23:59:59Z",
        allowed_paths=[
            "scripts/two_site_autopilot_monitor_broker_preflight.py",
            "tests/test_two_site_autopilot_monitor_broker_preflight.py",
        ],
        forbidden_paths=[],
    )


def _make_repo() -> dict[str, object]:
    return {
        "head": "a1b2c3d4e5" * 4,
        "status": "",
        "protected_refs": {ref: "11" * 20 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "22" * 20 for p in CONTROL_PATHS},
        "assignment_scope": _make_scope(),
    }


def _make_result(repo_head: str = "a1b2c3d4e5" * 4) -> dict[str, object]:
    distinct_before = "9" * 40 if repo_head != "9" * 40 else "0" * 40
    return {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Advance cycle.",
        "commit": repo_head,
        "next_action": "Run broker.",
        "blockers": [],
        "gemini_worker_run_id": "11111111-1111-4111-8111-111111111111",
        "gemini_reviewer_run_id": "22222222-2222-4222-8222-222222222222",
        "proposal_sha256": "4" * 64,
        "head_before": distinct_before,
        "head_after": repo_head,
        "head_changed": True,
    }


class TestAutopilotMonitorBrokerPreflight(unittest.TestCase):
    def _setup_base_fixture(self, tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
        tmp_path.mkdir(parents=True, exist_ok=True)
        root = tmp_path.resolve()
        root.chmod(0o700)
        state_root = root / ".local" / "two-site-refactor" / "autopilot"
        state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        curr = state_root
        while curr != root:
            curr.chmod(0o700)
            curr = curr.parent

        repo = _make_repo()
        return root, state_root, repo

    def test_positive_preflight_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": validate_accepted_repository(repo),
            }
            self.assertIs(run_preflight, run_broker_preflight)
            self.assertTrue(_accepted_checkpoint_matches_repository(checkpoint, repo))
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertTrue(ok)
                self.assertEqual(reason, "READY")

                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    exit_code = main(["--root", str(root)])
                self.assertEqual(exit_code, 0)
                self.assertEqual(buf.getvalue(), "READY\n")

    def test_negative_alternate_protected_ref_fails_checkpoint_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": validate_accepted_repository(repo),
            }
            self.assertTrue(_accepted_checkpoint_matches_repository(checkpoint, repo))

            alt_repo = dict(repo)
            alt_protected = dict(repo["protected_refs"])
            ref_key = sorted(PROTECTED_REFS)[0] if PROTECTED_REFS else "refs/heads/main"
            alt_protected[ref_key] = "33" * 20
            alt_repo["protected_refs"] = alt_protected
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, alt_repo))

            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=alt_repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STALE_CHECKPOINT")

    def test_negative_missing_or_corrupt_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=None):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STALE_CHECKPOINT")

            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", side_effect=AutopilotError("corrupt JSON")):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STALE_CHECKPOINT")

    def test_negative_dirty_repository_and_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": validate_accepted_repository(repo),
            }
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", side_effect=AutopilotError("dirty tree")):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "REPOSITORY_INVALID")

            drifted_repo = dict(repo)
            drifted_repo["head"] = "f" * 40
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", side_effect=[repo, drifted_repo]), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "REPOSITORY_DRIFT")

    def test_negative_stop_present_and_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": validate_accepted_repository(repo),
            }
            stop_state = state_root / "STOP"
            stop_state.write_text("HALT\n")
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STOP_PRESENT")

                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    code = main(["--root", str(root)])
                self.assertEqual(code, 1)
                self.assertEqual(buf.getvalue(), "STOP_PRESENT\n")

            stop_state.unlink()
            stop_state.symlink_to(root / "missing_target_stop")
            self.assertTrue(os.path.lexists(stop_state))
            self.assertFalse(stop_state.exists())
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STOP_PRESENT")

            stop_state.unlink()
            stop_root = root / "STOP"
            stop_root.write_text("HALT\n")
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STOP_PRESENT")

    def test_negative_symlinks_and_malformed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            real_root = base / "real_root"
            real_root.mkdir(mode=0o700, exist_ok=True)
            symlink_root = base / "symlink_root"
            symlink_root.symlink_to(real_root)

            ok, reason = run_broker_preflight(symlink_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_ROOT")

            ok, reason = run_broker_preflight(Path("relative/path/root"))
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_ROOT")

            file_root = base / "file_root"
            file_root.write_text("not a directory")
            ok, reason = run_broker_preflight(file_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_ROOT")

            root_with_state, state_root, repo = self._setup_base_fixture(base / "valid_root")
            state_root.rmdir()
            ext_target = base / "external_target"
            ext_target.mkdir(mode=0o700, exist_ok=True)
            state_root.symlink_to(ext_target)
            ok, reason = run_broker_preflight(root_with_state)
            self.assertFalse(ok)
            self.assertEqual(reason, "STATE_ROOT_UNSAFE")

            inter_root = base / "inter_root"
            inter_root.mkdir(mode=0o700, exist_ok=True)
            (inter_root / ".local").symlink_to(ext_target)
            ok, reason = run_broker_preflight(inter_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "STATE_ROOT_UNSAFE")

            perm_root, perm_state, _ = self._setup_base_fixture(base / "perm_root")
            perm_state.chmod(0o755)
            ok, reason = run_broker_preflight(perm_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "STATE_ROOT_UNSAFE")

    def test_supervisor_lock_not_acquired_or_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": validate_accepted_repository(repo),
            }
            lock_file = state_root / "autopilot.lock"
            lock_file.write_text("held by supervisor\n")
            with mock.patch("scripts.two_site_autopilot_monitor_broker_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_broker_preflight._load_accepted_checkpoint", return_value=checkpoint):
                ok, reason = run_broker_preflight(root)
                self.assertTrue(ok)
                self.assertEqual(reason, "READY")


if __name__ == "__main__":
    unittest.main()
