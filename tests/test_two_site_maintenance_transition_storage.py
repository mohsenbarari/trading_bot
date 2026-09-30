"""Test dormant pure reviewed maintenance-transition storage under AE03."""

from __future__ import annotations

import contextlib
import copy
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scripts.two_site_cold_start_adoption_storage as adoption_storage_mod
import scripts.two_site_cold_start_broker_quiescence as broker_mod
import scripts.two_site_cold_start_pending_scan as pending_scan_mod
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
    publish_maintenance_transition_bytes_noreplace,
)
import scripts.two_site_maintenance_transition_git as trans_git_mod
import scripts.two_site_maintenance_transition_record as record_mod
from scripts.two_site_maintenance_transition_record import (
    build_maintenance_transition_record,
    serialize_maintenance_transition_record,
)
import scripts.two_site_maintenance_transition_storage as storage_mod
from scripts.two_site_maintenance_transition_storage import (
    MaintenanceTransitionStorageError,
    read_maintenance_transition_locked,
)

_AUTOPILOT_CONTROL_PATH = "scripts/two_site_codex_autopilot.py"


class TestMaintenanceTransitionStorage(unittest.TestCase):
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
        self, *, autopilot_changed: bool = False
    ) -> tuple[dict, bytes, dict, str, dict, dict]:
        prior_chk_sha = "a" * 64
        prior_head, new_head = "1" * 40, "2" * 40
        refs = {r: "3" * 40 for r in sorted(PROTECTED_REFS)}
        ctrl = {p.as_posix(): "4" * 40 for p in sorted(CONTROL_PATHS, key=lambda p: p.as_posix())}
        new_ctrl = dict(ctrl)
        if autopilot_changed:
            new_ctrl[_AUTOPILOT_CONTROL_PATH] = "b" * 40

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
        paths = ["scripts/two_site_test.py"]
        if autopilot_changed:
            paths = sorted(["scripts/two_site_test.py", _AUTOPILOT_CONTROL_PATH])

        record = build_maintenance_transition_record(
            prior_checkpoint_sha256=prior_chk_sha,
            prior_head=prior_head,
            new_repository=norm_repo,
            maintenance_commits=[{
                "head": new_head,
                "parent": prior_head,
                "paths": paths,
                "proposal_sha256": "7" * 64,
                "reviewer_run_id": "reviewer-01",
                "test_evidence_sha256": "8" * 64,
                "worker_run_id": "worker-01",
            }],
        )
        raw_bytes = serialize_maintenance_transition_record(record)
        raw_repo = copy.deepcopy(norm_repo)
        raw_repo["assignment_scope"] = AssignmentScope(**scope_new)
        return record, raw_bytes, chk_repo, prior_chk_sha, norm_repo, raw_repo

    def _get_transitions_dir(self) -> Path:
        state_root_fd = storage_mod._open_validated_state_root(self.state_root)
        try:
            trans_fd = storage_mod.open_maintenance_transitions_dir(state_root_fd, create=True)
            self.assertIsNotNone(trans_fd)
            try:
                try:
                    return Path(os.readlink(f"/proc/self/fd/{trans_fd}"))
                except OSError:
                    pass
            finally:
                os.close(trans_fd)
        finally:
            os.close(state_root_fd)
        for child in self.state_root.iterdir():
            if child.is_dir() and child.name not in ("runs",):
                return child
        raise RuntimeError("transitions directory not found")

    def _publish_valid_record(self, leaf_name: str, record_bytes: bytes) -> None:
        state_root_fd = storage_mod._open_validated_state_root(self.state_root)
        try:
            trans_fd = storage_mod.open_maintenance_transitions_dir(state_root_fd, create=True)
            self.assertIsNotNone(trans_fd)
            try:
                publish_maintenance_transition_bytes_noreplace(trans_fd, leaf_name, record_bytes)
            finally:
                os.close(trans_fd)
        finally:
            os.close(state_root_fd)

    def _write_malformed_leaf_bytes_negative_fixture(self, leaf_name: str, corrupt_bytes: bytes) -> None:
        state_root_fd = storage_mod._open_validated_state_root(self.state_root)
        try:
            trans_fd = storage_mod.open_maintenance_transitions_dir(state_root_fd, create=True)
            self.assertIsNotNone(trans_fd)
            try:
                fd = os.open(
                    leaf_name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=trans_fd,
                )
                try:
                    os.write(fd, corrupt_bytes)
                finally:
                    os.close(fd)
            finally:
                os.close(trans_fd)
        finally:
            os.close(state_root_fd)

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
            storage_mod, "hold_idle_broker_client_lock_for_cold_adoption", side_effect=broker_fn
        )
        kwargs_repo = {"side_effect": repo_side_effect} if repo_side_effect else {"return_value": raw_repo}
        p_repo = mock.patch.object(storage_mod, "validate_repository", **kwargs_repo)
        if chk_side_effect:
            p_chk = mock.patch.object(storage_mod, "_load_and_audit_accepted_checkpoint", side_effect=chk_side_effect)
        else:
            p_chk = mock.patch.object(
                storage_mod, "_load_and_audit_accepted_checkpoint", return_value=({"repository": chk_repo}, chk_sha)
            )
        p_git = mock.patch.object(storage_mod, "verify_declared_maintenance_git_chain", return_value=git_chain)
        with p_broker, p_repo, p_chk, p_git:
            yield

    def test_direct_imported_dependencies_identity_and_pending_scan_contract(self) -> None:
        self.assertIs(storage_mod._load_and_audit_accepted_checkpoint, adoption_storage_mod._load_and_audit_accepted_checkpoint)
        self.assertIs(storage_mod._open_validated_state_root, adoption_storage_mod._open_validated_state_root)
        self.assertIs(storage_mod.assert_no_pending_cold_adoption_artifacts_locked, pending_scan_mod.assert_no_pending_cold_adoption_artifacts_locked)
        sig = inspect.signature(storage_mod.assert_no_pending_cold_adoption_artifacts_locked)
        self.assertEqual(len(sig.parameters), 1)
        param = list(sig.parameters.values())[0]
        self.assertIn(param.annotation, (Path, "Path"))

        self.assertIs(storage_mod.list_maintenance_transition_leaves, trans_dir_mod.list_maintenance_transition_leaves)
        self.assertIs(storage_mod.open_maintenance_transitions_dir, trans_dir_mod.open_maintenance_transitions_dir)
        self.assertIs(storage_mod.read_maintenance_transition_bytes, trans_file_mod.read_maintenance_transition_bytes)
        self.assertIs(storage_mod.verify_declared_maintenance_git_chain, trans_git_mod.verify_declared_maintenance_git_chain)
        self.assertIs(storage_mod.parse_maintenance_transition_record, record_mod.parse_maintenance_transition_record)
        self.assertIs(storage_mod._validate_persisted_repository, autopilot_mod._validate_persisted_repository)
        self.assertIs(storage_mod.rescope_epoch_basename, autopilot_mod.rescope_epoch_basename)
        self.assertIs(storage_mod.validate_accepted_repository, autopilot_mod.validate_accepted_repository)
        self.assertIs(storage_mod.validate_repository, autopilot_mod.validate_repository)
        self.assertIs(storage_mod.hold_idle_broker_client_lock_for_cold_adoption, broker_mod.hold_idle_broker_client_lock_for_cold_adoption)

        source_text = Path(storage_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ImportError", source_text)

    def test_persisted_vs_live_validator_distinction(self) -> None:
        _, _, chk_repo, _, _, _ = self._make_sample_payload()
        chk_norm = storage_mod._validate_persisted_repository(chk_repo)
        self.assertIsInstance(chk_norm, dict)
        self.assertIsInstance(chk_norm["assignment_scope"], dict)

        bad_chk = copy.deepcopy(chk_repo)
        bad_chk["assignment_scope"] = AssignmentScope(**chk_repo["assignment_scope"])
        with self.assertRaises(Exception):
            storage_mod._validate_persisted_repository(bad_chk)

        live_raw = copy.deepcopy(chk_repo)
        live_raw["assignment_scope"] = AssignmentScope(**chk_repo["assignment_scope"])
        live_norm = storage_mod.validate_accepted_repository(live_raw)
        self.assertIsInstance(live_norm, dict)
        self.assertIsInstance(live_norm["assignment_scope"], dict)

    def test_read_maintenance_transition_locked_success_and_empty_dir(self) -> None:
        record, raw_bytes, chk_repo, chk_sha, norm_repo, raw_repo = self._make_sample_payload()
        epoch_leaf = f"{rescope_epoch_basename(norm_repo)}.json"

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            self.assertIsNone(read_maintenance_transition_locked(self.root, self.state_root))

        self._get_transitions_dir()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            self.assertIsNone(read_maintenance_transition_locked(self.root, self.state_root))

        self._publish_valid_record(epoch_leaf, raw_bytes)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(storage_mod, "verify_declared_maintenance_git_chain", return_value=None) as m_git:
                loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)
        self.assertTrue(self.broker_entered and self.broker_exited)
        m_git.assert_called_once_with(self.root, record)

    def test_broker_exit_failure_and_stop_files_block(self) -> None:
        record, raw_bytes, chk_repo, chk_sha, norm_repo, raw_repo = self._make_sample_payload()
        epoch_leaf = f"{rescope_epoch_basename(norm_repo)}.json"
        self._publish_valid_record(epoch_leaf, raw_bytes)

        @contextlib.contextmanager
        def _failing_broker(root: Path):
            yield
            raise RuntimeError("broker hold exit error")

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha, broker_side_effect=_failing_broker):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        (self.root / "STOP").write_text("HALT", encoding="utf-8")
        with mock.patch.object(storage_mod, "hold_idle_broker_client_lock_for_cold_adoption", side_effect=self._fake_broker_client_lock):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)
        (self.root / "STOP").unlink()

        (self.state_root / "STOP").write_text("HALT", encoding="utf-8")
        with mock.patch.object(storage_mod, "hold_idle_broker_client_lock_for_cold_adoption", side_effect=self._fake_broker_client_lock):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

    def test_pending_artifacts_absence_and_record_blocks(self) -> None:
        record, raw_bytes, chk_repo, chk_sha, norm_repo, raw_repo = self._make_sample_payload()
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                storage_mod, "assert_no_pending_cold_adoption_artifacts_locked", side_effect=RuntimeError("pending")
            ):
                with self.assertRaises(MaintenanceTransitionStorageError):
                    read_maintenance_transition_locked(self.root, self.state_root)

        epoch_leaf = f"{rescope_epoch_basename(norm_repo)}.json"
        self._publish_valid_record(epoch_leaf, raw_bytes)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                storage_mod, "assert_no_pending_cold_adoption_artifacts_locked", side_effect=RuntimeError("pending")
            ):
                with self.assertRaises(MaintenanceTransitionStorageError):
                    read_maintenance_transition_locked(self.root, self.state_root)

    def test_checkpoint_failures_block(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo = self._make_sample_payload()
        for side_eff in [
            RuntimeError("checkpoint load error"),
            [({"repository": chk_repo}, "invalid-sha")],
            [({"repository": "not-a-dict"}, chk_sha)],
            [({"repository": {"invalid": True}}, chk_sha)],
        ]:
            kwargs = {"chk_side_effect": side_eff} if isinstance(side_eff, (list, RuntimeError)) else {}
            with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha, **kwargs):
                with self.assertRaises(MaintenanceTransitionStorageError):
                    read_maintenance_transition_locked(self.root, self.state_root)

    def test_repository_drift_failures_block(self) -> None:
        _, _, chk_repo, chk_sha, _, raw_repo = self._make_sample_payload()

        bad_head_repo = copy.deepcopy(raw_repo)
        bad_head_repo["head"] = chk_repo["head"]
        with self._mock_env(raw_repo=bad_head_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        bad_refs_chk = copy.deepcopy(chk_repo)
        bad_refs_chk["protected_refs"][list(sorted(PROTECTED_REFS))[0]] = "f" * 40
        with self._mock_env(raw_repo=raw_repo, chk_repo=bad_refs_chk, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        keys_mismatch_chk = copy.deepcopy(chk_repo)
        keys_mismatch_chk["control_blobs"] = {"unexpected/path.py": "4" * 40}
        with self._mock_env(raw_repo=raw_repo, chk_repo=keys_mismatch_chk, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        non_auto = [k for k in sorted(chk_repo["control_blobs"]) if k != _AUTOPILOT_CONTROL_PATH][0]
        hash_mismatch_chk = copy.deepcopy(chk_repo)
        hash_mismatch_chk["control_blobs"][non_auto] = "f" * 40
        with self._mock_env(raw_repo=raw_repo, chk_repo=hash_mismatch_chk, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        same_scope_repo = copy.deepcopy(raw_repo)
        same_scope_repo["assignment_scope"] = AssignmentScope(**chk_repo["assignment_scope"])
        with self._mock_env(raw_repo=same_scope_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

    def test_autopilot_control_change_and_git_chain(self) -> None:
        record, raw_bytes, chk_repo, chk_sha, norm_repo, raw_repo = self._make_sample_payload(autopilot_changed=True)
        epoch_leaf = f"{rescope_epoch_basename(norm_repo)}.json"

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        undeclared_record = copy.deepcopy(record)
        undeclared_record["maintenance_commits"][0]["paths"] = ["scripts/two_site_test.py"]
        self._publish_valid_record(epoch_leaf, serialize_maintenance_transition_record(undeclared_record))
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        trans_dir = self._get_transitions_dir()
        (trans_dir / epoch_leaf).unlink(missing_ok=True)
        self._publish_valid_record(epoch_leaf, raw_bytes)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            loaded = read_maintenance_transition_locked(self.root, self.state_root)
        self.assertEqual(loaded, record)

        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                storage_mod, "verify_declared_maintenance_git_chain", side_effect=RuntimeError("git failure")
            ):
                with self.assertRaises(MaintenanceTransitionStorageError):
                    read_maintenance_transition_locked(self.root, self.state_root)

    def test_leaf_negative_fixtures_and_postread_drift(self) -> None:
        record, raw_bytes, chk_repo, chk_sha, norm_repo, raw_repo = self._make_sample_payload()
        epoch_leaf = f"{rescope_epoch_basename(norm_repo)}.json"

        self._write_malformed_leaf_bytes_negative_fixture(epoch_leaf, b"{\"corrupt\": true}")
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        trans_dir = self._get_transitions_dir()
        (trans_dir / epoch_leaf).unlink(missing_ok=True)
        self._publish_valid_record(epoch_leaf, raw_bytes)
        foreign = trans_dir / "foreign.json"
        foreign.write_bytes(b"{}")
        foreign.chmod(0o600)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)
        foreign.unlink(missing_ok=True)

        (trans_dir / epoch_leaf).unlink(missing_ok=True)
        target = self.state_root / "target.json"
        target.write_bytes(raw_bytes)
        target.chmod(0o600)
        (trans_dir / epoch_leaf).symlink_to(target)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)
        (trans_dir / epoch_leaf).unlink(missing_ok=True)
        target.unlink(missing_ok=True)

        target_hard = self.state_root / "target_hard.json"
        target_hard.write_bytes(raw_bytes)
        target_hard.chmod(0o600)
        os.link(target_hard, trans_dir / epoch_leaf)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)
        (trans_dir / epoch_leaf).unlink(missing_ok=True)
        target_hard.unlink(missing_ok=True)

        self._publish_valid_record(epoch_leaf, raw_bytes)
        with self._mock_env(raw_repo=raw_repo, chk_repo=chk_repo, chk_sha=chk_sha):
            with mock.patch.object(
                storage_mod, "read_maintenance_transition_bytes", side_effect=[raw_bytes, raw_bytes + b" "]
            ):
                with self.assertRaises(MaintenanceTransitionStorageError):
                    read_maintenance_transition_locked(self.root, self.state_root)

        with self._mock_env(
            raw_repo=raw_repo,
            chk_side_effect=[({"repository": chk_repo}, chk_sha), ({"repository": chk_repo}, "f" * 64)],
        ):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        drift_repo = copy.deepcopy(raw_repo)
        drift_repo["head"] = "f" * 40
        with self._mock_env(repo_side_effect=[raw_repo, drift_repo], chk_repo=chk_repo, chk_sha=chk_sha):
            with self.assertRaises(MaintenanceTransitionStorageError):
                read_maintenance_transition_locked(self.root, self.state_root)

        with self.assertRaises(MaintenanceTransitionStorageError):
            read_maintenance_transition_locked(str(self.root), self.state_root)  # type: ignore[arg-type]
        with self.assertRaises(MaintenanceTransitionStorageError):
            read_maintenance_transition_locked(Path("relative"), self.state_root)
        with self.assertRaises(MaintenanceTransitionStorageError):
            read_maintenance_transition_locked(self.root, self.root / "wrong")


if __name__ == "__main__":
    unittest.main()
