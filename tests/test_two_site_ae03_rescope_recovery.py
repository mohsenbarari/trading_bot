#!/usr/bin/env python3
"""Hermetic tests for AE03 pure rescope receipt byte codec."""
import unittest

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    ACCEPTED_RESULT_KEYS_ORDER,
    CONTROL_PATHS,
    MAX_RESCOPE_RECEIPT_BYTES,
    PROTECTED_REFS,
    RESCOPE_RECEIPT_KEYS,
    RESCOPE_RECEIPT_SCHEMA_VERSION,
    RESCOPE_RESULT_KEYS_ORDER,
    AutopilotError,
    parse_rescope_receipt,
    serialize_rescope_receipt,
)


def _make_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="asg-ae03-test",
        stage_id="P1-00b",
        sha256="a" * 64,
        expires_at="2026-12-31T23:59:59Z",
        allowed_paths=["scripts/two_site_codex_autopilot.py", "tests/test_two_site_ae03_rescope_recovery.py"],
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


def _make_result(repo_head: str = "a1b2c3d4e5" * 4) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "Rescope cycle required due to bounded proposal constraint.",
        "commit": None,
        "next_action": "Execute rescope receipt byte codec.",
        "blockers": ["Proposal exceeded single-unit boundaries."],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": repo_head,
        "head_after": repo_head,
        "head_changed": False,
    }


class TestAE03RescopeReceiptCodec(unittest.TestCase):
    def test_constants_and_result_keys_order(self) -> None:
        self.assertEqual(RESCOPE_RESULT_KEYS_ORDER, ACCEPTED_RESULT_KEYS_ORDER)
        self.assertEqual(set(RESCOPE_RECEIPT_KEYS), set(RESCOPE_RESULT_KEYS_ORDER))

    def test_roundtrip_all_attempts(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        for attempt, expected_terminal in [(1, False), (2, False), (3, True)]:
            raw_bytes = serialize_rescope_receipt(result, repo, attempt=attempt)
            self.assertIsInstance(raw_bytes, bytes)
            self.assertLessEqual(len(raw_bytes), MAX_RESCOPE_RECEIPT_BYTES)

            # Roundtrip parse with live repository
            envelope = parse_rescope_receipt(raw_bytes, repo)
            self.assertEqual(envelope["schema_version"], RESCOPE_RECEIPT_SCHEMA_VERSION)
            self.assertEqual(envelope["attempt"], attempt)
            self.assertEqual(envelope["terminal"], expected_terminal)
            self.assertEqual(envelope["result"]["state"], "BLOCKED")
            self.assertEqual(envelope["result"]["disposition"], "RESCOPE")
            self.assertEqual(envelope["result"]["category"], "INTERNAL_REJECTION")
            self.assertIsNone(envelope["result"]["commit"])
            self.assertIsNone(envelope["result"]["gemini_worker_run_id"])
            self.assertFalse(envelope["result"]["head_changed"])
            self.assertEqual(envelope["result"]["head_before"], repo["head"])
            self.assertEqual(envelope["result"]["head_after"], repo["head"])
            self.assertEqual(tuple(envelope["result"].keys()), RESCOPE_RESULT_KEYS_ORDER)

            # Roundtrip parse with persisted repository facts
            persisted_repo = envelope["repository"]
            envelope_persisted = parse_rescope_receipt(raw_bytes, persisted_repo)
            self.assertEqual(envelope, envelope_persisted)

    def test_serialize_terminal_explicit_validation(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        self.assertTrue(serialize_rescope_receipt(result, repo, attempt=3, terminal=True))
        self.assertTrue(serialize_rescope_receipt(result, repo, attempt=1, terminal=False))

        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=3, terminal=False)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=2, terminal=True)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(result, repo, attempt=1, terminal="False")  # type: ignore[arg-type]

    def test_serialize_attempt_bounds(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        for invalid_attempt in [0, 4, -1, True, False, 1.5, "1", None]:
            with self.assertRaises(AutopilotError):
                serialize_rescope_receipt(result, repo, attempt=invalid_attempt)  # type: ignore[arg-type]

    def test_serialize_result_invariants(self) -> None:
        repo = _make_repo()

        # Non-BLOCKED state
        bad = _make_result(repo["head"])
        bad["state"] = "CONTINUE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-RESCOPE disposition
        bad = _make_result(repo["head"])
        bad["disposition"] = "HALT"
        bad["category"] = "EXTERNAL_GATE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-INTERNAL_REJECTION category
        bad = _make_result(repo["head"])
        bad["category"] = "NONE"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-null commit
        bad = _make_result(repo["head"])
        bad["commit"] = "c" * 40
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Non-null Gemini evidence
        bad = _make_result(repo["head"])
        bad["gemini_worker_run_id"] = "worker-001"
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Changed head
        bad = _make_result(repo["head"])
        bad["head_changed"] = True
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Mismatched head_before and head_after
        bad = _make_result(repo["head"])
        bad["head_after"] = "b" * 40
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

        # Mismatched repository head
        bad = _make_result("e" * 40)
        with self.assertRaises(AutopilotError):
            serialize_rescope_receipt(bad, repo, attempt=1)

    def test_parse_rejects_malformed_utf8(self) -> None:
        repo = _make_repo()
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(b"\xff\xfe\x00\x01", repo)

    def test_parse_rejects_incomplete_and_malformed_json(self) -> None:
        repo = _make_repo()
        for bad_bytes in [b"", b"{", b'{"schema_version":', b'{"schema_version": 1,', b"null", b"123"]:
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(bad_bytes, repo)

    def test_parse_rejects_duplicate_keys(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"attempt": 1', valid_bytes)
        dup_bytes = valid_bytes.replace(b'"attempt": 1', b'"attempt": 1, "attempt": 1', 1)
        self.assertNotEqual(dup_bytes, valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(dup_bytes, repo)

    def test_parse_rejects_noncanonical_formatting(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"attempt": 1', valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes + b" ", repo)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes.replace(b'"attempt": 1', b'"attempt":  1'), repo)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes.replace(b'"attempt": 1', b'"attempt":1'), repo)
        if valid_bytes.endswith(b"\n"):
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(valid_bytes[:-1], repo)
        else:
            with self.assertRaises(AutopilotError):
                parse_rescope_receipt(valid_bytes + b"\n", repo)

    def test_parse_rejects_unexpected_fields(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)
        self.assertIn(b'"terminal": false', valid_bytes)
        bad_envelope = valid_bytes.replace(b'"terminal": false', b'"terminal": false, "extra": true')
        self.assertNotEqual(bad_envelope, valid_bytes)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(bad_envelope, repo)

    def test_parse_rejects_oversize(self) -> None:
        repo = _make_repo()
        oversized = b" " * (MAX_RESCOPE_RECEIPT_BYTES + 1)
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(oversized, repo)

    def test_parse_rejects_stale_expected_repository(self) -> None:
        repo = _make_repo()
        result = _make_result(repo["head"])
        valid_bytes = serialize_rescope_receipt(result, repo, attempt=1)

        stale_repo = dict(repo)
        stale_repo["head"] = "f" * 40
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes, stale_repo)

        drifted_refs = dict(repo)
        drifted_refs["protected_refs"] = {ref: "9" * 40 for ref in PROTECTED_REFS}
        with self.assertRaises(AutopilotError):
            parse_rescope_receipt(valid_bytes, drifted_refs)


if __name__ == "__main__":
    unittest.main()
