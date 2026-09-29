"""Tests for STOP file safety and presence checking in scripts/two_site_autopilot_tmux.sh."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "two_site_autopilot_tmux.sh"


class TestTwoSiteAutopilotTmuxStop(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SCRIPT_PATH.is_file(), f"Missing launcher script: {SCRIPT_PATH}")
        self.script_content = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_bash_syntax(self) -> None:
        """Validate launcher shell script syntax with bash -n."""
        proc = subprocess.run(
            ["bash", "-n", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"bash -n failed on {SCRIPT_PATH}:\nstdout: {proc.stdout}\nstderr: {proc.stderr}",
        )

    def test_start_source_safety_invariants(self) -> None:
        """Assert source-level invariants for START in scripts/two_site_autopilot_tmux.sh."""
        start_match = re.search(r"start\)(.*?);;", self.script_content, re.DOTALL)
        self.assertIsNotNone(start_match, "Could not find start) branch in script")
        start_block = start_match.group(1)

        # 1. No removal or unlinking of STOP in start
        self.assertNotRegex(
            start_block,
            r"(?:rm|unlink)\s+.*STOP",
            "start) must never remove or unlink STOP",
        )

        # 2. Both checks exist before launch
        stop_check_pattern = (
            r'\[\[\s+-e\s+"\$state_root/STOP"\s+\|\|\s+-L\s+"\$state_root/STOP"\s+\|\|\s+'
            r'-e\s+"\$root/STOP"\s+\|\|\s+-L\s+"\$root/STOP"\s+\]\]'
        )
        checks = list(re.finditer(stop_check_pattern, start_block))
        self.assertEqual(
            len(checks),
            2,
            f"Expected exactly 2 STOP presence checks in start), found {len(checks)}",
        )

        # 3. First check is before broker launch/session creation
        broker_launch_pos = start_block.find('tmux new-session -d -s "$broker_session"')
        self.assertGreater(broker_launch_pos, 0, "Broker session creation not found")
        self.assertLess(
            checks[0].start(),
            broker_launch_pos,
            "Initial STOP check must occur before broker launch",
        )

        # 4. Second check is immediately before supervisor session creation
        supervisor_launch_pos = start_block.find('tmux new-session -d -s "$session"')
        self.assertGreater(supervisor_launch_pos, 0, "Supervisor session creation not found")
        self.assertLess(
            checks[1].start(),
            supervisor_launch_pos,
            "Recheck must occur before supervisor session creation",
        )

    def test_extracted_stop_guard_behavior(self) -> None:
        """Hermetic execution of extracted STOP guard against files and broken symlinks."""
        guard_script = """
set -euo pipefail
if [[ -e "$state_root/STOP" || -L "$state_root/STOP" || -e "$root/STOP" || -L "$root/STOP" ]]; then
  printf 'ERROR: STOP file present\\n' >&2
  exit 2
fi
exit 0
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            root = tmp_path / "repo"
            state_root = root / ".local" / "two-site-refactor" / "autopilot"
            state_root.mkdir(parents=True, exist_ok=True)

            def run_guard() -> subprocess.CompletedProcess[str]:
                env = os.environ.copy()
                env["root"] = str(root)
                env["state_root"] = str(state_root)
                return subprocess.run(
                    ["bash", "-c", guard_script],
                    env=env,
                    capture_output=True,
                    text=True,
                )

            # Scenario 1: Clean state - guard passes
            res = run_guard()
            self.assertEqual(res.returncode, 0, "Guard should pass when STOP is absent")

            # Scenario 2: state_root/STOP as normal file
            stop_file = state_root / "STOP"
            stop_file.write_text("stop requested\n", encoding="utf-8")
            res = run_guard()
            self.assertEqual(res.returncode, 2, "Guard should fail when state_root/STOP exists")
            self.assertIn("STOP file present", res.stderr)
            stop_file.unlink()

            # Scenario 3: state_root/STOP as broken (dangling) symlink
            stop_file.symlink_to(state_root / "nonexistent_target")
            self.assertTrue(stop_file.is_symlink())
            self.assertFalse(stop_file.exists())
            res = run_guard()
            self.assertEqual(res.returncode, 2, "Guard should fail on dangling state_root/STOP symlink")
            self.assertIn("STOP file present", res.stderr)
            stop_file.unlink()

            # Scenario 4: root/STOP as normal file
            root_stop = root / "STOP"
            root_stop.write_text("stop requested\n", encoding="utf-8")
            res = run_guard()
            self.assertEqual(res.returncode, 2, "Guard should fail when root/STOP exists")
            self.assertIn("STOP file present", res.stderr)
            root_stop.unlink()

            # Scenario 5: root/STOP as broken (dangling) symlink
            root_stop.symlink_to(root / "nonexistent_target")
            self.assertTrue(root_stop.is_symlink())
            self.assertFalse(root_stop.exists())
            res = run_guard()
            self.assertEqual(res.returncode, 2, "Guard should fail on dangling root/STOP symlink")
            self.assertIn("STOP file present", res.stderr)
            root_stop.unlink()


if __name__ == "__main__":
    unittest.main()
