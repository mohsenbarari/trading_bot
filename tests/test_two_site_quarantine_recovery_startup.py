#!/usr/bin/env python3
"""Hermetic unit tests for quarantine recovery startup fallback verification."""
from __future__ import annotations

import copy
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

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
from scripts.two_site_root_maintenance_bridge import RootMaintenanceBridgeError


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


@mock.patch(
    "scripts.two_site_maintenance_transition_startup.read_root_maintenance_bridge_locked",
    new=lambda *args, **kwargs: None,
)
class TestQuarantineRecoveryStartupFallback(unittest.TestCase):
    """Legacy/quarantine unit fixtures model an authenticated unmarked Root route.

    Their temporary roots have no real Git/Scope/Broker bridge fixture. Marked
    Root errors and interrupts are tested separately without this boundary.
    """
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
            ) as recovery:
                with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                                            "^quarantine recovery verification failed$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)

            # Recovery reader returns non-dict
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                return_value="not an envelope dict",
            ) as recovery:
                with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                    "^no reviewed maintenance transition or quarantine recovery record found$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)

            # Missing or non-dict record
            for bad_record in (None, "not a dict", []):
                with mock.patch(
                    "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                    return_value=None,
                ), mock.patch(
                    "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                    return_value={"record": bad_record},
                ) as recovery:
                    with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                                                "^invalid quarantine recovery record$"):
                        require_reviewed_maintenance_transition_locked(
                            root, state_root, self.repo
                        )
                    recovery.assert_called_once_with(root, state_root)

            # Missing or non-dict record.new_repository
            for bad_repo in (None, "not a dict", []):
                with mock.patch(
                    "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                    return_value=None,
                ), mock.patch(
                    "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                    return_value={"record": {"new_repository": bad_repo}},
                ) as recovery:
                    with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                                                "^invalid quarantine recovery record$"):
                        require_reviewed_maintenance_transition_locked(
                            root, state_root, self.repo
                        )
                    recovery.assert_called_once_with(root, state_root)

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
            ) as recovery:
                with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                    "^record new_repository does not match current repository$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)

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
            ) as recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=drifted_repo,
            ) as live:
                with self.assertRaisesRegex(MaintenanceTransitionStartupError,
                                            "^repository drift detected after broker lock release$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)
                live.assert_called_once_with(root, require_clean=True)

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
            ) as recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                with self.assertRaisesRegex(MaintenanceTransitionStartupError, "^STOP file detected$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)
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
            ) as recovery, mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                with self.assertRaisesRegex(MaintenanceTransitionStartupError, "^STOP file detected$"):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)
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
            ) as recovery:
                with self.assertRaises(KeyboardInterrupt):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
                recovery.assert_called_once_with(root, state_root)


class TestRootBridgeRoutingGuards(unittest.TestCase):
    """No unmarked-route patch: Root rejection cannot reach either fallback.

    Real marked Scope/Git/custody behavior remains covered by all three full
    Root bridge suites; these tests isolate startup's dispatch/error boundary.
    """

    def _assert_root_failure_without_fallback(self, failure, expected, message=None):
        with tempfile.TemporaryDirectory() as td:
            root, state_root = TestQuarantineRecoveryStartupFallback._setup_workspace(self, td)
            repo = _make_repo()
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_root_maintenance_bridge_locked",
                side_effect=failure,
            ) as bridge, mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                side_effect=AssertionError("Root failure must not reach legacy maintenance"),
            ) as legacy, mock.patch(
                "scripts.two_site_quarantine_recovery_storage.read_quarantine_recovery_locked",
                side_effect=AssertionError("Root failure must not reach quarantine"),
            ) as recovery:
                assertion = self.assertRaisesRegex(expected, message) if message else self.assertRaises(expected)
                with assertion as caught:
                    require_reviewed_maintenance_transition_locked(root, state_root, repo)
                if not isinstance(failure, RootMaintenanceBridgeError):
                    self.assertIs(caught.exception, failure)
                bridge.assert_called_once_with(root, state_root, repo)
                legacy.assert_not_called()
                recovery.assert_not_called()

    def test_root_bridge_error_never_falls_back(self):
        self._assert_root_failure_without_fallback(
            RootMaintenanceBridgeError("fixture marked Root rejection"),
            MaintenanceTransitionStartupError, "^root maintenance bridge verification failed$",
        )

    def test_root_bridge_interrupts_preserve_identity_without_fallback(self):
        for failure in (KeyboardInterrupt("Root interrupt"), SystemExit(7)):
            with self.subTest(kind=type(failure).__name__):
                self._assert_root_failure_without_fallback(failure, type(failure))


if __name__ == "__main__":
    unittest.main()
