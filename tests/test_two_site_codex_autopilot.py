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
            "disposition": "NONE",
            "category": "NONE",
            "summary": "Committed one bounded unit.",
            "commit": "b" * 40,
            "next_action": "Open the next safe internal unit.",
            "blockers": [],
            "gemini_worker_run_id": "worker-001",
            "gemini_reviewer_run_id": "reviewer-001",
            "proposal_sha256": "c" * 64,
        }
        result.update(changes)
        if result["state"] == "BLOCKED" and "disposition" not in changes:
            result["disposition"] = "HALT"
        if result["state"] == "BLOCKED" and "category" not in changes:
            result["category"] = (
                "INTERNAL_REJECTION" if result["disposition"] == "RESCOPE" else "EXTERNAL_GATE"
            )
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
        rescope = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Rescope next safe step.", blockers=["Upstream design blocked."],
        )
        self.assertEqual(autopilot.validate_result(rescope), rescope)

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
            self.result(disposition="UNKNOWN"),
            self.result(disposition="HALT"),
            self.result(disposition="RESCOPE"),
            self.result(category="UNKNOWN"),
            self.result(category="INTERNAL_REJECTION"),
            self.result(category="EXTERNAL_GATE"),
            self.result(state="COMPLETE", commit=None, next_action=None, disposition="HALT"),
            self.result(state="COMPLETE", commit=None, next_action=None, disposition="RESCOPE"),
            self.result(state="BLOCKED", commit=None, next_action=None, blockers=["Gate."], disposition="NONE"),
            self.result(state="BLOCKED", disposition="RESCOPE", commit=None, next_action=None, blockers=["Gate."]),
            self.result(state="BLOCKED", disposition="RESCOPE", commit="b" * 40, next_action="Next.", blockers=["Gate."]),
            self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None, next_action="Next.",
                blockers=["Gate."], gemini_worker_run_id="worker-001",
                gemini_reviewer_run_id="reviewer-001", proposal_sha256="c" * 64,
            ),
            self.result(state="BLOCKED", disposition="HALT", commit="b" * 40, next_action=None, blockers=["Gate."]),
            self.result(
                state="BLOCKED", disposition="HALT", commit=None, next_action=None,
                blockers=["Gate."], gemini_worker_run_id="worker-001",
                gemini_reviewer_run_id="reviewer-001", proposal_sha256="c" * 64,
            ),
        ]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(autopilot.AutopilotError):
                autopilot.validate_result(result)

    def test_disposition_contracts_and_rescope_safety(self):
        self.assertEqual(self.result()["disposition"], "NONE")
        self.assertEqual(self.result(state="COMPLETE")["disposition"], "NONE")
        self.assertEqual(self.result(state="BLOCKED")["disposition"], "HALT")

        valid_continue = self.result(state="CONTINUE", disposition="NONE")
        self.assertEqual(autopilot.validate_result(valid_continue)["disposition"], "NONE")

        valid_complete = self.result(
            state="COMPLETE", disposition="NONE", commit=None, next_action=None,
        )
        self.assertEqual(autopilot.validate_result(valid_complete)["disposition"], "NONE")

        valid_halt_without_next = self.result(
            state="BLOCKED", disposition="HALT", commit=None, next_action=None, blockers=["Gate."],
        )
        self.assertEqual(autopilot.validate_result(valid_halt_without_next)["disposition"], "HALT")

        valid_halt_with_next = self.result(
            state="BLOCKED", disposition="HALT", commit=None, next_action="Inspect log.", blockers=["Gate."],
        )
        self.assertEqual(autopilot.validate_result(valid_halt_with_next)["disposition"], "HALT")

        valid_rescope = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Re-anchor bounded proposal.", blockers=["Out of scope."],
        )
        self.assertEqual(autopilot.validate_result(valid_rescope)["disposition"], "RESCOPE")

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(disposition="INVALID"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(state="CONTINUE", disposition="HALT"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(state="CONTINUE", disposition="RESCOPE"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(
                state="COMPLETE", disposition="HALT", commit=None, next_action=None,
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(
                state="COMPLETE", disposition="RESCOPE", commit=None, next_action=None,
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "disposition"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="NONE", commit=None, next_action=None, blockers=["Gate."],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "next action"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None, next_action=None, blockers=["Gate."],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "next_action"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None, next_action="   ", blockers=["Gate."],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "blocker"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None, next_action="Next.", blockers=[],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "null commit"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit="b" * 40,
                next_action="Next.", blockers=["Gate."],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "evidence"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None, next_action="Next.",
                blockers=["Gate."], gemini_worker_run_id="worker-001",
                gemini_reviewer_run_id="reviewer-001", proposal_sha256="c" * 64,
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "null commit"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="HALT", commit="b" * 40,
                next_action=None, blockers=["Gate."],
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "evidence"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="HALT", commit=None,
                next_action=None, blockers=["Gate."], gemini_worker_run_id="worker-001",
                gemini_reviewer_run_id="reviewer-001", proposal_sha256="c" * 64,
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(category="INVALID"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(state="CONTINUE", category="INTERNAL_REJECTION"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(state="CONTINUE", category="EXTERNAL_GATE"))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="COMPLETE", commit=None, next_action=None, category="INTERNAL_REJECTION",
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="COMPLETE", commit=None, next_action=None, category="EXTERNAL_GATE",
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None,
                next_action="Next.", blockers=["Gate."], category="EXTERNAL_GATE",
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="RESCOPE", commit=None,
                next_action="Next.", blockers=["Gate."], category="NONE",
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="HALT", commit=None,
                next_action=None, blockers=["Gate."], category="INTERNAL_REJECTION",
            ))

        with self.assertRaisesRegex(autopilot.AutopilotError, "category"):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="HALT", commit=None,
                next_action=None, blockers=["Gate."], category="NONE",
            ))

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

    def test_prompt_templates_enforce_recovery_disposition_and_symbol_guards(self):
        codex_prompt = (autopilot.ROOT / autopilot.PROMPT_REL).read_text()
        gemini_prompt = (autopilot.ROOT / "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md").read_text()

        # Coordinator recovery context and recovery disposition policy
        self.assertIn("Supervisor recovery context", codex_prompt)
        self.assertIn("CONTINUE and COMPLETE use disposition NONE", codex_prompt)
        self.assertIn("BLOCKED/RESCOPE", codex_prompt)
        self.assertIn("BLOCKED/HALT", codex_prompt)
        self.assertIn("RESCOPE must not reuse artifacts, bypass tests or", codex_prompt)

        # Known clean exhausted-correction test case is RESCOPE and external gates remain HALT
        self.assertIn("including two exhausted correction attempts—must return BLOCKED/RESCOPE with category INTERNAL_REJECTION", codex_prompt)
        self.assertIn("It must never use HALT solely because the per-unit correction budget is exhausted", codex_prompt)
        self.assertIn("require BLOCKED/HALT with category EXTERNAL_GATE", codex_prompt)

        # Coordinator authoritative-symbol gate and UserRole.BUYER prevention
        self.assertIn("distinct smaller or context-complete internal unit", codex_prompt)
        self.assertIn("authoritative current definitions", codex_prompt)
        self.assertIn("UserRole.BUYER", codex_prompt)
        self.assertIn("forbidden unless its authoritative definition proves it exists", codex_prompt)

        # Gemini Reviewer authoritative-symbol gate and UserRole.BUYER prevention
        self.assertIn("validate each referenced enum member", gemini_prompt)
        self.assertIn("authoritative supplied source", gemini_prompt)
        self.assertIn("CHANGES_REQUIRED", gemini_prompt)
        self.assertIn("UserRole.BUYER", gemini_prompt)
        self.assertIn("do not infer members such as", gemini_prompt)
        self.assertIn("transport representation is not a semantic review", gemini_prompt)

        # Gemini Reviewer sealed-context rule and prohibited native tool actions
        self.assertIn("CONTEXT_JSON is the complete and only review evidence", gemini_prompt)
        self.assertIn("supplied paths are labels, not readable workspace paths", gemini_prompt)
        self.assertIn(
            "the verifier must use internal reasoning only and return the requested JSON directly",
            gemini_prompt,
        )
        self.assertIn("attempted native tool or tool-like action", gemini_prompt)
        self.assertIn(
            "irreversibly invalidates the proposal-only session and cannot obtain human approval",
            gemini_prompt,
        )
        self.assertIn(
            "The rule must apply even if the model believes more context is desirable",
            gemini_prompt,
        )
        for action in (
            "view_file",
            "run_command",
            "pwd",
            "ls",
            "browser",
            "network",
            "delegation",
            "file access",
        ):
            self.assertIn(action, gemini_prompt)

    def test_category_disposition_state_mappings(self):
        valid_triples = {
            ("CONTINUE", "NONE", "NONE"),
            ("COMPLETE", "NONE", "NONE"),
            ("BLOCKED", "RESCOPE", "INTERNAL_REJECTION"),
            ("BLOCKED", "HALT", "EXTERNAL_GATE"),
        }
        all_states = ("CONTINUE", "COMPLETE", "BLOCKED")
        all_dispositions = ("NONE", "HALT", "RESCOPE")
        all_categories = ("NONE", "INTERNAL_REJECTION", "EXTERNAL_GATE")

        for state in all_states:
            for disposition in all_dispositions:
                for category in all_categories:
                    triple = (state, disposition, category)
                    with self.subTest(triple=triple):
                        if triple in valid_triples:
                            if state == "CONTINUE":
                                res = self.result(
                                    state=state, disposition=disposition, category=category,
                                    commit="b" * 40, next_action="Next step.", blockers=[],
                                )
                            elif state == "COMPLETE":
                                res = self.result(
                                    state=state, disposition=disposition, category=category,
                                    commit=None, next_action=None, blockers=[],
                                )
                            elif disposition == "RESCOPE":
                                res = self.result(
                                    state=state, disposition=disposition, category=category,
                                    commit=None, next_action="Next step.", blockers=["Internal failure."],
                                )
                            else:
                                res = self.result(
                                    state=state, disposition=disposition, category=category,
                                    commit=None, next_action=None, blockers=["External gate."],
                                )
                            validated = autopilot.validate_result(res)
                            self.assertEqual(validated["state"], state)
                            self.assertEqual(validated["disposition"], disposition)
                            self.assertEqual(validated["category"], category)
                        else:
                            res = self.result(
                                state=state, disposition=disposition, category=category,
                                commit="b" * 40 if state == "CONTINUE" else None,
                                next_action="Next step." if (state == "CONTINUE" or disposition == "RESCOPE") else None,
                                blockers=[] if state != "BLOCKED" else ["Blocker."],
                            )
                            with self.assertRaises(autopilot.AutopilotError):
                                autopilot.validate_result(res)

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

    def test_prompt_for_cycle_carries_recovery_context(self):
        before = {"head": "a" * 40, "protected_refs": {}}
        recovery = {
            "attempt": 1,
            "prior_unit_rejected": True,
            "next_action": "Rescope bounded files.",
            "blockers": ["Scope was too broad."],
        }
        with patch.object(autopilot, "bounded_file_text", return_value="PROMPT_TEMPLATE"):
            prompt = autopilot.prompt_for_cycle(self.root, 2, before, recovery=recovery)
        self.assertIn("SUPERVISOR_CONTEXT_JSON:\n", prompt)
        context_json = prompt.split("SUPERVISOR_CONTEXT_JSON:\n", 1)[1]
        context = json.loads(context_json)
        self.assertEqual(context["cycle"], 2)
        self.assertEqual(context["recovery"], recovery)

    def test_rescope_recovery_handoff_into_next_cycle(self):
        rescope_result = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Narrow to single module.", blockers=["Boundary exceeded."],
        )
        rescope_result.update({"head_before": "a" * 40, "head_after": "a" * 40, "head_changed": False})
        commit_result = self.result(
            state="CONTINUE", disposition="NONE", commit="b" * 40,
            next_action="Continue next step.", blockers=[],
        )
        commit_result.update({"head_before": "a" * 40, "head_after": "b" * 40, "head_changed": True})

        calls = []
        def fake_run_cycle(root, codex, s_root, cycle, timeout, recovery=None):
            calls.append((cycle, recovery))
            if cycle == 1:
                return self.root / "run_001", rescope_result
            return self.root / "run_002", commit_result

        with patch.object(autopilot, "git", side_effect=["a" * 40, "a" * 40, "b" * 40]), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=fake_run_cycle), \
                patch.object(autopilot, "write_supervisor_state") as publish, \
                patch.object(autopilot, "prune_runs"):
            status = autopilot.run_loop(self.root, self.root / "codex", max_cycles=2, timeout=300, once=False)

        self.assertEqual(status, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], (1, None))
        self.assertEqual(calls[1][0], 2)
        self.assertTrue(calls[1][1]["rejected"])
        self.assertEqual(calls[1][1]["attempt"], 1)
        self.assertEqual(calls[1][1]["next_action"], "Narrow to single module.")
        self.assertEqual(calls[1][1]["blockers"], ["Boundary exceeded."])

        published_states = [call.args[1] for call in publish.call_args_list]
        self.assertIn("RESCOPE", published_states)
        self.assertIn("CONTINUE", published_states)

    def test_bounded_exhaustion_with_no_fourth_run(self):
        rescope_result = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Keep narrowing.", blockers=["Still blocked."],
        )
        rescope_result.update({"head_before": "a" * 40, "head_after": "a" * 40, "head_changed": False})

        cycle_runs = []
        def fake_run_cycle(root, codex, s_root, cycle, timeout, recovery=None):
            cycle_runs.append((cycle, recovery))
            return self.root / f"run_{cycle:03d}", rescope_result

        with patch.object(autopilot, "git", return_value="a" * 40), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=fake_run_cycle), \
                patch.object(autopilot, "write_supervisor_state") as publish, \
                patch.object(autopilot, "prune_runs"):
            status = autopilot.run_loop(self.root, self.root / "codex", max_cycles=10, timeout=300, once=False)

        self.assertEqual(status, 2)
        self.assertEqual(len(cycle_runs), 3, "must execute exactly 3 runs with no fourth run")
        self.assertEqual(cycle_runs[0][0], 1)
        self.assertIsNone(cycle_runs[0][1])
        self.assertEqual(cycle_runs[1][0], 2)
        self.assertEqual(cycle_runs[1][1]["attempt"], 1)
        self.assertEqual(cycle_runs[2][0], 3)
        self.assertEqual(cycle_runs[2][1]["attempt"], 2)

        last_publish = publish.call_args_list[-1]
        self.assertEqual(last_publish.args[1], "BLOCKED")
        self.assertEqual(last_publish.kwargs["cycle"], 3)

    def test_rescope_counter_resets_after_real_commit(self):
        rescope_result = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Rescope.", blockers=["Need rescope."],
        )
        rescope_result.update({"head_before": "a" * 40, "head_after": "a" * 40, "head_changed": False})
        commit_result = self.result(
            state="CONTINUE", disposition="NONE", commit="b" * 40,
            next_action="Continue.", blockers=[],
        )
        commit_result.update({"head_before": "a" * 40, "head_after": "b" * 40, "head_changed": True})

        cycle_runs = []
        def fake_run_cycle(root, codex, s_root, cycle, timeout, recovery=None):
            cycle_runs.append((cycle, recovery))
            if cycle == 1:
                return self.root / "run_1", rescope_result
            if cycle == 2:
                return self.root / "run_2", commit_result
            return self.root / f"run_{cycle}", rescope_result

        with patch.object(autopilot, "git", return_value="a" * 40), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=fake_run_cycle), \
                patch.object(autopilot, "write_supervisor_state"), \
                patch.object(autopilot, "prune_runs"):
            status = autopilot.run_loop(self.root, self.root / "codex", max_cycles=10, timeout=300, once=False)

        self.assertEqual(status, 2)
        self.assertEqual(len(cycle_runs), 5)
        self.assertIsNotNone(cycle_runs[1][1], "Cycle 2 had recovery context from cycle 1")
        self.assertIsNone(cycle_runs[2][1], "Cycle 3 had recovery context cleared after cycle 2 commit")
        self.assertEqual(cycle_runs[3][1]["attempt"], 1)
        self.assertEqual(cycle_runs[4][1]["attempt"], 2)

    def test_immediate_halt_for_halt_and_generic_blocked(self):
        halt_result = self.result(
            state="BLOCKED", disposition="HALT", commit=None,
            next_action="Inspect failure.", blockers=["Definitive roadblock."],
        )
        halt_result.update({"head_before": "a" * 40, "head_after": "a" * 40, "head_changed": False})

        run_calls = []
        def fake_run_cycle(root, codex, s_root, cycle, timeout, recovery=None):
            run_calls.append(cycle)
            return self.root / "run_001", halt_result

        with patch.object(autopilot, "git", return_value="a" * 40), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=fake_run_cycle), \
                patch.object(autopilot, "write_supervisor_state") as publish, \
                patch.object(autopilot, "prune_runs"):
            status = autopilot.run_loop(self.root, self.root / "codex", max_cycles=5, timeout=300, once=False)

        self.assertEqual(status, 2)
        self.assertEqual(len(run_calls), 1, "HALT must stop immediately with no further cycles")
        published_states = [call.args[1] for call in publish.call_args_list]
        self.assertEqual(published_states, ["RUNNING", "BLOCKED"])

    def test_once_flag_exits_immediately_on_rescope(self):
        rescope_result = self.result(
            state="BLOCKED", disposition="RESCOPE", commit=None,
            next_action="Rescope next.", blockers=["Gate."],
        )
        rescope_result.update({"head_before": "a" * 40, "head_after": "a" * 40, "head_changed": False})

        run_calls = []
        def fake_run_cycle(root, codex, s_root, cycle, timeout, recovery=None):
            run_calls.append(cycle)
            return self.root / "run_001", rescope_result

        with patch.object(autopilot, "git", return_value="a" * 40), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=fake_run_cycle), \
                patch.object(autopilot, "write_supervisor_state") as publish, \
                patch.object(autopilot, "prune_runs"):
            status = autopilot.run_loop(self.root, self.root / "codex", max_cycles=5, timeout=300, once=True)

        self.assertEqual(status, 2)
        self.assertEqual(len(run_calls), 1, "--once must execute only one cycle")
        published_states = [call.args[1] for call in publish.call_args_list]
        self.assertEqual(published_states, ["RUNNING", "RESCOPE"])


if __name__ == "__main__":
    unittest.main()
