#!/usr/bin/env python3
"""Hermetic unit tests for two-site autopilot monitor preflight."""
from __future__ import annotations

import io
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import pytest

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_autopilot_monitor_preflight import main, run_preflight
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    publish_rescope_intent,
    rescope_epoch_basename,
    serialize_rescope_receipt,
)


def _current_continue_fixture(tmp_path):
    from scripts.two_site_codex_autopilot import serialize_accepted_checkpoint
    from tests.test_two_site_autopilot_monitor_broker_preflight import _make_result as continue_result
    root, state_root, repo, epoch = TestAutopilotMonitorPreflight(methodName="runTest")._setup_base_fixture(tmp_path)
    checkpoint = state_root / "accepted_checkpoint.json"
    checkpoint.write_bytes(serialize_accepted_checkpoint(continue_result(repo["head"]), repo))
    checkpoint.chmod(0o600)
    return root, state_root, repo, epoch, checkpoint


@pytest.mark.parametrize("fault", [None, "missing", "head", "control", "protected_ref", "scope"])
def test_continue_requires_real_current_checkpoint_without_maintenance_admission(tmp_path, fault):
    import copy
    import dataclasses
    from scripts import two_site_autopilot_monitor_preflight as preflight
    root, state_root, original, _epoch, checkpoint = _current_continue_fixture(tmp_path)
    repo = copy.deepcopy(original)
    if fault == "missing":
        checkpoint.unlink()
    elif fault == "head":
        repo["head"] = "b" * 40
    elif fault == "control":
        repo["control_blobs"][next(iter(repo["control_blobs"]))] = "b" * 40
    elif fault == "protected_ref":
        repo["protected_refs"][next(iter(repo["protected_refs"]))] = "b" * 40
    elif fault == "scope":
        repo["assignment_scope"] = dataclasses.replace(repo["assignment_scope"], sha256="b" * 64)
    (state_root / "status.json").write_text(json.dumps({"state": "CONTINUE", "head": repo["head"]}))
    with mock.patch.object(preflight, "validate_repository", return_value=repo), \
         mock.patch("scripts.two_site_maintenance_transition_startup.require_reviewed_maintenance_transition_locked",
                    return_value=None) as maintenance:
        assert preflight.run_preflight(root) == ((True, "READY") if fault is None else (False, "STALE_CHECKPOINT"))
        maintenance.assert_not_called()


def test_continue_final_checkpoint_drift_cannot_use_maintenance_fallback(tmp_path):
    from scripts import two_site_autopilot_monitor_preflight as preflight
    root, state_root, repo, _epoch, checkpoint = _current_continue_fixture(tmp_path)
    original_read = preflight._load_accepted_checkpoint
    reads = []

    def read_then_disappear(path):
        reads.append(path)
        if len(reads) == 2:
            checkpoint.unlink()
        return original_read(path)

    with mock.patch.object(preflight, "validate_repository", return_value=repo), \
         mock.patch.object(preflight, "_load_accepted_checkpoint", side_effect=read_then_disappear), \
         mock.patch("scripts.two_site_maintenance_transition_startup.require_reviewed_maintenance_transition_locked",
                    return_value=None) as maintenance:
        assert preflight.run_preflight(root) == (False, "STALE_CHECKPOINT")
        assert len(reads) == 2
        maintenance.assert_not_called()


def test_running_recovery_retains_existing_reviewed_maintenance_path(tmp_path):
    from scripts import two_site_autopilot_monitor_preflight as preflight
    root, state_root, repo, _epoch, checkpoint = _current_continue_fixture(tmp_path)
    checkpoint.unlink()
    (state_root / "status.json").write_text(json.dumps({"state": "RUNNING", "head": repo["head"]}))
    with mock.patch.object(preflight, "validate_repository", return_value=repo), \
         mock.patch.object(preflight, "validate_rescope_epoch_startup", return_value=None), \
         mock.patch("scripts.two_site_maintenance_transition_startup.require_reviewed_maintenance_transition_locked",
                    return_value=None) as maintenance:
        assert preflight.run_preflight(root) == (True, "READY")
        assert maintenance.call_count == 2


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="asg-ae03-test",
        stage_id="P1-00b",
        sha256="a" * 64,
        expires_at="2026-12-31T23:59:59Z",
        allowed_paths=[
            "scripts/two_site_autopilot_monitor_preflight.py",
            "tests/test_two_site_autopilot_monitor_preflight.py",
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
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "Rescope cycle required due to bounded proposal constraint.",
        "commit": None,
        "next_action": "Execute rescope receipt byte codec.",
        "blockers": ["Proposal exceeded single-unit boundaries."],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": repo_head,
        "head_after": repo_head,
        "head_changed": False,
    }


class TestAutopilotMonitorPreflight(unittest.TestCase):
    def test_valid_nonterminal_rescope_status_can_restart_without_checkpoint(self) -> None:
        from scripts import two_site_autopilot_monitor_preflight as preflight
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, _epoch = self._setup_base_fixture(Path(td))
            result = _make_result(repo["head"])
            (state_root / "status.json").write_text(json.dumps({
                "state": "RESCOPE",
                "head": repo["head"],
                "summary": result["summary"],
                "next_action": result["next_action"],
                "blockers": result["blockers"],
            }))
            with mock.patch.object(preflight, "validate_repository", return_value=repo), \
                 mock.patch.object(preflight, "_load_accepted_checkpoint", return_value=None), \
                 mock.patch(
                     "scripts.two_site_maintenance_transition_startup.require_reviewed_maintenance_transition_locked"
                 ) as maintenance:
                self.assertEqual(run_preflight(root), (True, "READY"))
                maintenance.assert_not_called()

            status = json.loads((state_root / "status.json").read_text())
            status["next_action"] = "different action"
            (state_root / "status.json").write_text(json.dumps(status))
            with mock.patch.object(preflight, "validate_repository", return_value=repo), \
                 mock.patch.object(preflight, "_load_accepted_checkpoint", return_value=None):
                self.assertEqual(run_preflight(root), (False, "RECEIPT_INVALID"))

    def test_rescope_rejects_present_mismatched_checkpoint_and_malformed_terminal(self) -> None:
        from scripts import two_site_autopilot_monitor_preflight as preflight
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, _epoch = self._setup_base_fixture(Path(td))
            valid = _make_result(repo["head"])
            (state_root / "status.json").write_text(json.dumps({
                "state": "RESCOPE",
                "head": repo["head"],
                "summary": valid["summary"],
                "next_action": valid["next_action"],
                "blockers": valid["blockers"],
            }))
            with mock.patch.object(preflight, "validate_repository", return_value=repo), \
                 mock.patch.object(preflight, "_load_accepted_checkpoint", return_value={"stale": True}), \
                 mock.patch.object(preflight, "_accepted_checkpoint_matches_repository", return_value=False):
                self.assertEqual(run_preflight(root), (False, "STALE_CHECKPOINT"))

            malformed_results = []
            for changes in (
                {"commit": "b" * 40},
                {"gemini_worker_run_id": "worker"},
                {"blockers": []},
                {"summary": " "},
                {"next_action": " "},
                {"schema_version": "2.0"},
                {"unexpected": True},
            ):
                candidate = dict(valid)
                candidate.update(changes)
                malformed_results.append(candidate)
            missing = dict(valid)
            missing.pop("category")
            malformed_results.append(missing)

            for candidate in malformed_results:
                with self.subTest(candidate=candidate), \
                     mock.patch.object(preflight, "validate_repository", return_value=repo), \
                     mock.patch.object(preflight, "_load_accepted_checkpoint", return_value=None), \
                     mock.patch.object(preflight, "load_latest_rescope_receipt", return_value={
                         "attempt": 1, "terminal": False, "result": candidate,
                     }):
                    self.assertEqual(run_preflight(root), (False, "RECEIPT_INVALID"))

    def test_quota_wait_due_opt_in_releases_lock_and_never_submits(self) -> None:
        from scripts import two_site_autopilot_monitor_preflight as preflight
        from contextlib import contextmanager
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, _epoch = self._setup_base_fixture(Path(td))
            original = "cycle-0001-20261020T120000Z-123"
            run_dir = state_root / "runs" / original
            run_dir.mkdir(parents=True, mode=0o700)
            record = {"request_id": "a" * 32}
            (state_root / "status.json").write_text(json.dumps({"state": "WAITING_QUOTA", "head": repo["head"],
                "evidence": str(run_dir), "request_id": record["request_id"]}))

            @contextmanager
            def pinned(*_args):
                fd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    yield run_dir, fd, lambda: None
                finally:
                    os.close(fd)

            def due(*_args):
                with (state_root / "autopilot.lock").open("rb") as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return {"controller_run_id": original, "due": {"state": "KNOWN_RESET_REVALIDATION_DUE"}}

            with mock.patch.object(preflight, "validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_quota_wait_recovery.recover_durable_initial_wait_locked", return_value=(original, record)), \
                 mock.patch("scripts.two_site_quota_supervisor_due_inspect.inspect_durable_quota_wait_due_locked", side_effect=due), \
                 mock.patch("scripts.two_site_provider_broker._open_existing_cycle_run_directory", side_effect=pinned), \
                 mock.patch("scripts.two_site_quota_supervisor_resume_submit.submit_known_reset_resume_once_locked", side_effect=AssertionError("preflight must not submit")) as submit:
                self.assertEqual(run_preflight(root, quota_wait_enabled=True), (True, "QUOTA_READY"))
                submit.assert_not_called()
                (run_dir / "quota-resume-claim.json").write_text("retained")
                self.assertEqual(run_preflight(root, quota_wait_enabled=True), (False, "QUOTA_CLAIM_AMBIGUOUS"))
            with (state_root / "autopilot.lock").open("rb") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_quota_not_due_stop_and_default_off_never_launch(self) -> None:
        from scripts import two_site_autopilot_monitor_preflight as preflight
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, _epoch = self._setup_base_fixture(Path(td))
            with mock.patch.object(preflight, "validate_repository", return_value=repo), \
                 mock.patch.object(preflight, "_quota_wait_preflight_locked", return_value=(False, "QUOTA_NOT_DUE")) as quota, \
                 mock.patch.object(preflight, "_load_accepted_checkpoint", return_value=None), \
                 mock.patch("scripts.two_site_maintenance_transition_startup.require_reviewed_maintenance_transition_locked", side_effect=ValueError("no match")):
                self.assertEqual(run_preflight(root), (False, "STALE_CHECKPOINT"))
                quota.assert_not_called()
                self.assertEqual(run_preflight(root, quota_wait_enabled=True), (False, "QUOTA_NOT_DUE"))
                (root / "STOP").symlink_to(root / "missing-stop-target")
                self.assertEqual(run_preflight(root, quota_wait_enabled=True), (False, "STOP_PRESENT"))
                self.assertEqual(quota.call_count, 1)

    def test_quota_cli_only_passes_true_when_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for flag, expected in (([], {}), (["--quota-wait-enabled"], {"quota_wait_enabled": True})):
                with mock.patch("scripts.two_site_autopilot_monitor_preflight.run_preflight", return_value=(True, "READY")) as preflight, \
                     mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(main(["--root", str(root), *flag]), 0)
                preflight.assert_called_once_with(root, **expected)

    def _setup_base_fixture(self, tmp_path: Path) -> tuple[Path, Path, dict[str, object], Path]:
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
        status_file = state_root / "status.json"
        status_file.write_text(json.dumps({"head": repo["head"], "state": "CONTINUE"}))
        status_file.chmod(0o600)

        epoch_name = rescope_epoch_basename(repo)
        epoch_dir = state_root / epoch_name
        epoch_dir.mkdir(mode=0o700, exist_ok=True)
        epoch_dir.chmod(0o700)

        publish_rescope_intent(epoch_dir, 1)

        receipt_bytes = serialize_rescope_receipt(_make_result(repo["head"]), repo, attempt=1)
        receipt_file = epoch_dir / "rescope_receipt.json"
        receipt_file.write_bytes(receipt_bytes)
        receipt_file.chmod(0o600)

        return root, state_root, repo, epoch_dir

    def test_positive_preflight_and_cli(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            checkpoint = {
                "schema_version": 1,
                "result": _make_result(repo["head"]),
                "repository": repo,
            }
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value=checkpoint), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertTrue(ok)
                self.assertEqual(reason, "READY")

                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    exit_code = main(["--root", str(root)])
                self.assertEqual(exit_code, 0)
                self.assertEqual(buf.getvalue(), "READY\n")

    def test_negative_stale_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value=None), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STALE_CHECKPOINT")

    def test_negative_stop_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            (state_root / "STOP").write_text("HALT\n")
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STOP_PRESENT")

    def test_negative_status_oversize_and_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            status_file = state_root / "status.json"

            # Duplicate key
            status_file.write_text('{"head": "' + repo["head"] + '", "state": "CONTINUE", "state": "CONTINUE"}')
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STATUS_INVALID")

            # Oversize
            status_file.write_text('{"head": "' + repo["head"] + '", "padding": "' + ("x" * 70000) + '"}')
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STATUS_INVALID")

    def test_negative_wrong_receipt_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            wrong_receipt = epoch_dir / "rescope_receipt_1.json"
            wrong_receipt.write_bytes(b"{}")
            wrong_receipt.chmod(0o600)
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "WRONG_RECEIPT_FILENAME")

    def test_negative_current_unresolved_intent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            publish_rescope_intent(epoch_dir, 2)
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "UNRESOLVED_INTENT")

    def test_negative_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            publish_rescope_intent(epoch_dir, 2)
            r2_bytes = serialize_rescope_receipt(_make_result(repo["head"]), repo, attempt=2)
            r2_file = epoch_dir / "rescope_receipt_2.json"
            r2_file.write_bytes(r2_bytes)
            r2_file.chmod(0o600)

            publish_rescope_intent(epoch_dir, 3)
            r3_bytes = serialize_rescope_receipt(_make_result(repo["head"]), repo, attempt=3)
            r3_file = epoch_dir / "rescope_receipt_3.json"
            r3_file.write_bytes(r3_bytes)
            r3_file.chmod(0o600)

            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "TERMINAL_RECEIPT")

    def test_negative_unsafe_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            symlink_leaf = epoch_dir / "rogue_leaf.txt"
            target = root / "target.txt"
            target.write_text("target\n")
            symlink_leaf.symlink_to(target)
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "UNSAFE_LEAF")

    def test_negative_repository_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            drifted_repo = dict(repo)
            drifted_repo["head"] = "f" * 40
            repo_calls = [repo, drifted_repo]
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", side_effect=repo_calls), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "REPOSITORY_DRIFT")

    def test_negative_supervisor_lock_busy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.exclusive_supervisor_lock", side_effect=AutopilotError("another autopilot supervisor already owns the lock")):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "SUPERVISOR_LOCKED")

    def test_negative_dangling_stop_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            dangling_stop = state_root / "STOP"
            dangling_stop.symlink_to(root / "missing_target_file")
            self.assertTrue(os.path.lexists(dangling_stop))
            self.assertFalse(dangling_stop.exists())

            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STOP_PRESENT")
                self.assertTrue(os.path.lexists(dangling_stop))

    def test_negative_dangling_epoch_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            for child in epoch_dir.iterdir():
                child.unlink()
            epoch_dir.rmdir()

            epoch_dir.symlink_to(root / "missing_epoch_target")
            self.assertTrue(os.path.lexists(epoch_dir))
            self.assertFalse(epoch_dir.exists())

            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "UNSAFE_LEAF")

    def test_negative_symlink_root_and_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            real_root = base / "real_root"
            real_root.mkdir(mode=0o700, exist_ok=True)
            symlink_root = base / "symlink_root"
            symlink_root.symlink_to(real_root)

            ok, reason = run_preflight(symlink_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_ROOT")

            ok, reason = run_preflight(Path("relative/path/root"))
            self.assertFalse(ok)
            self.assertEqual(reason, "INVALID_ROOT")

            # state_root symlink component
            valid_root = base / "valid_root"
            valid_root.mkdir(mode=0o700, exist_ok=True)
            root_with_state, state_root, repo, _ = self._setup_base_fixture(valid_root)
            epoch_dir = state_root / rescope_epoch_basename(repo)
            for child in epoch_dir.iterdir():
                child.unlink()
            epoch_dir.rmdir()
            (state_root / "status.json").unlink()
            state_root.rmdir()
            external_target = base / "external_autopilot_target"
            external_target.mkdir(mode=0o700, exist_ok=True)
            state_root.symlink_to(external_target)

            ok, reason = run_preflight(root_with_state)
            self.assertFalse(ok)
            self.assertEqual(reason, "STATE_ROOT_UNSAFE")

            # Intermediate component symlink test
            inter_root = base / "inter_root"
            inter_root.mkdir(mode=0o700, exist_ok=True)
            inter_target = base / "inter_target"
            inter_target.mkdir(mode=0o700, exist_ok=True)
            (inter_root / ".local").symlink_to(inter_target)
            ok, reason = run_preflight(inter_root)
            self.assertFalse(ok)
            self.assertEqual(reason, "STATE_ROOT_UNSAFE")

    def test_negative_epoch_seventeen_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            st_valid = (epoch_dir / "rescope_receipt.json").stat(follow_symlinks=False)

            class _MockDirEntry:
                def __init__(self, name: str, st: os.stat_result) -> None:
                    self.name = name
                    self._st = st

                def stat(self, follow_symlinks: bool = True) -> os.stat_result:
                    return self._st

            class _MockScanner:
                def __init__(self, entries: list[_MockDirEntry]) -> None:
                    self._entries = entries

                def __enter__(self) -> iter:
                    return iter(self._entries)

                def __exit__(self, *args: object) -> None:
                    pass

            seventeen = [_MockDirEntry("rescope_receipt.json", st_valid) for _ in range(17)]

            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.os.scandir", return_value=_MockScanner(seventeen)):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "UNSAFE_LEAF")

    def test_negative_status_alias_without_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root, repo, epoch_dir = self._setup_base_fixture(Path(td))
            status_file = state_root / "status.json"
            status_file.write_text(json.dumps({"head": repo["head"], "status": "CONTINUE"}))
            status_file.chmod(0o600)

            with mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_repository", return_value=repo), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint", return_value={}), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight._accepted_checkpoint_matches_repository", return_value=True), \
                 mock.patch("scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup", return_value=None):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STATUS_INVALID")


if __name__ == "__main__":
    unittest.main()
