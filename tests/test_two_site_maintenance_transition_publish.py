"""Test dormant pure reviewed maintenance-transition publisher under AE03."""

from __future__ import annotations

import contextlib
import copy
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scripts.two_site_cold_start_broker_quiescence as broker_mod
import scripts.two_site_codex_autopilot as autopilot_mod
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AssignmentScope,
    rescope_epoch_basename,
)
import scripts.two_site_maintenance_transition_dir as trans_dir_mod
import scripts.two_site_maintenance_transition_file as trans_file_mod
from scripts.two_site_maintenance_transition_file import (
    MaintenanceTransitionFileError,
)
import scripts.two_site_maintenance_transition_git as trans_git_mod
import scripts.two_site_maintenance_transition_publish as publish_mod
from scripts.two_site_maintenance_transition_publish import (
    MaintenanceTransitionAmbiguousError,
    MaintenanceTransitionPublishError,
    publish_maintenance_transition_locked,
)
from scripts.two_site_maintenance_transition_record import (
    ALLOWED_MAINTENANCE_CONTROL_PATHS,
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)
import scripts.two_site_maintenance_transition_storage as storage_mod
from scripts.two_site_maintenance_transition_storage import (
    read_maintenance_transition_locked,
)

_AUTOPILOT_CONTROL_PATH = "scripts/two_site_codex_autopilot.py"
_LAUNCHER_CONTROL_PATH = "scripts/two_site_autopilot_tmux.sh"


class TestMaintenanceTransitionPublish(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self._tmp.name))
        self.state_root = self.root / ".local/two-site-refactor/autopilot"
        self.state_root.mkdir(parents=True, mode=0o700)
        (self.state_root / "runs").mkdir(parents=True, mode=0o700)
        self.broker_entered = False
        self.broker_exited = False

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @contextlib.contextmanager
    def _fake_broker_client_lock(self, root: Path):
        self.assertEqual(root, self.root)
        self.broker_entered = True
        try:
            yield
        finally:
            self.broker_exited = True

    def _make_sample_payload(
        self,
        *,
        autopilot_changed: bool = False,
        launcher_changed: bool = False,
    ) -> tuple[dict, bytes, dict, str, dict, dict, list[dict]]:
        prior_chk_sha = "a" * 64
        prior_head, new_head = "1" * 40, "2" * 40
        refs = {r: "3" * 40 for r in sorted(PROTECTED_REFS)}
        ctrl = {p.as_posix(): "4" * 40 for p in sorted(CONTROL_PATHS, key=lambda p: p.as_posix())}
        new_ctrl = dict(ctrl)
        paths = ["scripts/two_site_test.py"]
        if autopilot_changed:
            new_ctrl[_AUTOPILOT_CONTROL_PATH] = "b" * 40
            paths.append(_AUTOPILOT_CONTROL_PATH)
        if launcher_changed:
            new_ctrl[_LAUNCHER_CONTROL_PATH] = "c" * 40
            paths.append(_LAUNCHER_CONTROL_PATH)
        paths = sorted(paths)

        scope_prior = {
            "assignment_id": "assign-001",
            "stage_id": "P1-00a",
            "sha256": "5" * 64,
            "expires_at": "2026-10-01T00:00:00Z",
            "allowed_paths": ["scripts/two_site_test.py"],
            "forbidden_paths": ["scripts/two_site_forbidden.py"],
        }
        scope_new = {
            "assignment_id": "assign-002",
            "stage_id": "P1-00b",
            "sha256": "6" * 64,
            "expires_at": "2026-10-02T00:00:00Z",
            "allowed_paths": ["scripts/two_site_test.py"],
            "forbidden_paths": ["scripts/two_site_forbidden.py"],
        }
        chk_repo = {
            "assignment_scope": scope_prior,
            "control_blobs": ctrl,
            "head": prior_head,
            "protected_refs": refs,
            "status": "",
        }
        norm_repo = {
            "assignment_scope": scope_new,
            "control_blobs": new_ctrl,
            "head": new_head,
            "protected_refs": refs,
            "status": "",
        }

        commits = [{
            "head": new_head,
            "parent": prior_head,
            "paths": paths,
            "proposal_sha256": "7" * 64,
            "reviewer_run_id": "reviewer-01",
            "test_evidence_sha256": "8" * 64,
            "worker_run_id": "worker-01",
        }]
        record = build_maintenance_transition_record(
            prior_checkpoint_sha256=prior_chk_sha,
            prior_head=prior_head,
            new_repository=norm_repo,
            maintenance_commits=commits,
        )
        raw_bytes = serialize_maintenance_transition_record(record)
        raw_repo = copy.deepcopy(norm_repo)
        raw_repo["assignment_scope"] = AssignmentScope(**scope_new)
        return record, raw_bytes, chk_repo, prior_chk_sha, norm_repo, raw_repo, commits

    @contextlib.contextmanager
    def _mock_env(
        self,
        *,
        raw_repo=None,
        chk_repo=None,
        chk_sha=None,
        git_chain=None,
        broker_side_effect=None,
        chk_side_effect=None,
        repo_side_effect=None,
    ):
        broker_fn = broker_side_effect or self._fake_broker_client_lock
        p_broker = mock.patch.object(
            publish_mod, "hold_idle_broker_client_lock_for_cold_adoption", side_effect=broker_fn
        )
        kwargs_repo = {"side_effect": repo_side_effect} if repo_side_effect else {"return_value": raw_repo}
        p_repo = mock.patch.object(publish_mod, "validate_repository", **kwargs_repo)
        if chk_side_effect:
            p_chk = mock.patch.object(
                publish_mod, "_load_and_audit_accepted_checkpoint", side_effect=chk_side_effect
            )
        else:
            p_chk = mock.patch.object(
                publish_mod, "_load_and_audit_accepted_checkpoint", return_value=({"repository": chk_repo}, chk_sha)
            )
        p_git = mock.patch.object(publish_mod, "verify_declared_maintenance_git_chain", return_value=git_chain)

        p_broker_s = mock.patch.object(
            storage_mod, "hold_idle_broker_client_lock_for_cold_adoption", side_effect=broker_fn
        )
        p_repo_s = mock.patch.object(storage_mod, "validate_repository", **kwargs_repo)
        p_chk_s = mock.patch.object(
            storage_mod, "_load_and_audit_accepted_checkpoint", return_value=({"repository": chk_repo}, chk_sha)
        )
        p_git_s = mock.patch.object(storage_mod, "verify_declared_maintenance_git_chain", return_value=git_chain)

        with p_broker, p_repo, p_chk, p_git, p_broker_s, p_repo_s, p_chk_s, p_git_s:
            yield

    def test_direct_imported_dependencies(self) -> None:
        self.assertTrue(callable(publish_maintenance_transition_locked))
        self.assertTrue(issubclass(MaintenanceTransitionPublishError, Exception))
        self.assertIs(MaintenanceTransitionAmbiguousError, trans_file_mod.MaintenanceTransitionAmbiguousError)
        self.assertIs(publish_mod.ALLOWED_MAINTENANCE_CONTROL_PATHS, ALLOWED_MAINTENANCE_CONTROL_PATHS)
        self.assertIs(publish_mod.hold_idle_broker_client_lock_for_cold_adoption, broker_mod.hold_idle_broker_client_lock_for_cold_adoption)
        self.assertIs(publish_mod.open_maintenance_transitions_dir, trans_dir_mod.open_maintenance_transitions_dir)
        self.assertIs(publish_mod.publish_maintenance_transition_bytes_noreplace, trans_file_mod.publish_maintenance_transition_bytes_noreplace)
        self.assertIs(publish_mod.verify_declared_maintenance_git_chain, trans_git_mod.verify_declared_maintenance_git_chain)
        sig = inspect.signature(publish_maintenance_transition_locked)
        self.assertEqual(len(sig.parameters), 3)

    def test_publish_maintenance_transition_locked_success_and_read_back(self) -> None:
        record, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            published = publish_maintenance_transition_locked(self.root, self.state_root, commits)

        self.assertEqual(published, record)
        self.assertTrue(self.broker_entered and self.broker_exited)

        # Detached copy check
        published["new_repository"]["head"] = "mutated" * 4
        self.assertNotEqual(record["new_repository"]["head"], published["new_repository"]["head"])

        # Verify disk state using canonical storage reader
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)

    def test_duplicate_publication_fails_prepublication(self) -> None:
        record, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            publish_maintenance_transition_locked(self.root, self.state_root, commits)

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_stop_file_and_pending_artifacts_block(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()
        (self.root / "STOP").write_text("HALT", encoding="utf-8")
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)
        (self.root / "STOP").unlink()

        (self.state_root / "STOP").write_text("HALT", encoding="utf-8")
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)
        (self.state_root / "STOP").unlink()

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                storage_mod, "assert_no_pending_cold_adoption_artifacts_locked", side_effect=RuntimeError("pending")
            ) as mock_pending:
                with self.assertRaises(MaintenanceTransitionPublishError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)
                mock_pending.assert_called_once_with(self.state_root)

    def test_checkpoint_and_repo_drift_fails_prepublication(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()

        bad_head_repo = copy.deepcopy(raw_repo)
        bad_head_repo["head"] = chk_repo["head"]
        with self._mock_env(raw_repo=bad_head_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

        bad_refs_repo = copy.deepcopy(raw_repo)
        first_ref = list(sorted(PROTECTED_REFS))[0]
        bad_refs_repo["protected_refs"][first_ref] = "f" * 40
        with self._mock_env(raw_repo=bad_refs_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

        keys_mismatch_repo = copy.deepcopy(raw_repo)
        keys_mismatch_repo["control_blobs"] = {"extra/path.py": "4" * 40}
        with self._mock_env(raw_repo=keys_mismatch_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

        unrelated = [k for k in sorted(chk_repo["control_blobs"]) if k not in ALLOWED_MAINTENANCE_CONTROL_PATHS][0]
        hash_mismatch_repo = copy.deepcopy(raw_repo)
        hash_mismatch_repo["control_blobs"][unrelated] = "f" * 40
        with self._mock_env(raw_repo=hash_mismatch_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

        same_scope_repo = copy.deepcopy(raw_repo)
        same_scope_repo["assignment_scope"] = AssignmentScope(**chk_repo["assignment_scope"])
        with self._mock_env(raw_repo=same_scope_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

        record_auto, _, chk_auto, sha_auto, norm_auto, raw_auto, _ = self._make_sample_payload(autopilot_changed=True)
        undeclared_commits = copy.deepcopy(commits)
        undeclared_commits[0]["paths"] = ["scripts/two_site_test.py"]
        with self._mock_env(raw_repo=raw_auto, chk_repo=chk_auto, chk_sha=sha_auto):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, undeclared_commits)

        _, _, chk_launch, sha_launch, _, raw_launch, _ = self._make_sample_payload(launcher_changed=True)
        undeclared_launch_commits = copy.deepcopy(commits)
        undeclared_launch_commits[0]["paths"] = ["scripts/two_site_test.py"]
        with self._mock_env(raw_repo=raw_launch, chk_repo=chk_launch, chk_sha=sha_launch):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, undeclared_launch_commits)

    def test_invalid_git_chain_fails_prepublication(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                publish_mod, "verify_declared_maintenance_git_chain", side_effect=RuntimeError("git failed")
            ):
                with self.assertRaises(MaintenanceTransitionPublishError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_foreign_leaf_or_existing_leaf_blocks(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()
        state_fd = publish_mod._open_validated_state_root(self.state_root)
        try:
            trans_fd = publish_mod.open_maintenance_transitions_dir(state_fd, create=True)
            self.assertIsNotNone(trans_fd)
            try:
                foreign = Path(os.readlink(f"/proc/self/fd/{trans_fd}")) / "foreign.json"
                foreign.write_bytes(b"{}")
                foreign.chmod(0o600)
            finally:
                os.close(trans_fd)
        finally:
            os.close(state_fd)

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_post_publish_drift_and_failures_raise_ambiguous(self) -> None:
        _, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload()
        drift_repo = copy.deepcopy(norm_repo)
        drift_repo["head"] = "f" * 40
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(publish_mod, "_recheck_repository", side_effect=[None, RuntimeError("repo drift")]):
                with self.assertRaises(MaintenanceTransitionAmbiguousError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

        self.tearDown()
        self.setUp()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(publish_mod, "_recheck_checkpoint", side_effect=[None, RuntimeError("chk drift")]):
                with self.assertRaises(MaintenanceTransitionAmbiguousError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

        self.tearDown()
        self.setUp()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                publish_mod, "read_maintenance_transition_bytes", return_value=b"corrupted-bytes"
            ):
                with self.assertRaises(MaintenanceTransitionAmbiguousError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_helper_failure_after_attempted_raises_ambiguous(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                publish_mod,
                "publish_maintenance_transition_bytes_noreplace",
                side_effect=MaintenanceTransitionAmbiguousError("helper failed"),
            ):
                with self.assertRaises(MaintenanceTransitionAmbiguousError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                publish_mod,
                "publish_maintenance_transition_bytes_noreplace",
                side_effect=MaintenanceTransitionFileError("file write failed"),
            ):
                with self.assertRaises(MaintenanceTransitionAmbiguousError):
                    publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_broker_exit_failure_raises_ambiguous(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()

        @contextlib.contextmanager
        def _failing_broker(root: Path):
            yield
            raise RuntimeError("broker exit failed")

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha, broker_side_effect=_failing_broker):
            with self.assertRaises(MaintenanceTransitionAmbiguousError):
                publish_maintenance_transition_locked(self.root, self.state_root, commits)

    def test_invalid_argument_paths(self) -> None:
        commits: list[dict] = []
        with self.assertRaises(MaintenanceTransitionPublishError):
            publish_maintenance_transition_locked(str(self.root), self.state_root, commits)  # type: ignore[arg-type]
        with self.assertRaises(MaintenanceTransitionPublishError):
            publish_maintenance_transition_locked(Path("relative"), self.state_root, commits)
        with self.assertRaises(MaintenanceTransitionPublishError):
            publish_maintenance_transition_locked(self.root, self.root / "wrong", commits)
        with self.assertRaises(MaintenanceTransitionPublishError):
            publish_maintenance_transition_locked(self.root, self.state_root, "not-a-list")  # type: ignore[arg-type]

    def test_autopilot_control_change_declared_success(self) -> None:
        record, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload(autopilot_changed=True)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            published = publish_maintenance_transition_locked(self.root, self.state_root, commits)
        self.assertEqual(published, record)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)

    def test_launcher_control_change_declared_success(self) -> None:
        record, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload(launcher_changed=True)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            published = publish_maintenance_transition_locked(self.root, self.state_root, commits)
        self.assertEqual(published, record)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)

    def test_both_allowed_controls_change_declared_success(self) -> None:
        record, _, chk_repo, chk_sha, norm_repo, raw_repo, commits = self._make_sample_payload(
            autopilot_changed=True, launcher_changed=True
        )
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            published = publish_maintenance_transition_locked(self.root, self.state_root, commits)
        self.assertEqual(published, record)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)

    def test_both_allowed_controls_one_undeclared_fails(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload(
            autopilot_changed=True, launcher_changed=True
        )
        partial_commits = copy.deepcopy(commits)
        partial_commits[0]["paths"] = sorted(["scripts/two_site_test.py", _AUTOPILOT_CONTROL_PATH])
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, partial_commits)

    def test_unrelated_control_change_fails_even_if_declared(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo, commits = self._make_sample_payload()
        unrelated = [k for k in sorted(chk_repo["control_blobs"]) if k not in ALLOWED_MAINTENANCE_CONTROL_PATHS][0]
        bad_ctrl_repo = copy.deepcopy(raw_repo)
        bad_ctrl_repo["control_blobs"][unrelated] = "f" * 40
        bad_commits = copy.deepcopy(commits)
        bad_commits[0]["paths"] = sorted(["scripts/two_site_test.py", unrelated])
        with self._mock_env(raw_repo=bad_ctrl_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionPublishError):
                publish_maintenance_transition_locked(self.root, self.state_root, bad_commits)


if __name__ == "__main__":
    unittest.main()
