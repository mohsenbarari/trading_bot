#!/usr/bin/env python3
"""Hermetic unit tests for maintenance-transition startup verification and wiring."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    validate_accepted_repository,
)
from scripts.two_site_maintenance_transition_startup import (
    MaintenanceTransitionStartupError,
    WIRING_REPLACEMENTS,
    require_reviewed_maintenance_transition_locked,
)
from scripts.two_site_maintenance_transition_storage import (
    MaintenanceTransitionStorageError,
)


def _find_repo_root() -> Path | None:
    curr = Path(__file__).resolve().parent
    while curr != curr.parent:
        if (curr / "scripts" / "two_site_codex_autopilot.py").is_file():
            return curr
        curr = curr.parent
    return None


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


class TestWiringReplacementsMetadata(unittest.TestCase):
    def test_wiring_replacements_contract(self) -> None:
        self.assertEqual(len(WIRING_REPLACEMENTS), 3)
        allowed_targets = {
            "scripts/two_site_codex_autopilot.py",
            "scripts/two_site_autopilot_monitor_preflight.py",
        }
        seen_targets = set()
        seen_old = set()
        seen_new = set()
        for item in WIRING_REPLACEMENTS:
            self.assertIsInstance(item, tuple)
            self.assertEqual(len(item), 3)
            rel_path, old_frag, new_frag = item
            self.assertIn(rel_path, allowed_targets)
            seen_targets.add(rel_path)
            self.assertIsInstance(old_frag, str)
            self.assertIsInstance(new_frag, str)
            self.assertTrue(old_frag.strip())
            self.assertTrue(new_frag.strip())
            self.assertNotIn(old_frag, seen_old)
            self.assertNotIn(new_frag, seen_new)
            seen_old.add(old_frag)
            seen_new.add(new_frag)
            self.assertNotIn(old_frag, new_frag)
            self.assertLess(len(old_frag), 600)
            self.assertLess(len(new_frag), 900)
        self.assertEqual(seen_targets, allowed_targets)

    def test_wiring_replacements_against_repo_files(self) -> None:
        root = _find_repo_root()
        if root is None:
            self.skipTest("Repository root not detected")
        for rel_path, old_frag, new_frag in WIRING_REPLACEMENTS:
            target_path = root / rel_path
            self.assertTrue(target_path.is_file(), f"Target file missing: {rel_path}")
            text = target_path.read_text(encoding="utf-8")
            has_old = old_frag in text
            has_new = new_frag in text
            self.assertTrue(
                has_old ^ has_new,
                f"For {rel_path}, exactly one of old or new must be present (pre/post apply).",
            )
            if has_old:
                self.assertEqual(text.count(old_frag), 1)
                self.assertEqual(text.count(new_frag), 0)
            else:
                self.assertEqual(text.count(new_frag), 1)
                self.assertEqual(text.count(old_frag), 0)


@mock.patch(
    "scripts.two_site_maintenance_transition_startup.read_root_maintenance_bridge_locked",
    new=lambda *args, **kwargs: None,
)
class TestRequireReviewedMaintenanceTransitionLocked(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = _make_repo()
        self.norm_repo = validate_accepted_repository(self.repo)

    def _setup_workspace(self, td: str) -> tuple[Path, Path]:
        root = Path(td).resolve()
        state_root = root / ".local" / "two-site-refactor" / "autopilot"
        state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        return root, state_root

    def test_positive_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            record = {"new_repository": copy.deepcopy(self.norm_repo)}
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                result = require_reviewed_maintenance_transition_locked(
                    root, state_root, self.repo
                )
                self.assertIsNone(result)

    def test_negative_missing_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_negative_invalid_record_or_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                side_effect=MaintenanceTransitionStorageError("corrupt"),
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value="not a dict",
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_negative_mismatched_repo_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)

            # Different head
            diff_head_repo = copy.deepcopy(self.norm_repo)
            diff_head_repo["head"] = "f" * 40
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value={"new_repository": diff_head_repo},
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

            # Different scope
            diff_scope_repo = copy.deepcopy(self.norm_repo)
            diff_scope_repo["assignment_scope"] = copy.deepcopy(
                diff_scope_repo["assignment_scope"]
            )
            diff_scope_repo["assignment_scope"]["stage_id"] = "P1-99"
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value={"new_repository": diff_scope_repo},
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

            # Different refs
            diff_refs_repo = copy.deepcopy(self.norm_repo)
            diff_refs_repo["protected_refs"] = dict(diff_refs_repo["protected_refs"])
            first_ref = sorted(PROTECTED_REFS)[0]
            diff_refs_repo["protected_refs"][first_ref] = "9" * 40
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value={"new_repository": diff_refs_repo},
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

            # Different control
            diff_ctrl_repo = copy.deepcopy(self.norm_repo)
            diff_ctrl_repo["control_blobs"] = dict(diff_ctrl_repo["control_blobs"])
            first_ctrl = sorted(diff_ctrl_repo["control_blobs"].keys())[0]
            diff_ctrl_repo["control_blobs"][first_ctrl] = "8" * 40
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value={"new_repository": diff_ctrl_repo},
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_negative_repo_drift_after_reader_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            record = {"new_repository": copy.deepcopy(self.norm_repo)}
            drifted_repo = copy.deepcopy(self.repo)
            drifted_repo["head"] = "e" * 40
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=drifted_repo,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )

    def test_negative_stop_file_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            record = {"new_repository": copy.deepcopy(self.norm_repo)}

            # root / STOP
            stop_root = root / "STOP"
            stop_root.write_text("STOP\n")
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
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
            stop_state.write_text("STOP\n")
            with mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=self.repo,
            ):
                with self.assertRaises(MaintenanceTransitionStartupError):
                    require_reviewed_maintenance_transition_locked(
                        root, state_root, self.repo
                    )
            stop_state.unlink()

    def test_negative_invalid_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root, state_root = self._setup_workspace(td)
            with self.assertRaises(MaintenanceTransitionStartupError):
                require_reviewed_maintenance_transition_locked(
                    Path("relative/root"), state_root, self.repo
                )
            with self.assertRaises(MaintenanceTransitionStartupError):
                require_reviewed_maintenance_transition_locked(
                    root, root / "wrong_state_root", self.repo
                )


@mock.patch(
    "scripts.two_site_maintenance_transition_startup.read_root_maintenance_bridge_locked",
    new=lambda *args, **kwargs: None,
)
class TestWiringIntegration(unittest.TestCase):
    def _is_applied(self, rel_path: str, new_frag: str) -> bool:
        root = _find_repo_root()
        if root is None:
            return False
        target = root / rel_path
        if not target.is_file():
            return False
        return new_frag in target.read_text(encoding="utf-8")

    def test_multi_epoch_startup_when_applied(self) -> None:
        if not self._is_applied(
            "scripts/two_site_codex_autopilot.py", WIRING_REPLACEMENTS[0][2]
        ):
            self.skipTest("Autopilot startup wiring replacement not yet applied")

        from scripts.two_site_codex_autopilot import validate_rescope_epoch_startup

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            state_root = root / ".local" / "two-site-refactor" / "autopilot"
            state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            repo = _make_repo()
            norm_repo = validate_accepted_repository(repo)

            epoch_1 = state_root / ("rescope-" + "1" * 64)
            epoch_2 = state_root / ("rescope-" + "2" * 64)
            epoch_1.mkdir(mode=0o700)
            epoch_2.mkdir(mode=0o700)

            record = {"new_repository": copy.deepcopy(norm_repo)}
            with mock.patch(
                "scripts.two_site_codex_autopilot._load_accepted_checkpoint",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=repo,
            ):
                validate_rescope_epoch_startup(state_root, repo, root=root)

            with mock.patch(
                "scripts.two_site_codex_autopilot._load_accepted_checkpoint",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=repo,
            ):
                with self.assertRaises(AutopilotError):
                    validate_rescope_epoch_startup(state_root, repo, root=root)

            with self.assertRaises(AutopilotError):
                validate_rescope_epoch_startup(state_root, repo, root=None)

    def test_monitor_preflight_stale_checkpoint_when_applied(self) -> None:
        if not self._is_applied(
            "scripts/two_site_autopilot_monitor_preflight.py", WIRING_REPLACEMENTS[1][2]
        ):
            self.skipTest("Monitor preflight wiring replacement not yet applied")

        from scripts.two_site_autopilot_monitor_preflight import run_preflight

        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            root.chmod(0o700)
            state_root = root / ".local" / "two-site-refactor" / "autopilot"
            state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
            curr = state_root
            while curr != root:
                curr.chmod(0o700)
                curr = curr.parent

            repo = _make_repo()
            norm_repo = validate_accepted_repository(repo)
            status_file = state_root / "status.json"
            status_file.write_text(json.dumps({"head": repo["head"], "state": "CONTINUE"}))
            status_file.chmod(0o600)

            record = {"new_repository": copy.deepcopy(norm_repo)}
            with mock.patch(
                "scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_autopilot_monitor_preflight.validate_repository",
                return_value=repo,
            ), mock.patch(
                "scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=record,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=repo,
            ):
                ok, reason = run_preflight(root)
                self.assertTrue(ok)
                self.assertEqual(reason, "READY")

            with mock.patch(
                "scripts.two_site_autopilot_monitor_preflight._load_accepted_checkpoint",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_autopilot_monitor_preflight.validate_repository",
                return_value=repo,
            ), mock.patch(
                "scripts.two_site_autopilot_monitor_preflight.validate_rescope_epoch_startup",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.read_maintenance_transition_locked",
                return_value=None,
            ), mock.patch(
                "scripts.two_site_maintenance_transition_startup.validate_repository",
                return_value=repo,
            ):
                ok, reason = run_preflight(root)
                self.assertFalse(ok)
                self.assertEqual(reason, "STALE_CHECKPOINT")


if __name__ == "__main__":
    unittest.main()
