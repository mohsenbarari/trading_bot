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
from scripts import two_site_proposal_runner as proposal_runner


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
            "gemini_worker_run_id": "worker-001",
            "gemini_reviewer_run_id": "reviewer-001",
            "proposal_sha256": "c" * 64,
        }
        result.update(changes)
        if result["commit"] is None:
            for field in ("gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256"):
                if field not in changes:
                    result[field] = None
        return result

    def gemini_chain(self, source_head="a" * 40):
        proposals = self.root / ".local/two-site-refactor/proposals"
        worker_dir = proposals / "worker-001"
        reviewer_dir = proposals / "reviewer-001"
        worker_dir.mkdir(parents=True)
        reviewer_dir.mkdir()
        task = {
            "task_id": "CHAIN-001",
            "goal": "Change one exact file.",
            "context_files": ["change.py"],
            "allowed_files": ["change.py"],
            "requirements": ["Preserve the exact replacement"],
            "test_selectors": ["tests/test_change.py"],
        }
        sources = {
            "change.py": {
                "sha256": proposal_runner.sha(b"OLD = True\n"),
                "content": "OLD = True\n",
            },
        }
        worker_result = {
            "status": "PROPOSAL_READY",
            "summary": "Replaced the bounded value.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [{
                "path": "change.py",
                "original_sha256": sources["change.py"]["sha256"],
                "content": "NEW = True\n",
            }],
        }
        proposal_runner.write_json(worker_dir / "result.json", worker_result)
        worker_text = (worker_dir / "result.json").read_text()
        proposal_runner.write_json(worker_dir / "task.json", {
            "task": task, "source_head": source_head, "sources": sources,
        })
        proposal_runner.write_json(worker_dir / "status.json", {
            "run_id": "worker-001", "role": "worker", "state": "PROPOSAL_READY",
            "model": proposal_runner.MODEL, "head": source_head,
            "conversation_id": "worker-conversation",
            "result_sha256": proposal_runner.file_sha256(worker_dir / "result.json"),
        })
        reviewer_result = {
            "status": "VERIFIED",
            "summary": "All exact requirements are covered.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [],
        }
        proposal_runner.write_json(reviewer_dir / "result.json", reviewer_result)
        proposal_runner.write_json(reviewer_dir / "task.json", {
            "task": task, "source_head": source_head, "sources": sources,
            "proposal": worker_text,
        })
        proposal_runner.write_json(reviewer_dir / "status.json", {
            "run_id": "reviewer-001", "role": "verifier", "state": "VERIFIED",
            "model": proposal_runner.MODEL, "head": source_head,
            "conversation_id": "reviewer-conversation",
            "input_worker_run_id": "worker-001",
            "input_proposal_sha256": proposal_runner.file_sha256(worker_dir / "result.json"),
            "result_sha256": proposal_runner.file_sha256(reviewer_dir / "result.json"),
        })
        result = self.result(
            proposal_sha256=proposal_runner.file_sha256(worker_dir / "result.json"),
        )
        return result, worker_result, reviewer_dir

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
            self.result(gemini_reviewer_run_id=None),
            self.result(gemini_worker_run_id=7),
            self.result(proposal_sha256="short"),
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
        proposal = {"changes": []}
        with patch.object(autopilot, "validate_repository", return_value=after), \
                patch.object(autopilot, "git", return_value="") as git, \
                patch.object(autopilot, "validate_gemini_chain", return_value=proposal) as chain, \
                patch.object(autopilot, "validate_committed_proposal") as committed:
            checked = autopilot.validate_transition(self.root, before, self.result())
        self.assertTrue(checked["head_changed"])
        chain.assert_called_once_with(self.root, "a" * 40, self.result())
        committed.assert_called_once_with(self.root, "a" * 40, "b" * 40, proposal)
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

    def test_gemini_chain_requires_exact_proposal_and_fresh_reviewer(self):
        result, worker_result, reviewer_dir = self.gemini_chain()
        self.assertEqual(
            autopilot.validate_gemini_chain(self.root, "a" * 40, result),
            worker_result,
        )
        reviewer_status = json.loads((reviewer_dir / "status.json").read_text())
        reviewer_status["conversation_id"] = "worker-conversation"
        proposal_runner.write_json(reviewer_dir / "status.json", reviewer_status)
        with self.assertRaisesRegex(autopilot.AutopilotError, "independent fresh"):
            autopilot.validate_gemini_chain(self.root, "a" * 40, result)

    def test_committed_files_must_equal_exact_gemini_proposal(self):
        (self.root / "change.py").write_text("OLD = True\n")
        subprocess.check_call(["git", "init", "-q"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=self.root)
        subprocess.check_call(["git", "config", "user.name", "Test"], cwd=self.root)
        subprocess.check_call(["git", "add", "change.py"], cwd=self.root)
        subprocess.check_call(["git", "commit", "-qm", "baseline"], cwd=self.root)
        before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        (self.root / "change.py").write_text("NEW = True\n")
        subprocess.check_call(["git", "add", "change.py"], cwd=self.root)
        subprocess.check_call(["git", "commit", "-qm", "proposal"], cwd=self.root)
        after = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()
        proposal = {"changes": [{"path": "change.py", "content": "NEW = True\n"}]}
        autopilot.validate_committed_proposal(self.root, before, after, proposal)
        proposal["changes"][0]["content"] = "CODEX_EDIT = True\n"
        with self.assertRaisesRegex(autopilot.AutopilotError, "bytes differ"):
            autopilot.validate_committed_proposal(self.root, before, after, proposal)

    def test_codex_command_is_pinned_bounded_and_not_bypass_mode(self):
        command = autopilot.codex_command(
            Path("/usr/bin/codex"), self.root, self.root / "run", "prompt",
        )
        self.assertIn("gpt-5.6-sol", command)
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--sandbox", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertNotIn("--dangerously-bypass-hook-trust", command)

    def test_supervisor_prompt_enforces_gemini_author_review_then_codex_gate(self):
        prompt = (autopilot.ROOT / autopilot.PROMPT_REL).read_text()
        worker = prompt.index("Gemini Worker is the")
        reviewer = prompt.index("runner role `verifier`")
        codex = prompt.index("final Codex reviewer")
        self.assertLess(worker, reviewer)
        self.assertLess(reviewer, codex)
        self.assertIn("different conversation ID", prompt)
        self.assertIn("reviewed_proposal_sha256", prompt)
        self.assertIn("Do not launch a nested", prompt)
        self.assertIn("never author or repair implementation", prompt)
        self.assertIn("At most two materially different correction attempts", prompt)

    def test_codex_version_probe_uses_bounded_streaming(self):
        cli = self.root / "codex"
        cli.write_text("#!/usr/bin/env python3\nprint('codex-cli 0.153.0')\n")
        cli.chmod(0o755)
        digest = autopilot.file_sha256(cli)
        with patch.object(autopilot, "EXPECTED_CODEX_CLI_SHA256", digest):
            identity = autopilot.codex_identity(cli, self.root)
        self.assertEqual(identity["version"], "codex-cli 0.153.0")
        self.assertEqual(identity["sha256"], digest)
        self.assertEqual(identity["model"], "gpt-5.6-sol")
        self.assertEqual(identity["reasoning"], "xhigh")

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

    def test_run_loop_publishes_running_before_cycle_execution(self):
        state_root = self.root / ".local/two-site-refactor/autopilot"
        with patch.object(autopilot, "git", side_effect=["a" * 40]), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=KeyboardInterrupt), \
                patch.object(autopilot, "write_supervisor_state") as publish:
            with self.assertRaises(KeyboardInterrupt):
                autopilot.run_loop(self.root, self.root / "codex", 1, 300, True)
        publish.assert_called_once_with(
            state_root, "RUNNING", cycle=1, head="a" * 40,
            summary="Executing Gemini Worker -> Gemini Reviewer -> Codex final gate.",
            blockers=[],
        )

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
