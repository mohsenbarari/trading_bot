#!/usr/bin/env python3
"""Hermetic unit tests for dormant v1 terminal cold-adoption record codec."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    rescope_epoch_basename,
    serialize_rescope_receipt,
)
from scripts.two_site_cold_start_adoption_record import (
    ColdStartAdoptionRecordError,
    MAX_TERMINAL_ADOPTION_RECORD_BYTES,
    TERMINAL_ADOPTION_KIND,
    TERMINAL_ADOPTION_SCHEMA_VERSION,
    build_terminal_adoption_record,
    parse_terminal_adoption_record,
    serialize_terminal_adoption_record,
    validate_terminal_adoption_record,
)
from scripts.two_site_cold_start_epoch import inspect_terminal_rescope_epoch

HEAD_OLD = "a" * 40
HEAD_NEW = "1" * 40
CHECKPOINT_SHA = "f" * 64
ISSUED_AT = "2026-09-29T19:30:00Z"


def _valid_repo(head: str, aid: str, allowed_paths: list[str]) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": aid,
            "stage_id": "P1-00b",
            "sha256": "d" * 64,
            "expires_at": "2026-12-31T00:00:00Z",
            "allowed_paths": allowed_paths,
            "forbidden_paths": [],
        },
    }


def _valid_result(head: str, summary: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": summary,
        "commit": None,
        "next_action": "manual audit",
        "blockers": ["rejection"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


class TestTwoSiteColdStartAdoptionRecord(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="two-site-test-adoption-")
        self.state_root = Path(self._tmp.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)

        self.old_repo = _valid_repo(HEAD_OLD, "aid-test-old", ["scripts/two_site_cold_start_epoch.py"])
        self.basename = rescope_epoch_basename(self.old_repo)
        self.epoch_dir = self.state_root / self.basename
        self.epoch_dir.mkdir(mode=0o700)
        os.chmod(self.epoch_dir, 0o700)

        res1 = _valid_result(HEAD_OLD, "attempt 1")
        res2 = _valid_result(HEAD_OLD, "attempt 2")
        res3 = _valid_result(HEAD_OLD, "attempt 3")
        self._write_receipt("rescope_receipt.json", serialize_rescope_receipt(res1, self.old_repo, attempt=1))
        self._write_receipt("rescope_receipt_2.json", serialize_rescope_receipt(res2, self.old_repo, attempt=2))
        self._write_receipt("rescope_receipt_3.json", serialize_rescope_receipt(res3, self.old_repo, attempt=3, terminal=True))

        self.terminal_audit = inspect_terminal_rescope_epoch(self.epoch_dir)
        self.new_repo = _valid_repo(HEAD_NEW, "aid-test-new", ["scripts/two_site_cold_start_adoption_record.py"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_receipt(self, name: str, payload: bytes) -> None:
        path = self.epoch_dir / name
        path.write_bytes(payload)
        os.chmod(path, 0o600)

    def test_roundtrip_bytes_detachment_and_no_io_side_effects(self) -> None:
        parent_snapshot = set(os.listdir(self.state_root))
        epoch_snapshot = {
            p.name: (p.read_bytes(), p.stat(follow_symlinks=False).st_mtime_ns)
            for p in self.epoch_dir.iterdir()
        }
        repo_snapshot = copy.deepcopy(self.new_repo)
        audit_snapshot = copy.deepcopy(self.terminal_audit)

        record = build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)
        self.assertEqual(record["schema_version"], TERMINAL_ADOPTION_SCHEMA_VERSION)
        self.assertEqual(record["kind"], TERMINAL_ADOPTION_KIND)
        self.assertEqual(record["old_epoch_basename"], self.basename)
        self.assertEqual(record["old_repository_head"], HEAD_OLD)
        self.assertEqual(record["old_terminal_receipt_sha256"], self.terminal_audit["terminal_receipt_sha256"])
        self.assertEqual(record["prior_checkpoint_sha256"], CHECKPOINT_SHA)
        self.assertEqual(record["issued_at_utc"], ISSUED_AT)
        self.assertEqual(record["new_repository"], self.new_repo)

        raw_bytes = serialize_terminal_adoption_record(record, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)
        self.assertLessEqual(len(raw_bytes), MAX_TERMINAL_ADOPTION_RECORD_BYTES)

        validated = validate_terminal_adoption_record(record, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)
        self.assertEqual(validated, record)

        parsed = parse_terminal_adoption_record(raw_bytes, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)
        self.assertEqual(parsed, record)
        self.assertEqual(serialize_terminal_adoption_record(parsed, self.new_repo, self.terminal_audit, CHECKPOINT_SHA), raw_bytes)

        # Assert detached deep copies
        parsed["new_repository"]["head"] = "9" * 40
        self.assertEqual(self.new_repo["head"], HEAD_NEW)
        self.assertEqual(self.new_repo, repo_snapshot)
        self.assertEqual(self.terminal_audit, audit_snapshot)

        # Assert pure execution with zero disk writes, identical file bytes and unmodified mtimes
        self.assertEqual(set(os.listdir(self.state_root)), parent_snapshot)
        current_epoch_files = set(p.name for p in self.epoch_dir.iterdir())
        self.assertEqual(current_epoch_files, set(epoch_snapshot.keys()))
        for name, (orig_bytes, orig_mtime) in epoch_snapshot.items():
            leaf = self.epoch_dir / name
            self.assertEqual(leaf.read_bytes(), orig_bytes)
            self.assertEqual(leaf.stat(follow_symlinks=False).st_mtime_ns, orig_mtime)

    def test_cross_binding_rejection_in_validate_and_parse(self) -> None:
        valid_rec = build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)
        valid_raw = serialize_terminal_adoption_record(valid_rec, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

        # 1. Altered new_repository head
        diff_head_repo = _valid_repo("9" * 40, "aid-test-new", ["scripts/two_site_cold_start_adoption_record.py"])
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, diff_head_repo, self.terminal_audit, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, diff_head_repo, self.terminal_audit, CHECKPOINT_SHA)

        # 2. Altered new_repository assignment SHA
        diff_sha_repo = copy.deepcopy(self.new_repo)
        diff_sha_repo["assignment_scope"]["sha256"] = "9" * 64
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, diff_sha_repo, self.terminal_audit, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, diff_sha_repo, self.terminal_audit, CHECKPOINT_SHA)

        # 3. Altered new_repository controls
        diff_ctrl_repo = copy.deepcopy(self.new_repo)
        first_ctrl = next(iter(diff_ctrl_repo["control_blobs"]))
        diff_ctrl_repo["control_blobs"][first_ctrl] = "9" * 40
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, diff_ctrl_repo, self.terminal_audit, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, diff_ctrl_repo, self.terminal_audit, CHECKPOINT_SHA)

        # 4. Altered prior_checkpoint_sha256
        diff_cp_sha = "9" * 64
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, self.new_repo, self.terminal_audit, diff_cp_sha)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, self.new_repo, self.terminal_audit, diff_cp_sha)

        # 5. Altered old audit terminal_receipt_sha256
        diff_receipt_audit = copy.deepcopy(self.terminal_audit)
        diff_receipt_audit["terminal_receipt_sha256"] = "9" * 64
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, self.new_repo, diff_receipt_audit, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, self.new_repo, diff_receipt_audit, CHECKPOINT_SHA)

        # 6. Altered old audit head
        diff_audit_head = copy.deepcopy(self.terminal_audit)
        diff_audit_head["repository"]["head"] = "9" * 40
        diff_audit_head["epoch_basename"] = rescope_epoch_basename(diff_audit_head["repository"])
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, self.new_repo, diff_audit_head, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, self.new_repo, diff_audit_head, CHECKPOINT_SHA)

        # 7. Altered old audit epoch_basename
        diff_audit_base = copy.deepcopy(self.terminal_audit)
        diff_audit_base["epoch_basename"] = "mismatched_basename"
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(valid_rec, self.new_repo, diff_audit_base, CHECKPOINT_SHA)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(valid_raw, self.new_repo, diff_audit_base, CHECKPOINT_SHA)

    def test_reject_matching_head_or_matching_assignment_id(self) -> None:
        same_head_repo = _valid_repo(HEAD_OLD, "aid-test-new", ["scripts/two_site_cold_start_adoption_record.py"])
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(same_head_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

        same_aid_repo = _valid_repo(HEAD_NEW, "aid-test-old", ["scripts/two_site_cold_start_adoption_record.py"])
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(same_aid_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

    def test_reject_mismatched_protected_refs(self) -> None:
        tampered_refs_repo = copy.deepcopy(self.new_repo)
        ref = next(iter(PROTECTED_REFS))
        tampered_refs_repo["protected_refs"][ref] = "9" * 40
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(tampered_refs_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

    def test_reject_overlapping_or_illegal_allowed_paths(self) -> None:
        overlap_repo = _valid_repo(HEAD_NEW, "aid-test-new", ["scripts/two_site_cold_start_epoch.py"])
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(overlap_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

        illegal_cases = [
            [],
            [f"scripts/file_{i}.py" for i in range(257)],
            [".git"],
            [".git/config"],
            ["scripts/.git/file.py"],
            [".GIT/file.py"],
            ["scripts/file.py/.git"],
            ["C:file.py"],
            ["c:/scripts/file.py"],
            ["D:/file.py"],
            ["scripts/c:file.py"],
            ["file.py:stream"],
            ["scripts/*.py"],
            ["scripts/?.py"],
            ["scripts/[a-z].py"],
            ["scripts/{a,b}.py"],
            ["scripts/../scripts/file.py"],
            ["scripts/./file.py"],
            ["/scripts/file.py"],
            ["scripts//file.py"],
            ["scripts\\file.py"],
            ["scripts/file.py/"],
            ["scripts/file\x00.py"],
            ["scripts/file\n.py"],
            [" scripts/file.py"],
            ["scripts/file.py "],
            [""],
            ["a.py", "a.py"],
            [123],
        ]
        for bad_paths in illegal_cases:
            bad_repo = _valid_repo(HEAD_NEW, "aid-test-new", bad_paths)  # type: ignore[arg-type]
            with self.assertRaises(ColdStartAdoptionRecordError):
                build_terminal_adoption_record(bad_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

    def test_reject_invalid_issued_at_utc(self) -> None:
        bad_timestamps = [
            "2026-09-29T19:30:00",
            "2026-09-29 19:30:00Z",
            "2026-09-29T19:30:00.000Z",
            "2026-02-30T19:30:00Z",
            1234567890,
            None,
        ]
        for bad_ts in bad_timestamps:
            with self.assertRaises(ColdStartAdoptionRecordError):
                build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, bad_ts)  # type: ignore[arg-type]

    def test_reject_malformed_checkpoint_sha_or_audit(self) -> None:
        for bad_sha in ["f" * 63, "f" * 65, "F" * 64, "g" * 64, 12345]:
            with self.assertRaises(ColdStartAdoptionRecordError):
                build_terminal_adoption_record(self.new_repo, self.terminal_audit, bad_sha, ISSUED_AT)  # type: ignore[arg-type]

        bad_audit_sha = copy.deepcopy(self.terminal_audit)
        bad_audit_sha["terminal_receipt_sha256"] = "not_a_valid_hex_sha"
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(self.new_repo, bad_audit_sha, CHECKPOINT_SHA, ISSUED_AT)

        bad_audit_keys = {"epoch_basename": self.basename}
        with self.assertRaises(ColdStartAdoptionRecordError):
            build_terminal_adoption_record(self.new_repo, bad_audit_keys, CHECKPOINT_SHA, ISSUED_AT)

    def test_reject_extra_keys_disallowed_fields_or_wrong_kind(self) -> None:
        valid_rec = build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)

        extra = copy.deepcopy(valid_rec)
        extra["issuer"] = "Codex"
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(extra, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

        approved = copy.deepcopy(valid_rec)
        approved["approved"] = True
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(approved, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

        wrong_kind = copy.deepcopy(valid_rec)
        wrong_kind["kind"] = "NOT_A_COLD_ADOPTION"
        with self.assertRaises(ColdStartAdoptionRecordError):
            validate_terminal_adoption_record(wrong_kind, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

    def test_parser_rejects_duplicate_key_with_mutation_assertion(self) -> None:
        valid_rec = build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)
        raw_bytes = serialize_terminal_adoption_record(valid_rec, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)
        raw_text = raw_bytes.decode("utf-8")

        target_token = '"kind":"TERMINAL_EPOCH_COLD_ADOPTION"'
        self.assertEqual(raw_text.count(target_token), 1)
        dup_text = raw_text.replace(target_token, f"{target_token},{target_token}", 1)
        self.assertEqual(dup_text.count(target_token), 2)
        self.assertNotEqual(dup_text, raw_text)

        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(dup_text.encode("utf-8"), self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

    def test_parser_rejects_noncanonical_oversized_and_non_bytes(self) -> None:
        valid_rec = build_terminal_adoption_record(self.new_repo, self.terminal_audit, CHECKPOINT_SHA, ISSUED_AT)
        raw_bytes = serialize_terminal_adoption_record(valid_rec, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(raw_bytes.decode("utf-8"), self.new_repo, self.terminal_audit, CHECKPOINT_SHA)  # type: ignore[arg-type]

        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(raw_bytes + b"\n", self.new_repo, self.terminal_audit, CHECKPOINT_SHA)

        oversized = b" " * (MAX_TERMINAL_ADOPTION_RECORD_BYTES + 1)
        with self.assertRaises(ColdStartAdoptionRecordError):
            parse_terminal_adoption_record(oversized, self.new_repo, self.terminal_audit, CHECKPOINT_SHA)


if __name__ == "__main__":
    unittest.main()
