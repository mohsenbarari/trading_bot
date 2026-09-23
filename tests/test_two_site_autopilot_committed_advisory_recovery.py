#!/usr/bin/env python3
"""Tests for committed-CONTINUE advisory recovery in two-site autopilot."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_codex_autopilot import (
        AssignmentScope,
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        run_cycle,
        validate_result,
        write_supervisor_state,
    )
except ModuleNotFoundError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        AssignmentScope,
        AutopilotError,
        RESULT_KEYS,
        recover_committed_advisory_result,
        run_cycle,
        validate_result,
        write_supervisor_state,
    )

VALID_COMMIT = "1" * 40
VALID_WORKER_RUN_ID = "run-gemini-worker-001"
VALID_REVIEWER_RUN_ID = "run-gemini-reviewer-001"
VALID_PROPOSAL_SHA256 = "a" * 64
MODULE = recover_committed_advisory_result.__module__


def make_valid_incident_result(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Completed unit A and committed cleanly.",
        "commit": VALID_COMMIT,
        "next_action": "Proceed to unit B.",
        "blockers": ["Pre-existing test failure observed in baseline."],
        "gemini_worker_run_id": VALID_WORKER_RUN_ID,
        "gemini_reviewer_run_id": VALID_REVIEWER_RUN_ID,
        "proposal_sha256": VALID_PROPOSAL_SHA256,
    }
    base.update(overrides)
    return base


class TestCommittedAdvisoryRecoveryHelper(unittest.TestCase):
    """Unit tests for recover_committed_advisory_result helper."""

    def test_exact_committed_continue_recovers_without_mutating_input(self):
        raw = make_valid_incident_result()
        original = copy.deepcopy(raw)

        with self.assertRaises(AutopilotError) as exc_info:
            validate_result(raw)
        self.assertEqual(str(exc_info.exception), "non-blocked state cannot report blockers")

        recovered, advisories = recover_committed_advisory_result(raw, exc_info.exception)

        self.assertEqual(raw, original)
        self.assertEqual(raw["blockers"], ["Pre-existing test failure observed in baseline."])
        self.assertIsNot(advisories, raw["blockers"])
        self.assertEqual(advisories, ["Pre-existing test failure observed in baseline."])
        self.assertEqual(recovered["blockers"], [])
        self.assertEqual(recovered["state"], "CONTINUE")
        self.assertEqual(recovered["commit"], VALID_COMMIT)
        self.assertEqual(validate_result(recovered), recovered)

    def test_complete_with_blockers_does_not_recover(self):
        raw = make_valid_incident_result(state="COMPLETE", next_action=None)
        with self.assertRaises(AutopilotError) as exc_info:
            validate_result(raw)
        self.assertEqual(str(exc_info.exception), "non-blocked state cannot report blockers")

        with self.assertRaises(AutopilotError):
            recover_committed_advisory_result(raw, exc_info.exception)

    def test_continue_without_commit_does_not_recover(self):
        raw = make_valid_incident_result(
            commit=None,
            gemini_worker_run_id=None,
            gemini_reviewer_run_id=None,
            proposal_sha256=None,
        )
        with self.assertRaises(AutopilotError) as exc_info:
            validate_result(raw)
        self.assertEqual(str(exc_info.exception), "non-blocked state cannot report blockers")

        with self.assertRaises(AutopilotError):
            recover_committed_advisory_result(raw, exc_info.exception)

        raw_bad_commit = make_valid_incident_result(commit="not-a-40-hex-commit")
        with self.assertRaises(AutopilotError):
            recover_committed_advisory_result(
                raw_bad_commit,
                AutopilotError("non-blocked state cannot report blockers"),
            )

    def test_partial_or_invalid_gemini_evidence_does_not_recover(self):
        recoverable_exc = AutopilotError("non-blocked state cannot report blockers")

        cases = [
            make_valid_incident_result(gemini_worker_run_id=None),
            make_valid_incident_result(gemini_reviewer_run_id=None),
            make_valid_incident_result(proposal_sha256=None),
            make_valid_incident_result(gemini_worker_run_id="bad run id with spaces!"),
            make_valid_incident_result(gemini_reviewer_run_id=""),
            make_valid_incident_result(proposal_sha256="not-64-hex"),
            make_valid_incident_result(proposal_sha256="Z" * 64),
        ]
        for bad_case in cases:
            with self.subTest(bad_case=bad_case):
                with self.assertRaises(AutopilotError):
                    recover_committed_advisory_result(bad_case, recoverable_exc)

    def test_different_validation_error_does_not_recover(self):
        raw = make_valid_incident_result()
        different_errors = [
            AutopilotError("Codex result summary is missing"),
            AutopilotError("Codex result does not match the exact contract"),
            AutopilotError("Codex result commit is invalid"),
            AutopilotError("Codex result has an invalid disposition"),
            ValueError("non-blocked state cannot report blockers"),
        ]
        for err in different_errors:
            with self.subTest(err=err):
                with self.assertRaises(AutopilotError):
                    recover_committed_advisory_result(raw, err)

    def test_invalid_blockers_does_not_recover(self):
        recoverable_exc = AutopilotError("non-blocked state cannot report blockers")

        invalid_blocker_cases = [
            make_valid_incident_result(blockers=[]),
            make_valid_incident_result(blockers=[""]),
            make_valid_incident_result(blockers=["   "]),
            make_valid_incident_result(blockers=[123]),
            make_valid_incident_result(blockers=["valid", ""]),
            make_valid_incident_result(blockers=["item"] * 21),
            make_valid_incident_result(blockers="string instead of list"),
            make_valid_incident_result(blockers={"key": "val"}),
        ]
        for case in invalid_blocker_cases:
            with self.subTest(case=case):
                with self.assertRaises(AutopilotError):
                    recover_committed_advisory_result(case, recoverable_exc)

    def test_blocked_results_do_not_recover(self):
        recoverable_exc = AutopilotError("non-blocked state cannot report blockers")

        halt_blocked = make_valid_incident_result(
            state="BLOCKED",
            disposition="HALT",
            category="EXTERNAL_GATE",
            commit=None,
            gemini_worker_run_id=None,
            gemini_reviewer_run_id=None,
            proposal_sha256=None,
        )
        rescope_blocked = make_valid_incident_result(
            state="BLOCKED",
            disposition="RESCOPE",
            category="INTERNAL_REJECTION",
            commit=None,
            gemini_worker_run_id=None,
            gemini_reviewer_run_id=None,
            proposal_sha256=None,
        )
        for blocked_case in (halt_blocked, rescope_blocked):
            with self.subTest(blocked_case=blocked_case):
                with self.assertRaises(AutopilotError):
                    recover_committed_advisory_result(blocked_case, recoverable_exc)

    def test_other_malformed_fields_do_not_recover(self):
        recoverable_exc = AutopilotError("non-blocked state cannot report blockers")

        malformed_cases = [
            make_valid_incident_result(schema_version="2.0"),
            make_valid_incident_result(summary=""),
            make_valid_incident_result(summary="   "),
            make_valid_incident_result(next_action=""),
            make_valid_incident_result(next_action="   "),
            make_valid_incident_result(disposition="HALT"),
            make_valid_incident_result(category="EXTERNAL_GATE"),
            make_valid_incident_result(extra_key="unexpected"),
            {k: v for k, v in make_valid_incident_result().items() if k != "summary"},
        ]
        for malformed in malformed_cases:
            with self.subTest(malformed=malformed):
                with self.assertRaises(AutopilotError):
                    recover_committed_advisory_result(malformed, recoverable_exc)


class TestRunCycleCommittedAdvisoryRecovery(unittest.TestCase):
    """Hermetic tests for run_cycle handling of advisory recovery and transition checks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test-autopilot-recovery-")
        self.root = Path(self.temp_dir.name) / "repo"
        self.state_root = self.root / ".local/two-site-refactor/autopilot"
        self.codex = self.root / "bin/codex"
        self.root.mkdir(parents=True)
        self.state_root.mkdir(parents=True)
        (self.state_root / "runs").mkdir(parents=True, exist_ok=True)
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("#!/bin/sh\nexit 0\n")
        runtime_patch = patch(f"{MODULE}.codex_runtime_identity", return_value={})
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _setup_run_cycle_patches(self, result_payload: dict, transition_effect=None):
        before = {
            "head": "0" * 40,
            "status": "",
            "protected_refs": {},
            "control_blobs": {},
            "assignment_scope": AssignmentScope(
                assignment_id="WA-TEST-SCOPE-001",
                stage_id="P1-TEST",
                allowed_paths=("tests/**",),
                forbidden_paths=("blocked/**",),
                expires_at="2099-01-01T00:00:00Z",
                sha256="0" * 64,
            ),
        }
        identity = {
            "path": str(self.codex),
            "version": "codex-cli 0.153.0",
            "sha256": "fake-codex-sha",
            "runtime": {},
            "model": "gpt-6-sol",
            "reasoning": "xhigh",
        }

        def fake_capture_process(process, _stdin, stdout_path, stderr_path, _timeout, max_output=None):
            run_dir = stdout_path.parent
            (run_dir / "result.json").write_text(json.dumps(result_payload))
            stdout_path.write_text("")
            stderr_path.write_text("")

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.pid = 4321
        mock_process.__enter__.return_value = mock_process
        mock_process.__exit__.return_value = False

        if transition_effect is None:
            def default_transition(_root, _before, receipt):
                res = dict(receipt)
                res["head_before"] = before["head"]
                res["head_after"] = receipt.get("commit") or before["head"]
                res["head_changed"] = res["head_before"] != res["head_after"]
                return res
            transition_mock = MagicMock(side_effect=default_transition)
        elif isinstance(transition_effect, Exception):
            transition_mock = MagicMock(side_effect=transition_effect)
        else:
            transition_mock = MagicMock(side_effect=transition_effect)

        patches = [
            patch(f"{MODULE}.validate_repository", return_value=before),
            patch(f"{MODULE}.codex_identity", return_value=identity),
            patch(f"{MODULE}.file_sha256", return_value="fake-codex-sha"),
            patch(f"{MODULE}.subprocess.Popen", return_value=mock_process),
            patch(f"{MODULE}.capture_bounded_process", side_effect=fake_capture_process),
            patch(f"{MODULE}.prompt_for_cycle", return_value="fake prompt"),
            patch(f"{MODULE}.validate_transition", transition_mock),
        ]
        return patches, transition_mock

    def test_run_cycle_recovers_committed_continue_and_records_advisories(self):
        incident_result = make_valid_incident_result()
        patches, transition_mock = self._setup_run_cycle_patches(incident_result)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            run_dir, result = run_cycle(self.root, self.codex, self.state_root, cycle=1, timeout=300)

        transition_mock.assert_called_once()
        candidate_passed = transition_mock.call_args[0][2]
        self.assertEqual(candidate_passed["blockers"], [])
        self.assertEqual(candidate_passed["state"], "CONTINUE")

        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["advisories"], ["Pre-existing test failure observed in baseline."])

        status_data = json.loads((run_dir / "status.json").read_text())
        self.assertEqual(status_data["state"], "CONTINUE")
        self.assertEqual(status_data["blockers"], [])
        self.assertEqual(status_data["advisories"], ["Pre-existing test failure observed in baseline."])

    def test_run_cycle_transition_failure_is_not_swallowed(self):
        incident_result = make_valid_incident_result()
        transition_error = AutopilotError("cycle rewrote or diverged integration history")
        patches, transition_mock = self._setup_run_cycle_patches(
            incident_result, transition_effect=transition_error
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            with self.assertRaises(AutopilotError) as exc_info:
                run_cycle(self.root, self.codex, self.state_root, cycle=1, timeout=300)
            self.assertEqual(str(exc_info.exception), "cycle rewrote or diverged integration history")

        transition_mock.assert_called_once()
        candidate_passed = transition_mock.call_args[0][2]
        self.assertEqual(candidate_passed["blockers"], [])

        runs = list((self.state_root / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        status_data = json.loads((runs[0] / "status.json").read_text())
        self.assertEqual(status_data["state"], "FAILED")
        self.assertIn("cycle rewrote or diverged integration history", status_data["error"])

    def test_run_cycle_normal_strict_records_empty_advisories(self):
        strict_result = make_valid_incident_result(blockers=[])
        patches, transition_mock = self._setup_run_cycle_patches(strict_result)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            run_dir, result = run_cycle(self.root, self.codex, self.state_root, cycle=1, timeout=300)

        transition_mock.assert_called_once()
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["advisories"], [])

        status_data = json.loads((run_dir / "status.json").read_text())
        self.assertEqual(status_data["state"], "CONTINUE")
        self.assertEqual(status_data["blockers"], [])
        self.assertEqual(status_data["advisories"], [])

    def test_supervisor_state_published_continue_includes_advisories(self):
        write_supervisor_state(
            self.state_root,
            "CONTINUE",
            cycle=1,
            head=VALID_COMMIT,
            summary="Clean commit with advisory.",
            next_action="Continue next unit.",
            blockers=[],
            advisories=["Non-blocking baseline advisory."],
        )
        status_path = self.state_root / "status.json"
        self.assertTrue(status_path.exists())
        data = json.loads(status_path.read_text())
        self.assertEqual(data["state"], "CONTINUE")
        self.assertEqual(data["blockers"], [])
        self.assertEqual(data["advisories"], ["Non-blocking baseline advisory."])


if __name__ == "__main__":
    unittest.main()
