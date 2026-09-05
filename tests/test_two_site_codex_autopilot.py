"""Offline safety contracts for the background Codex supervisor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import two_site_codex_autopilot as autopilot


class AutopilotContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def result(self, **changes):
        result = {
            "schema_version": "1.0",
            "state": "CONTINUE",
            "summary": "Committed one bounded unit.",
            "commit": "b" * 40,
            "next_action": "Open the next safe internal unit.",
            "blockers": [],
        }
        result.update(changes)
        return result

    def test_result_contract_is_exact_and_state_consistent(self):
        self.assertEqual(autopilot.validate_result(self.result()), self.result())
        blocked = self.result(
            state="BLOCKED", commit=None, next_action=None, blockers=["Human gate."],
        )
        self.assertEqual(autopilot.validate_result(blocked), blocked)
        complete = self.result(state="COMPLETE", commit=None, next_action=None)
        self.assertEqual(autopilot.validate_result(complete), complete)

        invalid = [
            dict(self.result(), extra=True),
            self.result(schema_version="2.0"),
            self.result(state="BLOCKED", blockers=[]),
            self.result(state="CONTINUE", next_action=None),
            self.result(state="COMPLETE", blockers=["contradiction"]),
            self.result(commit="short"),
            self.result(summary=" "),
        ]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(autopilot.AutopilotError):
                autopilot.validate_result(result)

    def test_transition_requires_clean_descendant_progress_and_protected_refs(self):
        before = {
            "head": "a" * 40,
            "protected_refs": {"main": "1" * 40},
            "control_blobs": {"runner": "2" * 40},
        }
        after = {
            "head": "b" * 40,
            "protected_refs": before["protected_refs"],
            "control_blobs": before["control_blobs"],
        }
        with patch.object(autopilot, "validate_repository", return_value=after), \
                patch.object(autopilot, "git", return_value="") as git:
            checked = autopilot.validate_transition(self.root, before, self.result())
        self.assertTrue(checked["head_changed"])
        git.assert_called_once_with(
            self.root, "merge-base", "--is-ancestor", "a" * 40, "b" * 40,
        )

        unchanged = dict(after, head="a" * 40)
        with patch.object(autopilot, "validate_repository", return_value=unchanged):
            with self.assertRaisesRegex(autopilot.AutopilotError, "no-progress"):
                autopilot.validate_transition(
                    self.root, before, self.result(commit=None),
                )

        changed_ref = dict(after, protected_refs={"main": "3" * 40})
        with patch.object(autopilot, "validate_repository", return_value=changed_ref):
            with self.assertRaisesRegex(autopilot.AutopilotError, "protected"):
                autopilot.validate_transition(self.root, before, self.result())

        changed_control = dict(after, control_blobs={"runner": "4" * 40})
        with patch.object(autopilot, "validate_repository", return_value=changed_control):
            with self.assertRaisesRegex(autopilot.AutopilotError, "control"):
                autopilot.validate_transition(self.root, before, self.result())

    def test_codex_command_is_pinned_bounded_and_not_bypass_mode(self):
        command = autopilot.codex_command(
            Path("/usr/bin/codex"), self.root, self.root / "run", "prompt",
        )
        self.assertIn("gpt-6-astra", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("--dangerously-bypass-hook-trust", command)

    def test_codex_version_probe_uses_bounded_streaming(self):
        cli = self.root / "codex"
        cli.write_text("#!/usr/bin/env python3\nprint('codex-cli 0.153.0')\n")
        cli.chmod(0o755)
        digest = autopilot.file_sha256(cli)
        with patch.object(autopilot, "EXPECTED_CODEX_CLI_SHA256", digest):
            identity = autopilot.codex_identity(cli, self.root)
        self.assertEqual(identity["version"], "codex-cli 0.153.0")
        self.assertEqual(identity["sha256"], digest)
        self.assertEqual(identity["model"], "gpt-6-astra")
        self.assertEqual(identity["reasoning"], "high")

    def test_supervisor_lock_is_exclusive_and_rejects_hardlinks(self):
        lock_path = self.root / "autopilot.lock"
        with autopilot.exclusive_supervisor_lock(lock_path):
            with self.assertRaisesRegex(autopilot.AutopilotError, "already owns"):
                with autopilot.exclusive_supervisor_lock(lock_path):
                    self.fail("a second supervisor must never acquire the same lock")

        hardlinked_lock = self.root / "hardlinked.lock"
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "outside.lock"
            outside.write_text("")
            os.link(outside, hardlinked_lock)
            with self.assertRaisesRegex(autopilot.AutopilotError, "trusted regular"):
                with autopilot.exclusive_supervisor_lock(hardlinked_lock):
                    self.fail("a hardlinked lock must never be trusted")

    def test_retention_removes_only_old_successes_and_preserves_blockers(self):
        state_root = self.root / "autopilot"
        runs = state_root / "runs"
        runs.mkdir(parents=True)
        for index in range(24):
            path = runs / f"cycle-{index:04d}-20260905T120000Z-{index + 10}"
            path.mkdir()
            (path / "status.json").write_text(json.dumps({
                "state": "BLOCKED" if index == 0 else "CONTINUE",
            }))
            os.utime(path, (index + 1, index + 1))
        removed = autopilot.prune_runs(state_root, keep=20)
        self.assertEqual(len(removed), 3)
        self.assertTrue((runs / "cycle-0000-20260905T120000Z-10").exists())
        self.assertEqual(len(list(runs.iterdir())), 21)

    def test_repository_gate_requires_tracked_controls_clean_and_correct_branch(self):
        for relative in autopilot.CONTROL_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (autopilot.ROOT / autopilot.SCHEMA_REL).read_text()
                if relative == autopilot.SCHEMA_REL else "control\n"
            )
        subprocess.check_call(["git", "init", "-q"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=self.root)
        subprocess.check_call(["git", "add", "."], cwd=self.root)
        subprocess.check_call(["git", "commit", "-qm", "baseline"], cwd=self.root)
        subprocess.check_call(["git", "branch", "-M", autopilot.BRANCH], cwd=self.root)
        state = autopilot.validate_repository(self.root)
        self.assertRegex(str(state["head"]), r"^[0-9a-f]{40}$")
        self.assertEqual(set(state["control_blobs"]), {
            path.as_posix() for path in autopilot.CONTROL_PATHS
        })
        (self.root / "untracked.txt").write_text("dirty\n")
        with self.assertRaisesRegex(autopilot.AutopilotError, "not clean"):
            autopilot.validate_repository(self.root)

    def test_repository_gate_rejects_provider_incompatible_schema_before_launch(self):
        for relative in autopilot.CONTROL_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (autopilot.ROOT / autopilot.SCHEMA_REL).read_text()
                if relative == autopilot.SCHEMA_REL else "control\n"
            )
        subprocess.check_call(["git", "init", "-q"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=self.root)
        subprocess.check_call(["git", "add", "."], cwd=self.root)
        subprocess.check_call(["git", "commit", "-qm", "baseline"], cwd=self.root)
        subprocess.check_call(["git", "branch", "-M", autopilot.BRANCH], cwd=self.root)
        schema_path = self.root / autopilot.SCHEMA_REL
        schema = json.loads(schema_path.read_text())
        del schema["properties"]["schema_version"]["type"]
        schema_path.write_text(json.dumps(schema))
        subprocess.check_call(["git", "add", "."], cwd=self.root)
        subprocess.check_call(["git", "commit", "-qm", "bad schema"], cwd=self.root)
        with self.assertRaisesRegex(autopilot.AutopilotError, "schema"):
            autopilot.validate_repository(self.root)

    def test_repository_gate_rejects_hardlinked_control_input(self):
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "external-control"
            outside.write_text("control\n")
            for relative in autopilot.CONTROL_PATHS:
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if relative == autopilot.PROMPT_REL:
                    os.link(outside, path)
                else:
                    path.write_text(
                        (autopilot.ROOT / autopilot.SCHEMA_REL).read_text()
                        if relative == autopilot.SCHEMA_REL else "control\n"
                    )
            subprocess.check_call(["git", "init", "-q"], cwd=self.root)
            subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=self.root)
            subprocess.check_call(["git", "config", "user.name", "Test"], cwd=self.root)
            subprocess.check_call(["git", "add", "."], cwd=self.root)
            subprocess.check_call(["git", "commit", "-qm", "hardlinked control"], cwd=self.root)
            subprocess.check_call(["git", "branch", "-M", autopilot.BRANCH], cwd=self.root)
            with self.assertRaisesRegex(autopilot.AutopilotError, "unsafe"):
                autopilot.validate_repository(self.root)


if __name__ == "__main__":
    unittest.main()
