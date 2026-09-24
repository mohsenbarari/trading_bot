"""Unit tests for build_rescope_recovery_context."""
from __future__ import annotations

import copy
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_codex_autopilot import (
        CONTROL_PATHS,
        PROTECTED_REFS,
        AutopilotError,
        build_rescope_recovery_context,
        serialize_rescope_receipt,
    )
    from scripts.two_site_proposal_runner import unique_json
except ModuleNotFoundError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        CONTROL_PATHS,
        PROTECTED_REFS,
        AutopilotError,
        build_rescope_recovery_context,
        serialize_rescope_receipt,
    )
    from two_site_proposal_runner import unique_json  # type: ignore[no-redef]


class TestBuildRescopeRecoveryContext(unittest.TestCase):
    def _valid_repo(self) -> dict[str, object]:
        head = "1" * 40
        return {
            "head": head,
            "status": "",
            "protected_refs": {ref: "2" * 40 for ref in PROTECTED_REFS},
            "control_blobs": {p.as_posix(): "3" * 40 for p in CONTROL_PATHS},
            "assignment_scope": {
                "assignment_id": "WA-P1-00B-001",
                "stage_id": "P1-00b",
                "sha256": "4" * 64,
                "expires_at": "2026-10-01T00:00:00Z",
                "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
                "forbidden_paths": [],
            },
        }

    def _valid_result(self, head: str) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "state": "BLOCKED",
            "disposition": "RESCOPE",
            "category": "INTERNAL_REJECTION",
            "summary": "Rescope requested for slice isolation.",
            "commit": None,
            "next_action": "Implement slice A",
            "blockers": ["Blocker 1", "Blocker 2"],
            "gemini_worker_run_id": None,
            "gemini_reviewer_run_id": None,
            "proposal_sha256": None,
            "head_before": head,
            "head_after": head,
            "head_changed": False,
        }

    def _canonical_receipt(self, repo: dict[str, object], attempt: int) -> dict[str, object]:
        result = self._valid_result(str(repo["head"]))
        payload = serialize_rescope_receipt(result, repo, attempt=attempt)
        return unique_json(payload.decode("utf-8"))

    def test_attempt_1_exact_context_matching_in_loop(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        context = build_rescope_recovery_context(receipt, repo)

        expected_keys = {
            "attempt",
            "attempt_count",
            "consecutive_rescopes",
            "prior_unit_rejected",
            "rejected",
            "next_action",
            "blockers",
            "defer_prior_unit",
        }
        self.assertEqual(set(context), expected_keys)
        self.assertEqual(context["attempt"], 1)
        self.assertEqual(context["attempt_count"], 1)
        self.assertEqual(context["consecutive_rescopes"], 1)
        self.assertIs(context["prior_unit_rejected"], True)
        self.assertIs(context["rejected"], True)
        self.assertEqual(context["next_action"], "Implement slice A")
        self.assertEqual(context["blockers"], ["Blocker 1", "Blocker 2"])
        self.assertIs(context["defer_prior_unit"], False)
        self.assertNotIn("deferred_unit", context)

        self.assertIsNot(context["blockers"], receipt["result"]["blockers"])

    def test_attempt_2_exact_context_matching_in_loop(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 2)
        context = build_rescope_recovery_context(receipt, repo)

        expected_keys = {
            "attempt",
            "attempt_count",
            "consecutive_rescopes",
            "prior_unit_rejected",
            "rejected",
            "next_action",
            "blockers",
            "defer_prior_unit",
            "deferred_unit",
        }
        self.assertEqual(set(context), expected_keys)
        self.assertEqual(context["attempt"], 2)
        self.assertEqual(context["attempt_count"], 2)
        self.assertEqual(context["consecutive_rescopes"], 2)
        self.assertIs(context["prior_unit_rejected"], True)
        self.assertIs(context["rejected"], True)
        self.assertEqual(context["next_action"], "Implement slice A")
        self.assertEqual(context["blockers"], ["Blocker 1", "Blocker 2"])
        self.assertIs(context["defer_prior_unit"], True)

        deferred = context["deferred_unit"]
        self.assertIsInstance(deferred, dict)
        self.assertEqual(set(deferred), {"next_action", "blockers"})
        self.assertEqual(deferred["next_action"], "Implement slice A")
        self.assertEqual(deferred["blockers"], ["Blocker 1", "Blocker 2"])

        self.assertIsNot(context["blockers"], receipt["result"]["blockers"])
        self.assertIsNot(deferred["blockers"], receipt["result"]["blockers"])
        self.assertIsNot(context["blockers"], deferred["blockers"])

    def test_terminal_attempt_3_rejected(self) -> None:
        repo = self._valid_repo()
        receipt3 = self._canonical_receipt(repo, 3)
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(receipt3, repo)

    def test_stale_or_mismatched_repository_rejected(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        stale_repo = copy.deepcopy(repo)
        stale_repo["head"] = "f" * 40
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(receipt, stale_repo)

    def test_extra_keys_in_receipt_rejected(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        corrupt_receipt = copy.deepcopy(receipt)
        corrupt_receipt["unexpected_envelope_field"] = "invalid"
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(corrupt_receipt, repo)

    def test_bool_and_invalid_attempt_rejected(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        for bad_attempt in (True, False, 0, 3, 4, -1, "1"):
            bad_receipt = copy.deepcopy(receipt)
            bad_receipt["attempt"] = bad_attempt
            with self.assertRaises(AutopilotError):
                build_rescope_recovery_context(bad_receipt, repo)

    def test_type_and_path_arguments_rejected(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(Path("some/receipt.json"), repo)
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(receipt, Path("some/repo"))
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context("not a mapping", repo)
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(receipt, "not a repo")

    def test_result_corruption_rejected(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 1)
        corrupted = copy.deepcopy(receipt)
        corrupted["result"]["next_action"] = None
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(corrupted, repo)

        corrupted_commit = copy.deepcopy(receipt)
        corrupted_commit["result"]["commit"] = "a" * 40
        with self.assertRaises(AutopilotError):
            build_rescope_recovery_context(corrupted_commit, repo)

    def test_no_input_mutation(self) -> None:
        repo = self._valid_repo()
        receipt = self._canonical_receipt(repo, 2)
        original_receipt = copy.deepcopy(receipt)
        original_repo = copy.deepcopy(repo)

        context = build_rescope_recovery_context(receipt, repo)
        self.assertEqual(receipt, original_receipt)
        self.assertEqual(repo, original_repo)

        context["blockers"].append("extra")
        self.assertEqual(receipt["result"]["blockers"], ["Blocker 1", "Blocker 2"])
        self.assertEqual(context["deferred_unit"]["blockers"], ["Blocker 1", "Blocker 2"])

    def test_no_file_io(self) -> None:
        repo = self._valid_repo()
        receipt1 = self._canonical_receipt(repo, 1)
        receipt2 = self._canonical_receipt(repo, 2)

        def _forbidden_io(*args: object, **kwargs: object) -> None:
            raise AssertionError("file I/O forbidden in build_rescope_recovery_context")

        with patch("builtins.open", side_effect=_forbidden_io), patch("os.open", side_effect=_forbidden_io):
            ctx1 = build_rescope_recovery_context(receipt1, repo)
            self.assertEqual(ctx1["attempt"], 1)
            ctx2 = build_rescope_recovery_context(receipt2, repo)
            self.assertEqual(ctx2["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
