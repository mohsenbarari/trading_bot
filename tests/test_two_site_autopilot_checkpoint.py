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
    _accepted_checkpoint_matches_repository,
    _load_accepted_checkpoint,
    _persist_accepted_checkpoint,
    publish_accepted_checkpoint,
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


class AcceptedCheckpointAtomicReadValidationTests(unittest.TestCase):
    def test_loads_exact_canonical_checkpoint_envelope_without_touching_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-load-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME
            status_path = state_root / "status.json"

            sentinel_status_bytes = b'{"state": "SENTINEL", "cycle": 99, "untouched": true}\n'
            status_path.write_bytes(sentinel_status_bytes)
            os.chmod(status_path, 0o600)

            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)

            canonical_bytes = serialize_accepted_checkpoint(result, repository)
            checkpoint_path.write_bytes(canonical_bytes)
            os.chmod(checkpoint_path, 0o600)

            loaded = _load_accepted_checkpoint(state_root)

            self.assertIsNotNone(loaded)
            self.assertIsInstance(loaded, dict)
            self.assertEqual(set(loaded.keys()), {"schema_version", "result", "repository"})
            self.assertEqual(loaded["schema_version"], ACCEPTED_CHECKPOINT_SCHEMA_VERSION)

            expected_parsed = json.loads(canonical_bytes.decode("utf-8"))
            self.assertEqual(loaded, expected_parsed)
            self.assertEqual(checkpoint_path.read_bytes(), canonical_bytes)
            self.assertEqual(status_path.read_bytes(), sentinel_status_bytes)

    def test_rejects_malformed_oversized_and_unknown_schema_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-reject-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME

            # Case 0: Missing file
            self.assertIsNone(_load_accepted_checkpoint(state_root))

            # Case 1: Malformed JSON
            malformed_bytes = b'{"schema_version": 1, "unclosed": '
            checkpoint_path.write_bytes(malformed_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), malformed_bytes)

            # Case 2: Duplicate keys
            duplicate_key_bytes = b'{"schema_version": 1, "schema_version": 1, "result": {}, "repository": {}}\n'
            checkpoint_path.write_bytes(duplicate_key_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), duplicate_key_bytes)

            # Case 3: Invalid UTF-8
            invalid_utf8_bytes = b'{"schema_version": 1, "bad": \xff\xfe}\n'
            checkpoint_path.write_bytes(invalid_utf8_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), invalid_utf8_bytes)

            # Case 4: MAX_ACCEPTED_CHECKPOINT_BYTES + 1 bytes
            prefix = b'{"schema_version": 1, "pad": "'
            suffix = b'"}\n'
            needed_pad = MAX_ACCEPTED_CHECKPOINT_BYTES + 1 - len(prefix) - len(suffix)
            oversized_bytes = prefix + (b"x" * needed_pad) + suffix
            self.assertEqual(len(oversized_bytes), MAX_ACCEPTED_CHECKPOINT_BYTES + 1)
            checkpoint_path.write_bytes(oversized_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), oversized_bytes)

            # Canonical baseline for schema and semantic variants
            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)
            canonical_bytes = serialize_accepted_checkpoint(result, repository)
            canonical_envelope = json.loads(canonical_bytes.decode("utf-8"))

            # Case 5: Unknown schema version
            unknown_schema = dict(canonical_envelope, schema_version=99)
            unknown_schema_bytes = (json.dumps(unknown_schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            checkpoint_path.write_bytes(unknown_schema_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), unknown_schema_bytes)

            # Bool schema version (should not pass as int 1)
            bool_schema = dict(canonical_envelope, schema_version=True)
            bool_schema_bytes = (json.dumps(bool_schema, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            checkpoint_path.write_bytes(bool_schema_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), bool_schema_bytes)

            # Case 6a: Byte-noncanonical envelope (extra trailing newline)
            byte_noncanonical = canonical_bytes + b"\n"
            checkpoint_path.write_bytes(byte_noncanonical)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), byte_noncanonical)

            # Case 6b: Byte-noncanonical key ordering
            key_reordered = {
                "repository": canonical_envelope["repository"],
                "result": canonical_envelope["result"],
                "schema_version": canonical_envelope["schema_version"],
            }
            reordered_bytes = (json.dumps(key_reordered, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            checkpoint_path.write_bytes(reordered_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), reordered_bytes)

            # Case 6c: Semantically noncanonical (head_changed is False)
            semantically_bad_envelope = json.loads(canonical_bytes.decode("utf-8"))
            semantically_bad_envelope["result"]["head_changed"] = False
            semantic_bad_bytes = (json.dumps(semantically_bad_envelope, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            checkpoint_path.write_bytes(semantic_bad_bytes)
            self.assertIsNone(_load_accepted_checkpoint(state_root))
            self.assertEqual(checkpoint_path.read_bytes(), semantic_bad_bytes)

    def test_rejects_symlink_and_hardlink_checkpoints_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-checkpoint-links-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME

            target_dir = state_root / "targets"
            target_dir.mkdir(mode=0o700)
            target_path = target_dir / "valid_target.json"

            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)
            canonical_bytes = serialize_accepted_checkpoint(result, repository)

            target_path.write_bytes(canonical_bytes)
            os.chmod(target_path, 0o600)

            # Symlink rejection
            os.symlink(target_path, checkpoint_path)
            self.assertTrue(checkpoint_path.is_symlink())
            self.assertEqual(os.readlink(checkpoint_path), str(target_path))

            self.assertIsNone(_load_accepted_checkpoint(state_root))

            self.assertTrue(checkpoint_path.is_symlink())
            self.assertEqual(os.readlink(checkpoint_path), str(target_path))
            self.assertEqual(checkpoint_path.read_bytes(), canonical_bytes)
            self.assertEqual(target_path.read_bytes(), canonical_bytes)

            checkpoint_path.unlink()

            # Hardlink rejection
            os.link(target_path, checkpoint_path)
            self.assertFalse(checkpoint_path.is_symlink())
            self.assertEqual(checkpoint_path.stat().st_nlink, 2)
            self.assertEqual(target_path.stat().st_nlink, 2)
            self.assertEqual(checkpoint_path.stat().st_ino, target_path.stat().st_ino)

            self.assertIsNone(_load_accepted_checkpoint(state_root))

            self.assertFalse(checkpoint_path.is_symlink())
            self.assertEqual(checkpoint_path.stat().st_nlink, 2)
            self.assertEqual(target_path.stat().st_nlink, 2)
            self.assertEqual(checkpoint_path.stat().st_ino, target_path.stat().st_ino)
            self.assertEqual(checkpoint_path.read_bytes(), canonical_bytes)
            self.assertEqual(target_path.read_bytes(), canonical_bytes)


class AcceptedCheckpointRepositoryBindingValidationTests(unittest.TestCase):
    def test_matches_exact_current_repository_facts_without_io_or_mutation(self) -> None:
        head = "a" * 40
        head_before = "b" * 40
        receipt = _make_continue_receipt(head_after=head, head_before=head_before)
        repo_for_checkpoint = _make_repository(head=head)

        raw_bytes = serialize_accepted_checkpoint(receipt, repo_for_checkpoint)
        checkpoint = json.loads(raw_bytes.decode("utf-8"))

        current_repo = _make_repository(head=head)
        checkpoint_before = json.loads(json.dumps(checkpoint))
        repo_before = {
            "head": current_repo["head"],
            "status": current_repo["status"],
            "protected_refs": dict(current_repo["protected_refs"]),
            "control_blobs": dict(current_repo["control_blobs"]),
            "assignment_scope": current_repo["assignment_scope"],
        }

        io_fail = AssertionError("unexpected I/O attempt")
        with (
            patch("scripts.two_site_codex_autopilot.validate_repository", side_effect=io_fail),
            patch("scripts.two_site_codex_autopilot._load_accepted_checkpoint", side_effect=io_fail),
            patch("scripts.two_site_codex_autopilot.git", side_effect=io_fail),
            patch("scripts.two_site_codex_autopilot.bounded_file_text", side_effect=io_fail),
            patch("scripts.two_site_codex_autopilot.load_active_assignment_scope", side_effect=io_fail),
            patch("scripts.two_site_codex_autopilot.ref_snapshot", side_effect=io_fail),
        ):
            matched = _accepted_checkpoint_matches_repository(checkpoint, current_repo)

        self.assertTrue(matched)
        self.assertEqual(checkpoint, checkpoint_before)
        self.assertEqual(current_repo, repo_before)
        self.assertIs(current_repo["assignment_scope"], repo_before["assignment_scope"])

    def test_rejects_head_protected_ref_and_control_blob_drift(self) -> None:
        head = "a" * 40
        head_before = "b" * 40
        receipt = _make_continue_receipt(head_after=head, head_before=head_before)
        canonical_repo = _make_repository(head=head)
        checkpoint = json.loads(serialize_accepted_checkpoint(receipt, canonical_repo).decode("utf-8"))

        self.assertTrue(_accepted_checkpoint_matches_repository(checkpoint, canonical_repo))

        with self.subTest(drift="head"):
            drift_head_repo = dict(canonical_repo, head="f" * 40)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, drift_head_repo))

        with self.subTest(drift="protected_ref"):
            drift_refs = dict(canonical_repo["protected_refs"])
            drift_refs[PROTECTED_REFS[0]] = "1" * 40
            drift_ref_repo = dict(canonical_repo, protected_refs=drift_refs)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, drift_ref_repo))

        with self.subTest(drift="control_blob"):
            drift_controls = dict(canonical_repo["control_blobs"])
            drift_controls[CONTROL_PATHS[0].as_posix()] = "2" * 40
            drift_ctrl_repo = dict(canonical_repo, control_blobs=drift_controls)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, drift_ctrl_repo))

        self.assertTrue(_accepted_checkpoint_matches_repository(checkpoint, canonical_repo))

        for missing_key in REPOSITORY_FACT_KEYS:
            with self.subTest(missing_repo_key=missing_key):
                incomplete_repo = dict(canonical_repo)
                del incomplete_repo[missing_key]
                self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, incomplete_repo))

        with self.subTest(malformed="extra_key"):
            extra_repo = dict(canonical_repo, extra_key="unexpected")
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, extra_repo))

        with self.subTest(malformed="dirty_status"):
            dirty_repo = dict(canonical_repo, status=" M scripts/two_site_codex_autopilot.py")
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dirty_repo))

        with self.subTest(malformed="invalid_head_hex"):
            bad_hex_repo = dict(canonical_repo, head="g" * 40)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, bad_hex_repo))

        with self.subTest(malformed="invalid_head_len"):
            bad_len_repo = dict(canonical_repo, head="a" * 39)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, bad_len_repo))

        with self.subTest(malformed="missing_protected_ref"):
            missing_ref = dict(canonical_repo["protected_refs"])
            del missing_ref[PROTECTED_REFS[0]]
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dict(canonical_repo, protected_refs=missing_ref)))

        with self.subTest(malformed="extra_protected_ref"):
            extra_ref = dict(canonical_repo["protected_refs"], unexpected_ref="3" * 40)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dict(canonical_repo, protected_refs=extra_ref)))

        with self.subTest(malformed="missing_control_blob"):
            missing_ctrl = dict(canonical_repo["control_blobs"])
            del missing_ctrl[CONTROL_PATHS[0].as_posix()]
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dict(canonical_repo, control_blobs=missing_ctrl)))

        with self.subTest(malformed="extra_control_blob"):
            extra_ctrl = dict(canonical_repo["control_blobs"], extra_path="4" * 40)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dict(canonical_repo, control_blobs=extra_ctrl)))

        with self.subTest(malformed="non_dict_repo"):
            for non_dict in ("not_a_dict", 123, None, Path("scripts/two_site_codex_autopilot.py")):
                self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, non_dict))

        with self.subTest(malformed="dict_assignment_scope"):
            scope_dict = {
                "assignment_id": "WA-P1-00B-AE02-DURABLE-CHECKPOINT-20260922-059",
                "stage_id": "P1-00b",
                "sha256": "f" * 64,
                "expires_at": "2026-09-23T00:00:00Z",
                "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
                "forbidden_paths": ["config/secrets.env"],
            }
            dict_scope_repo = dict(canonical_repo, assignment_scope=scope_dict)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, dict_scope_repo))

    def test_rejects_assignment_identity_and_scope_drift(self) -> None:
        head = "a" * 40
        head_before = "b" * 40
        receipt = _make_continue_receipt(head_after=head, head_before=head_before)
        canonical_repo = _make_repository(head=head)
        base_scope = canonical_repo["assignment_scope"]
        assert isinstance(base_scope, AssignmentScope)

        raw_bytes = serialize_accepted_checkpoint(receipt, canonical_repo)
        checkpoint = json.loads(raw_bytes.decode("utf-8"))

        independent_scope = AssignmentScope(
            assignment_id=base_scope.assignment_id,
            stage_id=base_scope.stage_id,
            allowed_paths=list(base_scope.allowed_paths),
            forbidden_paths=list(base_scope.forbidden_paths),
            expires_at=base_scope.expires_at,
            sha256=base_scope.sha256,
        )
        self.assertIsNot(independent_scope, base_scope)
        self.assertTrue(
            _accepted_checkpoint_matches_repository(
                checkpoint,
                dict(canonical_repo, assignment_scope=independent_scope),
            )
        )

        variations = {
            "assignment_id": AssignmentScope(
                assignment_id="WA-DIFFERENT-ID-20260922-999",
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=base_scope.forbidden_paths,
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            ),
            "stage_id": AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id="P1-01",
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=base_scope.forbidden_paths,
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            ),
            "sha256": AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=base_scope.forbidden_paths,
                expires_at=base_scope.expires_at,
                sha256="0" * 64,
            ),
            "expires_at": AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=base_scope.forbidden_paths,
                expires_at="2026-09-24T12:00:00Z",
                sha256=base_scope.sha256,
            ),
            "allowed_paths": AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=("scripts/different.py",),
                forbidden_paths=base_scope.forbidden_paths,
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            ),
            "forbidden_paths": AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=("config/other_secret.env",),
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            ),
        }

        for field_name, drifted_scope in variations.items():
            with self.subTest(drift_field=field_name):
                drifted_repo = dict(canonical_repo, assignment_scope=drifted_scope)
                self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, drifted_repo))

        with self.subTest(order="allowed_paths_reversed"):
            reversed_allowed_scope = AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=tuple(reversed(base_scope.allowed_paths)),
                forbidden_paths=base_scope.forbidden_paths,
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            )
            self.assertNotEqual(reversed_allowed_scope.allowed_paths, base_scope.allowed_paths)
            reversed_repo = dict(canonical_repo, assignment_scope=reversed_allowed_scope)
            self.assertFalse(_accepted_checkpoint_matches_repository(checkpoint, reversed_repo))

        with self.subTest(order="forbidden_paths_reversed"):
            two_forbidden_scope = AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=("config/secrets1.env", "config/secrets2.env"),
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            )
            two_forbidden_repo = dict(canonical_repo, assignment_scope=two_forbidden_scope)
            two_forbidden_checkpoint = json.loads(
                serialize_accepted_checkpoint(receipt, two_forbidden_repo).decode("utf-8")
            )
            reversed_forbidden_scope = AssignmentScope(
                assignment_id=base_scope.assignment_id,
                stage_id=base_scope.stage_id,
                allowed_paths=base_scope.allowed_paths,
                forbidden_paths=("config/secrets2.env", "config/secrets1.env"),
                expires_at=base_scope.expires_at,
                sha256=base_scope.sha256,
            )
            reversed_forbidden_repo = dict(canonical_repo, assignment_scope=reversed_forbidden_scope)
            self.assertFalse(
                _accepted_checkpoint_matches_repository(
                    two_forbidden_checkpoint, reversed_forbidden_repo
                )
            )

        invalid_checkpoints = [
            ("none", None),
            ("string", "checkpoint"),
            ("number", 42),
            ("path", Path("accepted_checkpoint.json")),
            ("missing_schema_version", {"result": checkpoint["result"], "repository": checkpoint["repository"]}),
            ("missing_result", {"schema_version": 1, "repository": checkpoint["repository"]}),
            ("missing_repository", {"schema_version": 1, "result": checkpoint["result"]}),
            ("extra_key", dict(checkpoint, unexpected="key")),
            ("schema_version_int_99", dict(checkpoint, schema_version=99)),
            ("schema_version_bool", dict(checkpoint, schema_version=True)),
            ("schema_version_str", dict(checkpoint, schema_version="1")),
            ("dirty_checkpoint_repo", dict(checkpoint, repository=dict(checkpoint["repository"], status=" M foo.py"))),
            ("bad_checkpoint_result_state", dict(checkpoint, result=dict(checkpoint["result"], state="COMPLETE"))),
            ("bad_checkpoint_result_head_changed", dict(checkpoint, result=dict(checkpoint["result"], head_changed=False))),
            ("bad_checkpoint_result_commit_mismatch", dict(checkpoint, result=dict(checkpoint["result"], commit="0" * 40))),
            ("checkpoint_repo_scope_is_class", dict(checkpoint, repository=dict(checkpoint["repository"], assignment_scope=base_scope))),
        ]

        repo_snapshot = {
            "head": canonical_repo["head"],
            "status": canonical_repo["status"],
            "protected_refs": dict(canonical_repo["protected_refs"]),
            "control_blobs": dict(canonical_repo["control_blobs"]),
            "assignment_scope": canonical_repo["assignment_scope"],
        }

        for label, invalid_cp in invalid_checkpoints:
            with self.subTest(invalid_checkpoint=label):
                self.assertFalse(_accepted_checkpoint_matches_repository(invalid_cp, canonical_repo))
                self.assertEqual(canonical_repo, repo_snapshot)
                self.assertIs(canonical_repo["assignment_scope"], repo_snapshot["assignment_scope"])


class AcceptedCheckpointPublicationBoundaryTests(unittest.TestCase):
    def test_publishes_matching_committed_continue_without_touching_live_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-publish-checkpoint-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME
            status_path = state_root / "status.json"

            prior_checkpoint_bytes = b'{"prior": "checkpoint", "stale": true}\n'
            checkpoint_path.write_bytes(prior_checkpoint_bytes)
            os.chmod(checkpoint_path, 0o600)

            sentinel_status_bytes = b'{"state": "RUNNING", "cycle": 12, "active": true}\n'
            status_path.write_bytes(sentinel_status_bytes)
            os.chmod(status_path, 0o600)
            initial_status_stat = status_path.stat()

            head = "a" * 40
            head_before = "b" * 40
            result = _make_continue_receipt(head_after=head, head_before=head_before)
            repository = _make_repository(head=head)

            expected_bytes = serialize_accepted_checkpoint(result, repository)

            outcome = publish_accepted_checkpoint(state_root, result, repository)
            self.assertIsNone(outcome)

            self.assertTrue(checkpoint_path.is_file())
            self.assertFalse(checkpoint_path.is_symlink())
            persisted_bytes = checkpoint_path.read_bytes()
            self.assertEqual(persisted_bytes, expected_bytes)
            self.assertNotEqual(persisted_bytes, prior_checkpoint_bytes)

            mode = stat.S_IMODE(checkpoint_path.stat().st_mode)
            self.assertEqual(mode, 0o600)

            self.assertEqual(status_path.read_bytes(), sentinel_status_bytes)
            self.assertEqual(status_path.stat().st_mtime_ns, initial_status_stat.st_mtime_ns)

            loaded = _load_accepted_checkpoint(state_root)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded, json.loads(expected_bytes.decode("utf-8")))

            children = {p.name for p in state_root.iterdir()}
            self.assertEqual(children, {_ACCEPTED_CHECKPOINT_FILENAME, "status.json"})
            self.assertFalse(any(p.name.startswith(".accepted_checkpoint-") for p in state_root.iterdir()))

    def test_rejects_invalid_receipt_or_repository_mismatch_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test-publish-reject-") as temp_dir:
            state_root = Path(temp_dir)
            checkpoint_path = state_root / _ACCEPTED_CHECKPOINT_FILENAME
            status_path = state_root / "status.json"

            pre_existing_checkpoint_bytes = b'{"canonical": "pre_existing_checkpoint"}\n'
            checkpoint_path.write_bytes(pre_existing_checkpoint_bytes)
            os.chmod(checkpoint_path, 0o600)
            initial_cp_stat = checkpoint_path.stat()

            pre_existing_status_bytes = b'{"state": "RUNNING", "cycle": 3}\n'
            status_path.write_bytes(pre_existing_status_bytes)
            os.chmod(status_path, 0o600)
            initial_status_stat = status_path.stat()

            head = "a" * 40
            head_before = "b" * 40
            canonical_result = _make_continue_receipt(head_after=head, head_before=head_before)
            canonical_repo = _make_repository(head=head)

            # 1. Non-CONTINUE receipt (COMPLETE)
            complete_receipt = dict(canonical_result, state="COMPLETE")
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, complete_receipt, canonical_repo)

            # 2. Non-CONTINUE receipt (BLOCKED)
            blocked_receipt = dict(
                canonical_result,
                state="BLOCKED",
                disposition="HALT",
                category="EXTERNAL_GATE",
                blockers=["external gate"],
            )
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, blocked_receipt, canonical_repo)

            # 3. Repository-head mismatch
            mismatched_repo = _make_repository(head="f" * 40)
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, canonical_result, mismatched_repo)

            # 4. Receipt commit mismatch
            commit_mismatch_receipt = dict(canonical_result, commit="9" * 40)
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, commit_mismatch_receipt, canonical_repo)

            # 5. Invalid receipt shape
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, {"bad": "receipt"}, canonical_repo)

            # 6. Dirty repository
            dirty_repo = dict(canonical_repo, status=" M scripts/two_site_codex_autopilot.py")
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(state_root, canonical_result, dirty_repo)

            # 7. Non-Path state_root
            with self.assertRaises(AutopilotError):
                publish_accepted_checkpoint(str(state_root), canonical_result, canonical_repo)

            # Verify that through all rejections, pre-existing bytes and metadata remained untouched
            self.assertEqual(checkpoint_path.read_bytes(), pre_existing_checkpoint_bytes)
            self.assertEqual(status_path.read_bytes(), pre_existing_status_bytes)
            self.assertEqual(checkpoint_path.stat().st_mtime_ns, initial_cp_stat.st_mtime_ns)
            self.assertEqual(status_path.stat().st_mtime_ns, initial_status_stat.st_mtime_ns)

            # Verify no temporary artifacts were left
            children = {p.name for p in state_root.iterdir()}
            self.assertEqual(children, {_ACCEPTED_CHECKPOINT_FILENAME, "status.json"})
            self.assertFalse(any(p.name.startswith(".accepted_checkpoint-") for p in state_root.iterdir()))
