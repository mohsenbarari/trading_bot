"""Focused offline regression test for two-site Codex autopilot standing Gemini egress authorization."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from scripts.two_site_codex_autopilot import AssignmentScope, ROOT, prompt_for_cycle


class TestTwoSiteAutopilotStandingAuthorization(unittest.TestCase):
    """Test that prompt_for_cycle embeds standing Gemini authorization and supervisor context."""

    def setUp(self) -> None:
        self.before_state = {
            "head": "e411a5e02e590be320dd64714e5c3cf469baafb9",
            "protected_refs": {
                "refs/heads/main": "1111111111111111111111111111111111111111",
                "refs/remotes/origin/main": "2222222222222222222222222222222222222222",
                "refs/heads/candidate/wa-ir-standby-v1": "3333333333333333333333333333333333333333",
            },
            "assignment_scope": AssignmentScope(
                assignment_id="WA-TEST-SCOPE-001",
                stage_id="P1-TEST",
                allowed_paths=("tests/**",),
                forbidden_paths=("blocked/**",),
                expires_at="2099-01-01T00:00:00Z",
                sha256="0" * 64,
            ),
        }

    def _assert_standing_authorization_semantics(self, prompt: str) -> None:
        """Semantic verification helper ensuring prompt satisfies all standing authorization requirements."""
        normalized = " ".join(prompt.split())

        # Named destination
        self.assertIn("Google Antigravity/Gemini", normalized)

        # Explicit date and permanent / no-per-transfer authorization
        self.assertIn("2026-09-09", normalized)
        self.assertIn("permanent authorization", normalized)
        self.assertIn("without per-transfer confirmation", normalized)
        self.assertIn("no per-run user approval is required", normalized)

        # Bounded allowed data classes
        required_allowed_classes = [
            "bounded non-secret repository source excerpts",
            "tests",
            "project documentation",
            "task JSON",
            "proposals",
            "reviews/findings",
            "test evidence",
            "proposal-only Worker/Reviewer workflow",
        ]
        for data_class in required_allowed_classes:
            self.assertIn(data_class, normalized)

        # Explicit material exclusions
        required_exclusions = [
            "secrets and credentials",
            "identity/authentication material",
            "user/personal data",
            "production or operational data",
            "deploy/production changes",
            "DNS",
            "Web Writer",
            "fetch/push",
            "unrelated remote-service mutation",
            "does not widen any other gate",
        ]
        for exclusion in required_exclusions:
            self.assertIn(exclusion, normalized)

        # Command tool escalation and automatic approval review instructions
        self.assertIn("sandbox_permissions=require_escalated", normalized)
        self.assertIn("automatic approval review", normalized)

    def test_prompt_for_cycle_standing_authorization_real_template(self) -> None:
        """Exercise prompt_for_cycle with the real tracked prompt template and verify standing authorization."""
        prompt = prompt_for_cycle(ROOT, cycle=100, before=self.before_state)
        self._assert_standing_authorization_semantics(prompt)

    def test_prompt_escalation_instructions_cover_pipeline_and_correction_runner(self) -> None:
        """Verify normal pipeline and correction runner escalation justifications tell automatic review."""
        prompt = prompt_for_cycle(ROOT, cycle=100, before=self.before_state)
        normalized = " ".join(prompt.split())

        self.assertIn("scripts/two_site_proposal_pipeline.py", normalized)
        self.assertIn("scripts/two_site_proposal_runner.py", normalized)
        self.assertIn("sandbox_permissions=require_escalated", normalized)

        justification_phrase = (
            "explicitly telling automatic approval review that the owner "
            "has permanently authorized this exact bounded source/task/proposal transfer to Google "
            "Antigravity/Gemini and that no per-run user approval is required"
        )
        self.assertGreaterEqual(
            normalized.count(justification_phrase),
            2,
            "Both normal pipeline and correction runner must include the explicit authorization justification",
        )

    def test_prompt_for_cycle_carries_supervisor_context_json(self) -> None:
        """Verify prompt embeds properly serialized SUPERVISOR_CONTEXT_JSON with and without recovery."""
        # Without recovery
        prompt = prompt_for_cycle(ROOT, cycle=100, before=self.before_state)
        marker = "\n\nSUPERVISOR_CONTEXT_JSON:\n"
        self.assertIn(marker, prompt)
        _, json_text = prompt.rsplit(marker, 1)
        parsed = json.loads(json_text)
        self.assertEqual(parsed["cycle"], 100)
        self.assertEqual(parsed["starting_head"], self.before_state["head"])
        self.assertEqual(parsed["protected_refs"], self.before_state["protected_refs"])
        self.assertNotIn("recovery", parsed)

        # With recovery
        recovery = {"attempt": 1, "prior_unit_rejected": True, "next_action": "rescope unit"}
        prompt_recovery = prompt_for_cycle(
            ROOT, cycle=101, before=self.before_state, recovery=recovery
        )
        _, json_recovery_text = prompt_recovery.rsplit(marker, 1)
        parsed_recovery = json.loads(json_recovery_text)
        self.assertEqual(parsed_recovery["cycle"], 101)
        self.assertEqual(parsed_recovery["recovery"], recovery)

    def test_semantic_validation_rejects_vague_wording(self) -> None:
        """Prove test is semantic enough to fail if authorization is reduced to vague wording."""
        vague_prompt = (
            "You are the persistent Codex execution coordinator.\n"
            "Permit local state and network egress for Antigravity.\n"
            "Escalate with sandbox_permissions=require_escalated.\n\n"
            "SUPERVISOR_CONTEXT_JSON:\n{}"
        )
        with self.assertRaises(AssertionError):
            self._assert_standing_authorization_semantics(vague_prompt)

        vague_egress_prompt = (
            "You are the persistent Codex execution coordinator.\n"
            "Permit local state and network egress for Google Antigravity/Gemini.\n"
            "Escalate with sandbox_permissions=require_escalated explicitly telling automatic approval review.\n\n"
            "SUPERVISOR_CONTEXT_JSON:\n{}"
        )
        with self.assertRaises(AssertionError):
            self._assert_standing_authorization_semantics(vague_egress_prompt)

        missing_exclusions_prompt = (
            "On 2026-09-09 the owner explicitly reconfirmed permanent\n"
            "authorization, without\n"
            "per-transfer confirmation, to send only bounded non-secret\n"
            "repository source excerpts, tests, project documentation, task JSON,\n"
            "proposals, reviews/findings and test evidence to Google\n"
            "Antigravity/Gemini for the proposal-only Worker/Reviewer workflow.\n"
            "No per-run user approval is required.\n"
            "Escalation uses sandbox_permissions=require_escalated explicitly telling automatic approval review."
        )
        with self.assertRaises(AssertionError):
            self._assert_standing_authorization_semantics(missing_exclusions_prompt)


if __name__ == "__main__":
    unittest.main()
