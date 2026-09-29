"""Fail-closed Supervisor startup hookup for a terminal cold adoption."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import two_site_codex_autopilot as autopilot
from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_cold_start_startup_read import ColdStartStartupReadError


def _repository(head: str, assignment: str, allowed_path: str) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "2" * 40 for ref in autopilot.PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "3" * 40 for p in autopilot.CONTROL_PATHS},
        "assignment_scope": AssignmentScope(
            assignment_id=assignment,
            stage_id="P1-00b",
            allowed_paths=(allowed_path,),
            forbidden_paths=(),
            expires_at="2099-01-01T00:00:00Z",
            sha256="a" * 64,
        ),
    }


class ColdStartHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="ae03-startup-hook-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(strict=True)
        self.state = self.root / ".local/two-site-refactor/autopilot"
        self.state.mkdir(parents=True, mode=0o700)
        os.chmod(self.state, 0o700)
        self.old = _repository("1" * 40, "OLD-ASSIGN", "tests/old_unit.py")
        self.new = _repository("2" * 40, "NEW-ASSIGN", "tests/new_unit.py")
        self.old_epoch = autopilot.ensure_rescope_epoch_dir(self.state, self.old)

    def _startup(self, *, root: Path | None = None) -> None:
        autopilot.validate_rescope_epoch_startup(self.state, self.new, root=root)

    def test_valid_one_prior_epoch_uses_normalized_record_then_revalidates_repo(self) -> None:
        with (
            patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked") as reader,
            patch.object(autopilot, "validate_repository", return_value=self.new) as refresh,
        ):
            self._startup(root=self.root)
        reader.assert_called_once_with(
            self.root,
            self.state,
            autopilot.validate_accepted_repository(self.new),
            self.old_epoch.name,
        )
        refresh.assert_called_once_with(self.root, require_clean=True)

    def test_no_explicit_root_or_missing_record_stays_blocked(self) -> None:
        with patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked") as reader:
            with self.assertRaisesRegex(autopilot.AutopilotError, "prior epoch requires matching accepted checkpoint"):
                self._startup()
            reader.assert_not_called()

        with patch(
            "scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked",
            side_effect=ColdStartStartupReadError("Adoption record missing"),
        ):
            with self.assertRaisesRegex(autopilot.AutopilotError, "prior epoch requires matching accepted checkpoint"):
                self._startup(root=self.root)

    def test_multiple_prior_or_preexisting_current_epoch_blocks_before_reader(self) -> None:
        other = _repository("3" * 40, "OTHER-ASSIGN", "tests/other_unit.py")
        other_epoch = autopilot.ensure_rescope_epoch_dir(self.state, other)
        with patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked") as reader:
            with self.assertRaises(autopilot.AutopilotError):
                self._startup(root=self.root)
            reader.assert_not_called()
        other_epoch.rmdir()

        current = autopilot.ensure_rescope_epoch_dir(self.state, self.new)
        with patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked") as reader:
            with self.assertRaises(autopilot.AutopilotError):
                self._startup(root=self.root)
            reader.assert_not_called()
        current.rmdir()

    def test_repository_drift_and_reader_interrupt_fail_without_provider(self) -> None:
        changed = _repository("4" * 40, "NEW-ASSIGN", "tests/new_unit.py")
        with (
            patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked"),
            patch.object(autopilot, "validate_repository", return_value=changed),
        ):
            with self.assertRaises(autopilot.AutopilotError):
                self._startup(root=self.root)

        for interrupt in (KeyboardInterrupt, SystemExit):
            with self.subTest(interrupt=interrupt.__name__):
                with patch(
                    "scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked",
                    side_effect=interrupt,
                ):
                    with self.assertRaises(interrupt):
                        self._startup(root=self.root)

    def test_matching_checkpoint_bypasses_adoption_reader(self) -> None:
        with (
            patch.object(autopilot, "_load_accepted_checkpoint", return_value={"valid": True}),
            patch.object(autopilot, "_accepted_checkpoint_matches_repository", return_value=True),
            patch("scripts.two_site_cold_start_startup_read.require_terminal_cold_adoption_startup_locked") as reader,
        ):
            self._startup()
        reader.assert_not_called()

    def test_run_loop_passes_explicit_root_under_supervisor_lock(self) -> None:
        with (
            patch.object(autopilot, "validate_repository", return_value=self.new),
            patch.object(autopilot, "validate_rescope_epoch_startup", side_effect=autopilot.AutopilotError("sentinel")) as gate,
            patch.object(autopilot, "run_cycle") as cycle,
        ):
            result = autopilot.run_loop(self.root, self.root / "unused-codex", 1, 300, True)
        self.assertEqual(result, 2)
        self.assertEqual(gate.call_count, 1)
        self.assertEqual(gate.call_args.kwargs, {"root": self.root})
        cycle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
