#!/usr/bin/env python3
"""Hermetic unit tests for quarantine recovery startup fallback verification."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    validate_accepted_repository,
)
from scripts.two_site_maintenance_transition_startup import (
    MaintenanceTransitionStartupError,
    require_reviewed_maintenance_transition_locked,
)
from scripts.two_site_maintenance_transition_storage import (
    MaintenanceTransitionStorageError,
)


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="asg-ae03-recovery-test",
        stage_id="P1-00b",
        sha256="b" * 64,
        expires_at="2026-12-31T23:59:59Z",
        allowed_paths=[
            "scripts/two_site_maintenance_transition_startup.py",
            "tests/test_two_site_quarantine_recovery_startup.py",
        ],
        forbidden_paths=[],
    )


def _make_repo() -> dict[str, object]:
    return {
        "head": "c1d2e3f4a5" * 4,
        "status": "",
        "protected_refs": {ref: "33" * 20 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "44" * 20 for p in CONTROL_PATHS},
        "assignment_scope": _make_scope(),
    }


class TestQuarantineRecoveryStartupFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = _make_repo()
        self.norm_repo = validate_accepted_repository(self.repo)

    def _setup_workspace(self, td: str) -> tuple[Path, Path]:
        root = Path(td).resolve()
        state_root = root / ".local" / "two-site-refactor" / "autopilot"
        state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        return root, state_root

    def test_ordinary_valid_maintenance_record_never_invokes_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            record = {"new_repository": copy.deepcopy(self.norm_repo)}
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked"
            ) as mock_recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                result = require_reviewed_maintenance_transition_locked(
                    root, state_root, self.repo
                )
                self.assertIsNone(result)
                mock_recovery.assert_not_called()

    def test_ordinary_read_exception_with_valid_recovery_envelope_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            recovery_env = {
                "record": {
                    "new_repository": copy.deepcopy(self.norm_repo),
                    "prior_checkpoint_sha256": "5" * 64,
                }
            }
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                side_effect=MaintenanceTransitionStorageError("storage corrupt"),
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ) as mock_recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                result = require_reviewed_maintenance_transition_locked(
                    root, state_root, self.repo
                )
                self.assertIsNone(result)
                mock_recovery.assert_called_once_with(root, state_root)

    def test_ordinary_read_none_with_valid_recovery_envelope_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            recovery_env = {
                "record": {
                    "new_repository": copy.deepcopy(self.norm_repo),
                }
            }
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ) as mock_recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                result = require_reviewed_maintenance_transition_locked(
                    root, state_root, self.repo
                )
                self.assertIsNone(result)
                mock_recovery.assert_called_once_with(root, state_root)

    def test_ordinary_read_none_and_recovery_none_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=None,
            ) as mock_recovery:
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                mock_recovery.assert_called_once_with(root, state_root)

    def test_tampered_or_invalid_recovery_payloads_fail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)

            # Recovery reader raises Exception
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                side_effect=Exception("signature verification failed"),
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

            # Recovery reader returns non-dict
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value="not an envelope dict",
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

            # Missing or non-dict record
            for bad_record in (None, "not a dict", []):
                with mock.patch(
                    "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                    return_value=None,
                ), mock.patch(
                    "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                    return_value={"record": bad_record},
                ):
                    with self.assertRaises(MaintenanceTransitionStartupError):
                        require_reviewed_maintenance_transition_locked(
                            root, state_root, self.repo
                        )

            # Missing or non-dict record.new_repository
            for bad_repo in (None, "not a dict", []):
                with mock.patch(
                    "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                    return_value=None,
                ), mock.patch(
                    "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                    return_value={"record": {"new_repository": bad_repo}},
                ):
                    with self.assertRaises(MaintenanceTransitionStartupError):
                        require_reviewed_maintenance_transition_locked(
                            root, state_root, self.repo
                        )

    def test_recovery_supplied_repo_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            mismatched_repo = copy.deepcopy(self.norm_repo)
            mismatched_repo["head"] = "9" * 40
            recovery_env = {"record": {"new_repository": mismatched_repo}}
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_recovery_live_repo_drift_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            recovery_env = {"record": {"new_repository": copy.deepcopy(self.norm_repo)}}
            drifted_repo = copy.deepcopy(self.repo)
            drifted_repo["head"] = "8" * 40
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=drifted_repo,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_recovery_stop_file_present_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            recovery_env = {"record": {"new_repository": copy.deepcopy(self.norm_repo)}}

            # root / STOP
            stop_root = root / "STOP"
            stop_root.write_text("STOP\n", encoding="utf-8")
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
            stop_root.unlink()

            # state_root / STOP
            stop_state = state_root / "STOP"
            stop_state.write_text("STOP\n", encoding="utf-8")
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value=recovery_env,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
            stop_state.unlink()

    def test_interrupt_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                side_effect=KeyboardInterrupt(),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )


if __name__ == "__main__":
    unittest.main()
