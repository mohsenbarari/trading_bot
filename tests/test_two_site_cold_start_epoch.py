#!/usr/bin/env python3
"""Hermetic unit tests for read-only terminal old-epoch audit primitive."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import copy
import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    MAX_RESCOPE_RECEIPT_BYTES,
    PROTECTED_REFS,
    rescope_epoch_basename,
    serialize_rescope_receipt,
)
from scripts.two_site_cold_start_epoch import (
    ColdStartEpochError,
    inspect_terminal_rescope_epoch,
)

HEAD = "a" * 40


def _valid_repo(head: str = HEAD) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "aid-test-1",
            "stage_id": "P1-00b",
            "sha256": "d" * 64,
            "expires_at": "2026-12-31T00:00:00Z",
            "allowed_paths": ["scripts/two_site_cold_start_epoch.py"],
            "forbidden_paths": [],
        },
    }


def _valid_result(head: str = HEAD, summary: str = "terminal rescope") -> dict[str, object]:
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


class TestTwoSiteColdStartEpoch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="two-site-test-cold-start-")
        self.state_root = Path(self._tmp.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)
        self.repo = _valid_repo()
        self.res1 = _valid_result(summary="attempt 1")
        self.res2 = _valid_result(summary="attempt 2")
        self.res3 = _valid_result(summary="attempt 3")
        self.basename = rescope_epoch_basename(self.repo)
        self.epoch_dir = self.state_root / self.basename
        self.epoch_dir.mkdir(mode=0o700)
        os.chmod(self.epoch_dir, 0o700)
        self._write_receipts_1_2_3()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_leaf(self, name: str, payload: bytes, mode: int = 0o600) -> Path:
        path = self.epoch_dir / name
        path.write_bytes(payload)
        os.chmod(path, mode)
        return path

    def _write_receipts_1_2_3(self) -> None:
        p1 = serialize_rescope_receipt(self.res1, self.repo, attempt=1)
        p2 = serialize_rescope_receipt(self.res2, self.repo, attempt=2)
        p3 = serialize_rescope_receipt(self.res3, self.repo, attempt=3, terminal=True)
        self._write_leaf("rescope_receipt.json", p1)
        self._write_leaf("rescope_receipt_2.json", p2)
        self._write_leaf("rescope_receipt_3.json", p3)

    def test_positive_inspection_exact_keys_and_detachment(self) -> None:
        expected_payload_3 = serialize_rescope_receipt(self.res3, self.repo, attempt=3, terminal=True)
        expected_sha = hashlib.sha256(expected_payload_3).hexdigest()

        res = inspect_terminal_rescope_epoch(self.epoch_dir)
        self.assertEqual(set(res.keys()), {"epoch_basename", "repository", "terminal_receipt_sha256"})
        self.assertEqual(res["epoch_basename"], self.basename)
        self.assertEqual(res["terminal_receipt_sha256"], expected_sha)
        self.assertEqual(res["repository"], self.repo)

        # Assert repository returned is a deep detached copy
        res["repository"]["head"] = "z" * 40
        self.assertEqual(self.repo["head"], HEAD)
        res2 = inspect_terminal_rescope_epoch(self.epoch_dir)
        self.assertEqual(res2["repository"]["head"], HEAD)

    def test_positive_inspection_preserves_bytes_and_mtime_without_new_files(self) -> None:
        epoch_snapshot = {
            p.name: (p.read_bytes(), p.stat(follow_symlinks=False).st_mtime_ns)
            for p in self.epoch_dir.iterdir()
        }
        parent_snapshot = set(os.listdir(self.state_root))

        inspect_terminal_rescope_epoch(self.epoch_dir)

        self.assertEqual(set(os.listdir(self.state_root)), parent_snapshot)
        current_epoch_files = set(p.name for p in self.epoch_dir.iterdir())
        self.assertEqual(current_epoch_files, set(epoch_snapshot.keys()))
        for name, (orig_bytes, orig_mtime) in epoch_snapshot.items():
            leaf = self.epoch_dir / name
            self.assertEqual(leaf.read_bytes(), orig_bytes)
            self.assertEqual(leaf.stat(follow_symlinks=False).st_mtime_ns, orig_mtime)

    def test_reject_non_path_or_non_absolute_or_noncanonical_or_symlinked_dir(self) -> None:
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(str(self.epoch_dir))  # type: ignore[arg-type]

        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(Path(self.basename))

        sub_dir = self.epoch_dir / "subdir"
        sub_dir.mkdir(mode=0o700)
        non_canonical = sub_dir / ".."
        self.assertEqual(non_canonical.resolve(strict=True), self.epoch_dir)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(non_canonical)
        sub_dir.rmdir()

        sym_dir = self.state_root / "symlink_epoch_dir"
        os.symlink(self.epoch_dir, sym_dir)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(sym_dir)
        sym_dir.unlink()

    def test_reject_unsafe_directory_mode(self) -> None:
        os.chmod(self.epoch_dir, 0o755)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        os.chmod(self.epoch_dir, 0o700)

    def test_reject_unsafe_directory_owner(self) -> None:
        with patch("os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(ColdStartEpochError):
                inspect_terminal_rescope_epoch(self.epoch_dir)

    def test_reject_missing_terminal_receipt(self) -> None:
        (self.epoch_dir / "rescope_receipt_3.json").unlink()
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        self._write_receipts_1_2_3()

    def test_reject_nonterminal_or_wrong_attempt_receipt(self) -> None:
        valid_p3 = serialize_rescope_receipt(self.res3, self.repo, attempt=3, terminal=True)
        valid_dict = json.loads(valid_p3.decode("utf-8"))
        valid_dict["terminal"] = False
        self._write_leaf("rescope_receipt_3.json", json.dumps(valid_dict).encode("utf-8"))
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)

        attempt2_payload = serialize_rescope_receipt(self.res2, self.repo, attempt=2)
        self._write_leaf("rescope_receipt_3.json", attempt2_payload)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        self._write_receipts_1_2_3()

    def test_reject_noncontiguous_receipt_chain(self) -> None:
        (self.epoch_dir / "rescope_receipt_2.json").unlink()
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)

        self._write_receipts_1_2_3()
        (self.epoch_dir / "rescope_receipt.json").unlink()
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        self._write_receipts_1_2_3()

    def test_reject_symlink_or_hardlink_or_wrong_mode_receipt(self) -> None:
        leaf3 = self.epoch_dir / "rescope_receipt_3.json"
        valid_bytes = leaf3.read_bytes()
        leaf3.unlink()

        target = self.state_root / "external_target.json"
        target.write_bytes(valid_bytes)
        os.chmod(target, 0o600)
        os.symlink(target, leaf3)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        leaf3.unlink()
        target.unlink()

        self._write_leaf("rescope_receipt_3.json", valid_bytes)
        hardlink = self.epoch_dir / "extra_hardlink.json"
        os.link(leaf3, hardlink)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        hardlink.unlink()

        os.chmod(leaf3, 0o644)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        os.chmod(leaf3, 0o600)

    def test_reject_tampered_json_duplicate_keys_or_payload_facts(self) -> None:
        leaf3 = self.epoch_dir / "rescope_receipt_3.json"
        valid_p3 = serialize_rescope_receipt(self.res3, self.repo, attempt=3, terminal=True)
        raw = valid_p3.decode("utf-8")
        target_token = '"attempt": 3,'
        self.assertEqual(raw.count(target_token), 1)
        dup_json = raw.replace(target_token, f"{target_token} {target_token}", 1)
        self.assertEqual(dup_json.count(target_token), 2)
        self.assertNotEqual(dup_json, raw)
        self._write_leaf("rescope_receipt_3.json", dup_json.encode("utf-8"))
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)

        tampered_head = "f" * 40
        tampered_repo = _valid_repo(head=tampered_head)
        tampered_res3 = _valid_result(head=tampered_head, summary="tampered attempt 3")
        tampered_payload = serialize_rescope_receipt(tampered_res3, tampered_repo, attempt=3, terminal=True)
        self._write_leaf("rescope_receipt_3.json", tampered_payload)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)

        self._write_leaf("rescope_receipt_3.json", b"NOT_VALID_JSON{")
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        self._write_receipts_1_2_3()

    def test_reject_mismatched_epoch_name(self) -> None:
        other_repo = _valid_repo(head="e" * 40)
        other_basename = rescope_epoch_basename(other_repo)
        wrong_dir = self.state_root / other_basename
        self.epoch_dir.rename(wrong_dir)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(wrong_dir)
        wrong_dir.rename(self.epoch_dir)

    def test_reject_oversized_terminal_file(self) -> None:
        oversized = b" " * (MAX_RESCOPE_RECEIPT_BYTES + 1)
        self._write_leaf("rescope_receipt_3.json", oversized)
        with self.assertRaises(ColdStartEpochError):
            inspect_terminal_rescope_epoch(self.epoch_dir)
        self._write_receipts_1_2_3()

    def test_double_read_byte_change_detected(self) -> None:
        from scripts import two_site_cold_start_epoch

        real_read = two_site_cold_start_epoch.bounded_file_bytes
        call_count = 0

        def drifting_read(root_dir: Path, target_path: Path, max_bytes: int, label: str) -> bytes:
            nonlocal call_count
            call_count += 1
            data = real_read(root_dir, target_path, max_bytes, label)
            if call_count == 2:
                return data + b" "
            return data

        with patch.object(two_site_cold_start_epoch, "bounded_file_bytes", side_effect=drifting_read):
            with self.assertRaises(ColdStartEpochError):
                inspect_terminal_rescope_epoch(self.epoch_dir)

    def test_interrupts_propagate_bare(self) -> None:
        from scripts import two_site_cold_start_epoch

        with patch.object(
            two_site_cold_start_epoch,
            "bounded_file_bytes",
            side_effect=KeyboardInterrupt("simulated interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                inspect_terminal_rescope_epoch(self.epoch_dir)

        with patch.object(
            two_site_cold_start_epoch,
            "bounded_file_bytes",
            side_effect=SystemExit(2),
        ):
            with self.assertRaises(SystemExit):
                inspect_terminal_rescope_epoch(self.epoch_dir)


if __name__ == "__main__":
    unittest.main()
