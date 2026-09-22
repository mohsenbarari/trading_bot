from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import (
    _ACCEPTED_CHECKPOINT_FILENAME,
    ACCEPTED_CHECKPOINT_FILENAME,
    ACCEPTED_CHECKPOINT_SCHEMA_VERSION,
    ACCEPTED_RECEIPT_KEYS,
    CONTROL_PATHS,
    MAX_ACCEPTED_CHECKPOINT_BYTES,
    PROTECTED_REFS,
    REPOSITORY_FACT_KEYS,
    AutopilotError,
    _persist_accepted_checkpoint,
    serialize_accepted_checkpoint,
    validate_accepted_checkpoint,
)


def _make_assignment_scope() -> AssignmentScope:
    return AssignmentScope(
        assignment_id="WA-P1-00B-AE02-DURABLE-CHECKPOINT-20260922-059",
        stage_id="P1-00b",
        allowed_paths=(
            "scripts/two_site_codex_autopilot.py",
            "tests/test_two_site_autopilot_checkpoint.py",
        ),
        forbidden_paths=("config/secrets.env",),
        expires_at="2026-09-23T00:00:00Z",
        sha256="f" * 64,
    )


def _make_repository(head: str = "a" * 40) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "d" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "e" * 40 for p in CONTROL_PATHS},
        "assignment_scope": _make_assignment_scope(),
    }


def _make_continue_receipt(
    head_after: str = "a" * 40,
    head_before: str = "b" * 40,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "پیشرفت گام بازآرایی\u200cها",
        "commit": head_after,
        "next_action": "اجرای چرخه بعدی با موفقیت",
        "blockers": [],
        "gemini_worker_run_id": "worker-cycle-0001",
        "gemini_reviewer_run_id": "reviewer-cycle-0001",
        "proposal_sha256": "c" * 64,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": True,
    }


class AcceptedCheckpointSerializerValidatorTests(unittest.TestCase):
    def test_serializes_canonical_unicode_checkpoint_with_exact_json_bytes(self) -> None:
        head = "a" * 40
        head_before = "b" * 40
        result = _make_continue_receipt(head_after=head, head_before=head_before)
        repository = _make_repository(head=head)

        serialized = serialize_accepted_checkpoint(result, repository)

        expected_envelope = {
            "schema_version": ACCEPTED_CHECKPOINT_SCHEMA_VERSION,
            "result": {
                "schema_version": "1.0",
                "state": "CONTINUE",
                "disposition": "NONE",
                "category": "NONE",
                "summary": "پیشرفت گام بازآرایی\u200cها",
                "commit": head,
                "next_action": "اجرای چرخه بعدی با موفقیت",
                "blockers": [],
                "gemini_worker_run_id": "worker-cycle-0001",
                "gemini_reviewer_run_id": "reviewer-cycle-0001",
                "proposal_sha256": "c" * 64,
                "head_before": head_before,
                "head_after": head,
                "head_changed": True,
            },
            "repository": {
                "head": head,
                "status": "",
                "protected_refs": {ref: "d" * 40 for ref in sorted(PROTECTED_REFS)},
                "control_blobs": {path: "e" * 40 for path in sorted(p.as_posix() for p in CONTROL_PATHS)},
                "assignment_scope": {
                    "assignment_id": "WA-P1-00B-AE02-DURABLE-CHECKPOINT-20260922-059",
                    "stage_id": "P1-00b",
                    "sha256": "f" * 64,
                    "expires_at": "2026-09-23T00:00:00Z",
                    "allowed_paths": [
                        "scripts/two_site_codex_autopilot.py",
                        "tests/test_two_site_autopilot_checkpoint.py",
                    ],
                    "forbidden_paths": [
                        "config/secrets.env",
                    ],
                },
            },
        }
        expected_bytes = (json.dumps(expected_envelope, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        self.assertEqual(serialized, expected_bytes)
        self.assertTrue(serialized.endswith(b"\n"))
        self.assertFalse(serialized.endswith(b"\n\n"))
        self.assertIn("پیشرفت گام بازآرایی\u200cها".encode("utf-8"), serialized)
        self.assertNotIn(b"\\u067e", serialized)

    def test_rejects_incomplete_or_noncanonical_continue_receipts(self) -> None:
        head = "a" * 40
        repo = _make_repository(head=head)
        canonical = _make_continue_receipt(head_after=head)

        for missing_key in ACCEPTED_RECEIPT_KEYS:
            with self.subTest(missing_key=missing_key):
                incomplete = dict(canonical)
                del incomplete[missing_key]
                with self.assertRaises(AutopilotError):
                    serialize_accepted_checkpoint(incomplete, repo)

        extra_key_receipt = dict(canonical, unexpected_key="extra_value")
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(extra_key_receipt, repo)

        false_head_changed = dict(canonical, head_changed=False)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(false_head_changed, repo)

        non_bool_head_changed = dict(canonical, head_changed=1)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(non_bool_head_changed, repo)

        nonempty_blockers = dict(canonical, blockers=["blocking issue"])
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(nonempty_blockers, repo)

        mismatched_commit = dict(canonical, commit="b" * 40)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(mismatched_commit, repo)

        mismatched_head_after = dict(canonical, head_after="b" * 40, commit="b" * 40)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(mismatched_head_after, repo)

        identical_heads = dict(canonical, head_before=head, head_after=head, commit=head)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(identical_heads, repo)

        invalid_head_sha = dict(canonical, head_before="G" * 40)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(invalid_head_sha, repo)

        complete_state = dict(canonical, state="COMPLETE")
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(complete_state, repo)

        halt_disposition = dict(canonical, disposition="HALT")
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(halt_disposition, repo)

        missing_evidence = dict(canonical, gemini_worker_run_id=None)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(missing_evidence, repo)

        empty_summary = dict(canonical, summary="   ")
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(empty_summary, repo)

        null_next_action = dict(canonical, next_action=None)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(null_next_action, repo)

        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(Path("scripts/two_site_codex_autopilot.py"), repo)

    def test_rejects_incomplete_repository_facts_and_enforces_exact_serialized_byte_limit(self) -> None:
        head = "a" * 40
        receipt = _make_continue_receipt(head_after=head)
        canonical_repo = _make_repository(head=head)

        for missing_repo_key in REPOSITORY_FACT_KEYS:
            with self.subTest(missing_repo_key=missing_repo_key):
                bad_repo = dict(canonical_repo)
                del bad_repo[missing_repo_key]
                with self.assertRaises(AutopilotError):
                    serialize_accepted_checkpoint(receipt, bad_repo)

        extra_repo_key = dict(canonical_repo, unreviewed_fact=True)
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, extra_repo_key)

        dirty_repo = dict(canonical_repo, status=" M scripts/two_site_codex_autopilot.py")
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, dirty_repo)

        missing_ref = dict(canonical_repo, protected_refs=dict(canonical_repo["protected_refs"]))
        del missing_ref["protected_refs"][PROTECTED_REFS[0]]
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, missing_ref)

        extra_ref = dict(canonical_repo, protected_refs=dict(canonical_repo["protected_refs"]))
        extra_ref["protected_refs"]["refs/heads/unreviewed"] = "1" * 40
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, extra_ref)

        null_ref = dict(canonical_repo, protected_refs=dict(canonical_repo["protected_refs"]))
        null_ref["protected_refs"][PROTECTED_REFS[0]] = None
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, null_ref)

        missing_control = dict(canonical_repo, control_blobs=dict(canonical_repo["control_blobs"]))
        del missing_control["control_blobs"][CONTROL_PATHS[0].as_posix()]
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, missing_control)

        extra_control = dict(canonical_repo, control_blobs=dict(canonical_repo["control_blobs"]))
        extra_control["control_blobs"]["untracked.py"] = "2" * 40
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, extra_control)

        dict_scope_repo = dict(canonical_repo, assignment_scope={"assignment_id": "test"})
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, dict_scope_repo)

        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(receipt, Path("scripts/two_site_codex_autopilot.py"))

        _, normalized_repo = validate_accepted_checkpoint(receipt, canonical_repo)
        self.assertIsInstance(normalized_repo["assignment_scope"], dict)
        self.assertNotIsInstance(normalized_repo["assignment_scope"], AssignmentScope)
        self.assertEqual(
            normalized_repo["assignment_scope"]["assignment_id"],
            canonical_repo["assignment_scope"].assignment_id,
        )

        baseline_receipt = dict(receipt, summary="VALID_SUMMARY")
        baseline_len = len(serialize_accepted_checkpoint(baseline_receipt, canonical_repo))
        padding_needed = MAX_ACCEPTED_CHECKPOINT_BYTES - baseline_len

        exact_receipt = dict(receipt, summary="VALID_SUMMARY" + ("Z" * padding_needed))
        exact_bytes = serialize_accepted_checkpoint(exact_receipt, canonical_repo)
        self.assertEqual(len(exact_bytes), 16384)
        self.assertEqual(len(exact_bytes), MAX_ACCEPTED_CHECKPOINT_BYTES)

        over_receipt = dict(receipt, summary="VALID_SUMMARY" + ("Z" * (padding_needed + 1)))
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(over_receipt, canonical_repo)

        unicode_multibyte_receipt = dict(
            receipt, summary="VALID_SUMMARY" + ("Z" * (padding_needed - 1)) + "ش"
        )
        with self.assertRaises(AutopilotError):
            serialize_accepted_checkpoint(unicode_multibyte_receipt, canonical_repo)


class AcceptedCheckpointAtomicPersistenceTests(unittest.TestCase):
    def test_atomically_persists_exact_private_checkpoint_without_touching_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-persistence-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME
            status_path = state_root / "status.json"

            prior_bytes = b'{"prior": "checkpoint", "stale": true}\n'
            checkpoint_path.write_bytes(prior_bytes)
            os.chmod(checkpoint_path, 0o644)

            sentinel_status_bytes = b'{"state": "SENTINEL", "cycle": 42, "persisted": false}\n'
            status_path.write_bytes(sentinel_status_bytes)
            os.chmod(status_path, 0o600)

            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)

            expected_bytes = serialize_accepted_checkpoint(result, repository)

            _persist_accepted_checkpoint(state_root, result, repository)

            self.assertTrue(checkpoint_path.is_file())
            self.assertFalse(checkpoint_path.is_symlink())
            persisted_bytes = checkpoint_path.read_bytes()
            self.assertEqual(persisted_bytes, expected_bytes)
            self.assertNotEqual(persisted_bytes, prior_bytes)

            mode = stat.S_IMODE(checkpoint_path.stat().st_mode)
            self.assertEqual(mode, 0o600)

            self.assertEqual(status_path.read_bytes(), sentinel_status_bytes)

            children = {p.name for p in state_root.iterdir()}
            self.assertEqual(children, {_ACCEPTED_CHECKPOINT_FILENAME, "status.json"})
            self.assertFalse(any(p.name.endswith(".tmp") for p in state_root.iterdir()))
            self.assertFalse(any(p.name.startswith(".accepted_checkpoint-") for p in state_root.iterdir()))

    def test_serializer_failure_preserves_existing_checkpoint_without_temp_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-serializer-fail-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME

            existing_bytes = b'{"canonical": "prior_state", "intact": true}\n'
            checkpoint_path.write_bytes(existing_bytes)
            os.chmod(checkpoint_path, 0o600)
            initial_stat = checkpoint_path.stat()

            invalid_result = {"invalid_key": "not_a_valid_receipt"}
            valid_repo = _make_repository(head="a" * 40)

            with self.assertRaises(AutopilotError):
                _persist_accepted_checkpoint(state_root, invalid_result, valid_repo)

            self.assertTrue(checkpoint_path.is_file())
            self.assertEqual(checkpoint_path.read_bytes(), existing_bytes)
            self.assertEqual(stat.S_IMODE(checkpoint_path.stat().st_mode), 0o600)
            self.assertEqual(checkpoint_path.stat().st_mtime_ns, initial_stat.st_mtime_ns)

            children = {p.name for p in state_root.iterdir()}
            self.assertEqual(children, {_ACCEPTED_CHECKPOINT_FILENAME})

    def test_replace_failure_preserves_existing_checkpoint_and_cleans_temp_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-replace-fail-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME

            existing_bytes = b'{"canonical": "prior_checkpoint_data"}\n'
            checkpoint_path.write_bytes(existing_bytes)
            os.chmod(checkpoint_path, 0o600)
            initial_stat = checkpoint_path.stat()

            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)

            with patch.object(os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError) as cm:
                    _persist_accepted_checkpoint(state_root, result, repository)
                self.assertIn("simulated replace failure", str(cm.exception))

            self.assertTrue(checkpoint_path.is_file())
            self.assertEqual(checkpoint_path.read_bytes(), existing_bytes)
            self.assertEqual(stat.S_IMODE(checkpoint_path.stat().st_mode), 0o600)
            self.assertEqual(checkpoint_path.stat().st_mtime_ns, initial_stat.st_mtime_ns)

            children = {p.name for p in state_root.iterdir()}
            self.assertEqual(children, {_ACCEPTED_CHECKPOINT_FILENAME})
            self.assertFalse(any(p.name.startswith(".accepted_checkpoint-") for p in state_root.iterdir()))
