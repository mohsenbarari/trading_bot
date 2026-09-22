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
            "goal": f"Change one exact file for {self.assignment_scope().assignment_id}.",
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
            "model": proposal_runner.WORKER_MODEL, "head": source_head,
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

    def assignment_scope(self):
        return autopilot.AssignmentScope(
            assignment_id="WA-TEST-SCOPE-001",
            stage_id="P1-TEST",
            allowed_paths=("change.py",),
            forbidden_paths=("blocked/**",),
            expires_at="2099-01-01T00:00:00Z",
            sha256="a" * 64,
        )

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
            self.result(
                state="BLOCKED", disposition="HALT", commit=None, next_action=None,
                blockers=["Gate."], gemini_worker_run_id="worker-001",
                gemini_reviewer_run_id="reviewer-001", proposal_sha256="c" * 64,
            ),
        ]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(autopilot.AutopilotError):
                autopilot.validate_result(result)
        with self.assertRaisesRegex(
            autopilot.AutopilotError,
            "HALT requires null commit and Gemini acceptance evidence",
        ):
            autopilot.validate_result(self.result(
                state="BLOCKED", disposition="HALT", commit="b" * 40, next_action=None, blockers=["Gate."],
            ))

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
            "assignment_scope": self.assignment_scope(),
        }
        after = {
            "head": "b" * 40,
            "protected_refs": before["protected_refs"],
            "control_blobs": before["control_blobs"],
            "assignment_scope": before["assignment_scope"],
        }
        proposal = {"changes": []}
        with patch.object(autopilot, "validate_repository", return_value=after), \
                patch.object(autopilot, "git", return_value="") as git, \
                patch.object(autopilot, "validate_scoped_gemini_chain", return_value=proposal) as scoped_chain, \
                patch.object(autopilot, "validate_scoped_committed_proposal") as scoped_committed:
            checked = autopilot.validate_transition(self.root, before, self.result())
        self.assertTrue(checked["head_changed"])
        scoped_chain.assert_called_once_with(
            self.root, "a" * 40, self.result(), before["assignment_scope"],
        )
        scoped_committed.assert_called_once_with(
            self.root, "a" * 40, "b" * 40, proposal, before["assignment_scope"],
        )
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
        with patch.object(autopilot, "validate_repository", return_value=changed_control), \
                patch.object(autopilot, "git", return_value=""), \
                patch.object(autopilot, "validate_scoped_gemini_chain", return_value=proposal), \
                patch.object(autopilot, "validate_scoped_committed_proposal"):
            with self.assertRaisesRegex(autopilot.AutopilotError, "control"):
                autopilot.validate_transition(self.root, before, self.result())

        control_proposal = {"changes": [{"path": "runner"}]}
        with patch.object(autopilot, "validate_repository", return_value=changed_control), \
                patch.object(autopilot, "git", return_value=""), \
                patch.object(autopilot, "validate_scoped_gemini_chain", return_value=control_proposal), \
                patch.object(autopilot, "validate_scoped_committed_proposal"):
            checked = autopilot.validate_transition(self.root, before, self.result())
        self.assertTrue(checked["head_changed"])

        mutated_scope = autopilot.AssignmentScope(
            assignment_id=before["assignment_scope"].assignment_id,
            stage_id=before["assignment_scope"].stage_id,
            allowed_paths=before["assignment_scope"].allowed_paths,
            forbidden_paths=before["assignment_scope"].forbidden_paths,
            expires_at=before["assignment_scope"].expires_at,
            sha256="f" * 64,
        )
        changed_scope = dict(after, assignment_scope=mutated_scope)
        with patch.object(autopilot, "validate_repository", return_value=changed_scope), \
                patch.object(autopilot, "validate_scoped_gemini_chain") as evidence_mock:
            with self.assertRaisesRegex(autopilot.AutopilotError, "assignment scope changed"):
                autopilot.validate_transition(self.root, before, self.result())
            evidence_mock.assert_not_called()

    def test_gemini_chain_requires_exact_proposal_and_fresh_reviewer(self):
        result, worker_result, reviewer_dir = self.gemini_chain()
        self.assertEqual(
            autopilot.validate_gemini_chain(self.root, 'a' * 40, result),
            worker_result,
        )
        reviewer_status = json.loads((reviewer_dir / "status.json").read_text())
        reviewer_status["conversation_id"] = "worker-conversation"
        proposal_runner.write_json(reviewer_dir / "status.json", reviewer_status)
        with self.assertRaisesRegex(autopilot.AutopilotError, "independent fresh"):
            autopilot.validate_gemini_chain(self.root, 'a' * 40, result)

    def test_scoped_gemini_chain_enforces_assignment_scope(self):
        result, worker_result, _ = self.gemini_chain()
        self.assertEqual(
            autopilot.validate_scoped_gemini_chain(self.root, 'a' * 40, result, self.assignment_scope()),
            worker_result,
        )
        scope = self.assignment_scope()
        other_scope = autopilot.AssignmentScope(
            assignment_id="WA-OTHER-SCOPE-001",
            stage_id=scope.stage_id,
            allowed_paths=scope.allowed_paths,
            forbidden_paths=scope.forbidden_paths,
            expires_at=scope.expires_at,
            sha256=scope.sha256,
        )
        with self.assertRaisesRegex(autopilot.AutopilotError, "assignment scope"):
            autopilot.validate_scoped_gemini_chain(self.root, 'a' * 40, result, other_scope)
        path_scope = autopilot.AssignmentScope(
            assignment_id=scope.assignment_id,
            stage_id=scope.stage_id,
            allowed_paths=("other.py",),
            forbidden_paths=scope.forbidden_paths,
            expires_at=scope.expires_at,
            sha256=scope.sha256,
        )
        with self.assertRaisesRegex(autopilot.AutopilotError, "assignment scope"):
            autopilot.validate_scoped_gemini_chain(self.root, 'a' * 40, result, path_scope)

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
        autopilot.validate_scoped_committed_proposal(
            self.root, before, after, proposal, self.assignment_scope(),
        )
        scope = self.assignment_scope()
        path_scope = autopilot.AssignmentScope(
            assignment_id=scope.assignment_id,
            stage_id=scope.stage_id,
            allowed_paths=("other.py",),
            forbidden_paths=scope.forbidden_paths,
            expires_at=scope.expires_at,
            sha256=scope.sha256,
        )
        with self.assertRaisesRegex(autopilot.AutopilotError, "assignment scope"):
            autopilot.validate_scoped_committed_proposal(
                self.root, before, after, proposal, path_scope,
            )
        out_of_scope_proposal = {"changes": [{"path": "blocked/path.py", "content": "arbitrary\n"}]}
        with self.assertRaisesRegex(autopilot.AutopilotError, "assignment scope"):
            autopilot.validate_scoped_committed_proposal(
                self.root, before, after, out_of_scope_proposal, self.assignment_scope(),
            )
        proposal["changes"][0]["content"] = "CODEX_EDIT = True\n"
        with self.assertRaisesRegex(autopilot.AutopilotError, "bytes differ"):
            autopilot.validate_committed_proposal(self.root, before, after, proposal)

    def test_codex_command_is_pinned_bounded_and_not_bypass_mode(self):
        command = autopilot.codex_command(
            Path("/usr/bin/codex"), self.root, self.root / "run", "prompt",
        )
        self.assertIn(autopilot.MODEL, command)
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

    def test_coordinator_prompt_enforces_narrow_git_escalation_and_forbidden_operations(self):
        prompt = (autopilot.ROOT / autopilot.PROMPT_REL).read_text()
        self.assertIn(
            "final Git metadata escalation only for narrowly scoped git add -- <exact accepted proposal paths>",
            prompt,
        )
        self.assertIn("one non-interactive git commit", prompt)
        self.assertIn("Require Git staging to name every and only accepted proposal path", prompt)
        self.assertIn(
            "read-only worktree Git metadata failure must be retried once through that narrow command-tool require_escalated path",
            prompt,
        )
        self.assertIn("single-parent commit on refactor/two-site-architecture", prompt)
        self.assertIn("Supervisor exact-proposal validation", prompt)

        prohibited = [
            "git add -A",
            "git add .",
            "globs",
            "unrelated paths",
            "amend",
            "reset",
            "checkout",
            "merge",
            "rebase",
            "push",
            "fetch",
            "tag",
            "branch mutation",
            "hooks bypass",
            "signing-policy bypass",
        ]
        for operation in prohibited:
            with self.subTest(operation=operation):
                self.assertIn(operation, prompt)

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
        cli.write_text(f"#!/usr/bin/env python3\nprint('{autopilot.EXPECTED_CODEX_CLI_VERSION}')\n")
        cli.chmod(0o755)
        digest = autopilot.file_sha256(cli)
        with patch.object(autopilot, "EXPECTED_CODEX_CLI_SHA256", digest), \
                patch.object(autopilot, "codex_runtime_identity", return_value={}):
            identity = autopilot.codex_identity(cli, self.root)
        self.assertEqual(identity["version"], autopilot.EXPECTED_CODEX_CLI_VERSION)
        self.assertEqual(identity["sha256"], digest)
        self.assertEqual(identity["model"], autopilot.MODEL)
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
        publish.assert_called_once()
        args, kwargs = publish.call_args
        self.assertEqual(args, (state_root, "RUNNING"))
        self.assertEqual(kwargs["cycle"], 1)
        self.assertEqual(kwargs["head"], "a" * 40)
        self.assertEqual(kwargs["phase"], "PREPARING")
        self.assertIsInstance(kwargs["phase_started_at"], float)
        self.assertEqual(kwargs["heartbeat_at"], kwargs["phase_started_at"])
        self.assertEqual(
            kwargs["summary"],
            "Executing Gemini Worker -> Gemini Reviewer -> Codex final gate.",
        )
        self.assertEqual(kwargs["blockers"], [])

    def test_run_loop_contains_unexpected_cycle_errors(self):
        state_root = self.root / ".local/two-site-refactor/autopilot"
        with patch.object(autopilot, "git", side_effect=["a" * 40, "a" * 40, ""]), \
                patch.object(autopilot, "ref_snapshot", return_value={}), \
                patch.object(autopilot, "run_cycle", side_effect=RuntimeError("unexpected cycle error")), \
                patch.object(autopilot, "write_supervisor_state") as publish:
            status = autopilot.run_loop(self.root, self.root / "codex", 1, 300, True)
        self.assertEqual(status, 2)
        self.assertEqual(publish.call_args_list[-1].args[1], "RETRYABLE_FAILURE")
        self.assertIn("unexpected cycle error", publish.call_args_list[-1].kwargs["error"])

    def test_repository_gate_requires_tracked_controls_clean_and_correct_branch(self):
        for relative in autopilot.CONTROL_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (autopilot.ROOT / autopilot.SCHEMA_REL).read_text()
                if relative == autopilot.SCHEMA_REL
                else (autopilot.ROOT / autopilot.GEMINI_RUNTIME_CONTRACT_REL).read_text()
                if relative == autopilot.GEMINI_RUNTIME_CONTRACT_REL else "control\n"
            )
        for relative in proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((proposal_runner.ROOT / relative).read_text())
        (self.root / "docs/refactor/two-server").mkdir(parents=True, exist_ok=True)
        (self.root / "docs/refactor/two-server/execution-order.yaml").write_text(
            "stages:\n"
            "  P1-TEST:\n"
            "    state: IN_PROGRESS\n"
        )
        (self.root / ".local/two-site-refactor/assignments").mkdir(parents=True, exist_ok=True)
        (self.root / ".local/two-site-refactor/worktrees.yaml").write_text(
            "integration:\n"
            f"  path: {self.root}\n"
            f"  branch: {autopilot.BRANCH}\n"
            "  active_assignment_id: WA-TEST-SCOPE-001\n"
        )
        (self.root / ".local/two-site-refactor/assignments/WA-TEST-SCOPE-001.md").write_text(
            "# WA-TEST-SCOPE-001\n\n"
            "```yaml\n"
            "id: WA-TEST-SCOPE-001\n"
            "assignment_id: WA-TEST-SCOPE-001\n"
            "status: READY\n"
            "role: WORKER\n"
            "stage_id: P1-TEST\n"
            f"integration_branch: {autopilot.BRANCH}\n"
            "external_access: NONE\n"
            "expires_at: '2099-01-01T00:00:00Z'\n"
            "allowed_paths:\n"
            "  - tests/test_two_site_codex_autopilot.py\n"
            "forbidden_paths:\n"
            "  - scripts/two_site_assignment_scope.py\n"
            "```\n"
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
        if "assignment_scope" in state:
            scope = state["assignment_scope"]
            self.assertEqual(
                scope.assignment_id if hasattr(scope, "assignment_id") else scope["assignment_id"],
                "WA-TEST-SCOPE-001",
            )
            self.assertEqual(
                scope.stage_id if hasattr(scope, "stage_id") else scope["stage_id"],
                "P1-TEST",
            )
            self.assertEqual(
                tuple(scope.allowed_paths if hasattr(scope, "allowed_paths") else scope["allowed_paths"]),
                ("tests/test_two_site_codex_autopilot.py",),
            )
            self.assertEqual(
                tuple(scope.forbidden_paths if hasattr(scope, "forbidden_paths") else scope["forbidden_paths"]),
                ("scripts/two_site_assignment_scope.py",),
            )
        (self.root / "untracked.txt").write_text("dirty\n")
        with self.assertRaisesRegex(autopilot.AutopilotError, "not clean"):
            autopilot.validate_repository(self.root)

    def test_repository_gate_rejects_gemini_runtime_document_drift(self):
        contract = self.root / autopilot.GEMINI_RUNTIME_CONTRACT_REL
        contract.parent.mkdir(parents=True, exist_ok=True)
        contract.write_text((proposal_runner.ROOT / proposal_runner.GEMINI_RUNTIME_CONTRACT_REL).read_text())
        reference = self.root / proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS[0]
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(
            (proposal_runner.ROOT / proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS[0])
            .read_text()
            .replace("gemini-3.8-flash-high", "gemini-3.8-flash-medium")
        )
        for relative in proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS[1:]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((proposal_runner.ROOT / relative).read_text())
        with self.assertRaisesRegex(proposal_runner.ProposalError, "drift"):
            autopilot.validate_runtime_contract(self.root)

    def test_repository_gate_rejects_provider_incompatible_schema_before_launch(self):
        for relative in autopilot.CONTROL_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                (autopilot.ROOT / autopilot.SCHEMA_REL).read_text()
                if relative == autopilot.SCHEMA_REL
                else (autopilot.ROOT / autopilot.GEMINI_RUNTIME_CONTRACT_REL).read_text()
                if relative == autopilot.GEMINI_RUNTIME_CONTRACT_REL else "control\n"
            )
        for relative in proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((proposal_runner.ROOT / relative).read_text())
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
                        if relative == autopilot.SCHEMA_REL
                        else (autopilot.ROOT / autopilot.GEMINI_RUNTIME_CONTRACT_REL).read_text()
                        if relative == autopilot.GEMINI_RUNTIME_CONTRACT_REL else "control\n"
                    )
            for relative in proposal_runner.GEMINI_RUNTIME_REFERENCE_PATHS:
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text((proposal_runner.ROOT / relative).read_text())
            subprocess.check_call(["git", "init", "-q"], cwd=self.root)
            subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=self.root)
            subprocess.check_call(["git", "config", "user.name", "Test"], cwd=self.root)
            subprocess.check_call(["git", "add", "."], cwd=self.root)
            subprocess.check_call(["git", "commit", "-qm", "hardlinked control"], cwd=self.root)
            subprocess.check_call(["git", "branch", "-M", autopilot.BRANCH], cwd=self.root)
            with self.assertRaisesRegex(autopilot.AutopilotError, "unsafe"):
                autopilot.validate_repository(self.root)

    def test_prompt_for_cycle_carries_recovery_context(self):
        before = {
            "head": "a" * 40,
            "protected_refs": {},
            "assignment_scope": self.assignment_scope(),
        }
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
        self.assertIs(context["startup_validation"], True)

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
        self.assertTrue(cycle_runs[2][1]["defer_prior_unit"])
        self.assertEqual(cycle_runs[2][1]["deferred_unit"], {
            "next_action": "Keep narrowing.",
            "blockers": ["Still blocked."],
        })

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

    def test_run_cycle_stops_heartbeat_and_joins_before_normal_return(self):
        test_case = self
        captured_threads = []
        events = []
        caller_observed_return = False

        class FakeHeartbeatThread:
            def __init__(self, target=None, args=(), **kwargs):
                self.target = target
                self.args = args
                self.name = kwargs.get("name")
                self.daemon = kwargs.get("daemon")
                self.stop_event = args[0] if args else None
                self.join_called = 0
                captured_threads.append(self)

            def start(self):
                pass

            def join(self, timeout=None):
                self.join_called += 1
                test_case.assertFalse(caller_observed_return)
                events.append(("join_start", bool(self.stop_event and self.stop_event.is_set())))
                events.append("join_done")

        class FakeProcess:
            pid = 12345
            returncode = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        codex = self.root / "codex"
        codex.write_text("")
        state_root = self.root / "state"
        (state_root / "runs").mkdir(parents=True)

        before = {
            "head": "a" * 40,
            "protected_refs": {},
            "assignment_scope": self.assignment_scope(),
        }
        raw_receipt = self.result()
        expected_result = dict(raw_receipt, head_before="a" * 40, head_after="b" * 40, head_changed=True)

        def fake_capture(_proc, _stdin, stream_path, _stderr, _timeout, max_output=None):
            autopilot.write_json(stream_path.parent / "result.json", raw_receipt)

        with patch.object(autopilot, "validate_repository", return_value=before), \
                patch.object(autopilot, "prompt_for_cycle", return_value="fake prompt"), \
                patch.object(autopilot, "codex_identity", return_value={"sha256": "s" * 64, "runtime": {}}), \
                patch.object(autopilot, "codex_command", return_value=["fake-codex"]), \
                patch.object(autopilot.subprocess, "Popen", return_value=FakeProcess()), \
                patch.object(autopilot, "write_supervisor_state"), \
                patch.object(autopilot.threading, "Thread", side_effect=FakeHeartbeatThread), \
                patch.object(autopilot, "capture_bounded_process", side_effect=fake_capture), \
                patch.object(autopilot, "file_sha256", return_value="s" * 64), \
                patch.object(autopilot, "codex_runtime_identity", return_value={}), \
                patch.object(autopilot, "validate_transition", return_value=expected_result):
            run_dir, result = autopilot.run_cycle(self.root, codex, state_root, 1, 30)
            caller_observed_return = True
            events.append("caller_returned")

        self.assertEqual(len(captured_threads), 1)
        thread = captured_threads[0]
        self.assertIs(thread.target, autopilot._heartbeat_running_cycle)
        self.assertEqual(thread.name, "two-site-supervisor-heartbeat")
        self.assertTrue(thread.daemon)
        self.assertIsInstance(thread.stop_event, autopilot.threading.Event)
        self.assertTrue(thread.stop_event.is_set())
        self.assertEqual(thread.join_called, 1)
        self.assertEqual(
            events,
            [("join_start", True), "join_done", "caller_returned"],
        )
        self.assertTrue(caller_observed_return)
        self.assertIsNone(autopilot._ACTIVE_PROCESS)
        self.assertEqual(result["state"], "CONTINUE")
        self.assertTrue(run_dir.is_dir())

    def test_run_cycle_preserves_primary_baseexception_when_heartbeat_join_raises(self):
        test_case = self
        captured_threads = []
        events = []

        class DedicatedPrimaryBaseException(BaseException):
            pass

        class DedicatedHeartbeatJoinBaseException(BaseException):
            pass

        primary_error = DedicatedPrimaryBaseException("primary base exception failure")
        join_error = DedicatedHeartbeatJoinBaseException("heartbeat join cleanup failure")

        class FakeHeartbeatThread:
            def __init__(self, target=None, args=(), **kwargs):
                self.target = target
                self.args = args
                self.name = kwargs.get("name")
                self.daemon = kwargs.get("daemon")
                self.stop_event = args[0] if args else None
                self.join_called = 0
                captured_threads.append(self)

            def start(self):
                pass

            def join(self, timeout=None):
                self.join_called += 1
                stop_set_before_join = bool(self.stop_event and self.stop_event.is_set())
                events.append(("join_start", stop_set_before_join))
                test_case.assertTrue(stop_set_before_join)
                test_case.assertIsNotNone(autopilot._ACTIVE_PROCESS)
                raise join_error

        class FakeProcess:
            pid = 12345
            returncode = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        codex = self.root / "codex"
        codex.write_text("")
        state_root = self.root / "state"
        (state_root / "runs").mkdir(parents=True)

        before = {
            "head": "a" * 40,
            "protected_refs": {},
            "assignment_scope": self.assignment_scope(),
        }

        def fake_capture(_proc, _stdin, _stream_path, _stderr, _timeout, max_output=None):
            raise primary_error

        with patch.object(autopilot, "validate_repository", return_value=before), \
                patch.object(autopilot, "prompt_for_cycle", return_value="fake prompt"), \
                patch.object(autopilot, "codex_identity", return_value={"sha256": "s" * 64, "runtime": {}}), \
                patch.object(autopilot, "codex_command", return_value=["fake-codex"]), \
                patch.object(autopilot.subprocess, "Popen", return_value=FakeProcess()), \
                patch.object(autopilot, "write_supervisor_state"), \
                patch.object(autopilot.threading, "Thread", side_effect=FakeHeartbeatThread), \
                patch.object(autopilot, "capture_bounded_process", side_effect=fake_capture):
            with self.assertRaises(BaseException) as ctx:
                autopilot.run_cycle(self.root, codex, state_root, 1, 30)

        self.assertIs(ctx.exception, primary_error)
        self.assertEqual(len(captured_threads), 1)
        thread = captured_threads[0]
        self.assertEqual(thread.join_called, 1)
        self.assertTrue(thread.stop_event.is_set())
        self.assertEqual(events, [("join_start", True)])
        self.assertIsNone(autopilot._ACTIVE_PROCESS)

        runs = list((state_root / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        status_path = runs[0] / "status.json"
        self.assertTrue(status_path.is_file())
        status = json.loads(status_path.read_text())
        self.assertEqual(status["state"], "FAILED")
        self.assertIn("DedicatedPrimaryBaseException", status["error"])
        self.assertIn("primary base exception failure", status["error"])

    def test_run_cycle_propagates_heartbeat_join_failure_without_primary_exception(self):
        test_case = self
        captured_threads = []
        events = []

        class DedicatedHeartbeatJoinBaseException(BaseException):
            pass

        join_error = DedicatedHeartbeatJoinBaseException("heartbeat join cleanup failure without primary")

        class FakeHeartbeatThread:
            def __init__(self, target=None, args=(), **kwargs):
                self.target = target
                self.args = args
                self.name = kwargs.get("name")
                self.daemon = kwargs.get("daemon")
                self.stop_event = args[0] if args else None
                self.join_called = 0
                captured_threads.append(self)

            def start(self):
                pass

            def join(self, timeout=None):
                self.join_called += 1
                stop_set_before_join = bool(self.stop_event and self.stop_event.is_set())
                events.append(("join_start", stop_set_before_join))
                test_case.assertTrue(stop_set_before_join)
                test_case.assertIsNotNone(autopilot._ACTIVE_PROCESS)
                raise join_error

        class FakeProcess:
            pid = 12345
            returncode = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        codex = self.root / "codex"
        codex.write_text("")
        state_root = self.root / "state"
        (state_root / "runs").mkdir(parents=True)

        before = {
            "head": "a" * 40,
            "protected_refs": {},
            "assignment_scope": self.assignment_scope(),
        }
        raw_receipt = self.result()
        expected_result = dict(raw_receipt, head_before="a" * 40, head_after="b" * 40, head_changed=True)

        def fake_capture(_proc, _stdin, stream_path, _stderr, _timeout, max_output=None):
            autopilot.write_json(stream_path.parent / "result.json", raw_receipt)

        with patch.object(autopilot, "validate_repository", return_value=before), \
                patch.object(autopilot, "prompt_for_cycle", return_value="fake prompt"), \
                patch.object(autopilot, "codex_identity", return_value={"sha256": "s" * 64, "runtime": {}}), \
                patch.object(autopilot, "codex_command", return_value=["fake-codex"]), \
                patch.object(autopilot.subprocess, "Popen", return_value=FakeProcess()), \
                patch.object(autopilot, "write_supervisor_state"), \
                patch.object(autopilot.threading, "Thread", side_effect=FakeHeartbeatThread), \
                patch.object(autopilot, "capture_bounded_process", side_effect=fake_capture), \
                patch.object(autopilot, "file_sha256", return_value="s" * 64), \
                patch.object(autopilot, "codex_runtime_identity", return_value={}), \
                patch.object(autopilot, "validate_transition", return_value=expected_result):
            with self.assertRaises(BaseException) as ctx:
                autopilot.run_cycle(self.root, codex, state_root, 1, 30)

        self.assertIs(ctx.exception, join_error)
        self.assertEqual(len(captured_threads), 1)
        thread = captured_threads[0]
        self.assertEqual(thread.join_called, 1)
        self.assertTrue(thread.stop_event.is_set())
        self.assertEqual(events, [("join_start", True)])
        self.assertIsNone(autopilot._ACTIVE_PROCESS)


if __name__ == "__main__":
    unittest.main()
