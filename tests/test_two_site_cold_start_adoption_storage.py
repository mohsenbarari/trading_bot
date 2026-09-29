#!/usr/bin/env python3
"""Hermetic unit tests for dormant v1 terminal cold-adoption disk storage."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from scripts.two_site_assignment_scope import AssignmentScope
except ImportError:
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]

try:
    from scripts.two_site_codex_autopilot import (
        CONTROL_PATHS,
        PROTECTED_REFS,
        _load_accepted_checkpoint,
        rescope_epoch_basename,
        serialize_accepted_checkpoint,
        serialize_rescope_receipt,
        validate_accepted_repository,
    )
except ImportError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        CONTROL_PATHS,
        PROTECTED_REFS,
        _load_accepted_checkpoint,
        rescope_epoch_basename,
        serialize_accepted_checkpoint,
        serialize_rescope_receipt,
        validate_accepted_repository,
    )

try:
    import scripts.two_site_cold_start_adoption_storage as storage_mod
    from scripts.two_site_cold_start_adoption_storage import (
        ADOPTIONS_DIRNAME,
        ColdStartAdoptionStorageAmbiguousPostPublicationError,
        ColdStartAdoptionStorageError,
        MAX_TERMINAL_ADOPTION_RECORD_BYTES,
        publish_terminal_adoption_record_locked,
        read_terminal_adoption_record_locked,
    )
except ImportError:
    import two_site_cold_start_adoption_storage as storage_mod  # type: ignore[no-redef]
    from two_site_cold_start_adoption_storage import (  # type: ignore[no-redef]
        ADOPTIONS_DIRNAME,
        ColdStartAdoptionStorageAmbiguousPostPublicationError,
        ColdStartAdoptionStorageError,
        MAX_TERMINAL_ADOPTION_RECORD_BYTES,
        publish_terminal_adoption_record_locked,
        read_terminal_adoption_record_locked,
    )

HEAD_PRIOR = "0" * 40
HEAD_OLD = "a" * 40
HEAD_NEW = "1" * 40
ISSUED_AT = "2026-09-29T20:00:00Z"


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


def _valid_continue_result(head_before: str, head_after: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "continue checkpoint",
        "commit": head_after,
        "next_action": "continue",
        "blockers": [],
        "gemini_worker_run_id": "gw-1",
        "gemini_reviewer_run_id": "gr-1",
        "proposal_sha256": "e" * 64,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": True,
    }


class TestTwoSiteColdStartAdoptionStorage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="two-site-test-adoption-storage-")
        self.state_root = Path(self._tmp.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)

        live_repo = {
            "head": HEAD_OLD,
            "status": "",
            "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
            "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
            "assignment_scope": AssignmentScope(
                assignment_id="aid-test-old",
                stage_id="P1-00b",
                allowed_paths=("scripts/two_site_cold_start_epoch.py",),
                forbidden_paths=(),
                expires_at="2026-12-31T00:00:00Z",
                sha256="d" * 64,
            ),
        }
        self.old_repo = validate_accepted_repository(live_repo)
        self.old_epoch_basename = rescope_epoch_basename(self.old_repo)
        self.epoch_dir = self.state_root / self.old_epoch_basename
        self.epoch_dir.mkdir(mode=0o700)
        os.chmod(self.epoch_dir, 0o700)

        res1 = _valid_result(HEAD_OLD, "attempt 1")
        res2 = _valid_result(HEAD_OLD, "attempt 2")
        res3 = _valid_result(HEAD_OLD, "attempt 3")
        self._write_file(self.epoch_dir / "rescope_receipt.json", serialize_rescope_receipt(res1, self.old_repo, attempt=1))
        self._write_file(self.epoch_dir / "rescope_receipt_2.json", serialize_rescope_receipt(res2, self.old_repo, attempt=2))
        self._write_file(self.epoch_dir / "rescope_receipt_3.json", serialize_rescope_receipt(res3, self.old_repo, attempt=3, terminal=True))

        res_continue = _valid_continue_result(HEAD_PRIOR, HEAD_OLD)
        cp_bytes = serialize_accepted_checkpoint(res_continue, live_repo)
        self.checkpoint_file = self.state_root / "accepted_checkpoint.json"
        self._write_file(self.checkpoint_file, cp_bytes)
        loaded_cp = _load_accepted_checkpoint(self.state_root)
        self.assertIsInstance(loaded_cp, dict)

        self.new_repo = _valid_repo(HEAD_NEW, "aid-test-new", ["scripts/two_site_cold_start_adoption_storage.py"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_file(self, path: Path, payload: bytes) -> None:
        path.write_bytes(payload)
        os.chmod(path, 0o600)

    def test_positive_one_shot_publish_read_and_detachment(self) -> None:
        old_receipts_before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.epoch_dir.iterdir()}
        checkpoint_before = (self.checkpoint_file.read_bytes(), self.checkpoint_file.stat().st_mtime_ns)

        expected_basename = rescope_epoch_basename(self.new_repo)
        published_path = publish_terminal_adoption_record_locked(
            self.state_root,
            self.new_repo,
            self.old_epoch_basename,
            ISSUED_AT,
        )
        self.assertEqual(published_path, self.state_root / ADOPTIONS_DIRNAME / f"{expected_basename}.json")
        self.assertTrue(published_path.is_file())
        st = os.lstat(published_path)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertEqual(st.st_uid, os.geteuid())
        self.assertEqual(st.st_nlink, 1)
        self.assertLessEqual(st.st_size, MAX_TERMINAL_ADOPTION_RECORD_BYTES)

        adoptions_dir = self.state_root / ADOPTIONS_DIRNAME
        st_dir = os.lstat(adoptions_dir)
        self.assertEqual(st_dir.st_mode & 0o777, 0o700)
        self.assertEqual(st_dir.st_uid, os.geteuid())

        record = read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        self.assertIsNotNone(record)
        self.assertIsInstance(record, dict)
        self.assertEqual(record["old_epoch_basename"], self.old_epoch_basename)
        self.assertEqual(record["new_repository"], self.new_repo)
        self.assertEqual(record["old_repository_head"], HEAD_OLD)

        record["new_repository"]["head"] = "9" * 40
        record_fresh = read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        self.assertEqual(record_fresh["new_repository"]["head"], HEAD_NEW)

        old_receipts_after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.epoch_dir.iterdir()}
        self.assertEqual(old_receipts_before, old_receipts_after)
        checkpoint_after = (self.checkpoint_file.read_bytes(), self.checkpoint_file.stat().st_mtime_ns)
        self.assertEqual(checkpoint_before, checkpoint_after)

    def test_duplicate_publish_eexist_preserves_bytes_and_mtime(self) -> None:
        published_path = publish_terminal_adoption_record_locked(
            self.state_root,
            self.new_repo,
            self.old_epoch_basename,
            ISSUED_AT,
        )
        bytes_before = published_path.read_bytes()
        mtime_before = published_path.stat().st_mtime_ns

        with self.assertRaises(ColdStartAdoptionStorageError):
            publish_terminal_adoption_record_locked(
                self.state_root,
                self.new_repo,
                self.old_epoch_basename,
                ISSUED_AT,
            )

        self.assertEqual(published_path.read_bytes(), bytes_before)
        self.assertEqual(published_path.stat().st_mtime_ns, mtime_before)

    def test_checkpoint_loader_must_match_stable_raw_bytes(self) -> None:
        forged = copy.deepcopy(_load_accepted_checkpoint(self.state_root))
        self.assertIsInstance(forged, dict)
        forged["result"]["summary"] = "different valid checkpoint contents"
        with patch.object(storage_mod, "_load_accepted_checkpoint", return_value=forged):
            with self.assertRaises(ColdStartAdoptionStorageError):
                publish_terminal_adoption_record_locked(
                    self.state_root, self.new_repo, self.old_epoch_basename, ISSUED_AT
                )
        self.assertFalse((self.state_root / ADOPTIONS_DIRNAME).exists())

    def test_read_missing_adoption_target_returns_none(self) -> None:
        self.assertIsNone(read_terminal_adoption_record_locked(self.state_root, self.new_repo))
        (self.state_root / ADOPTIONS_DIRNAME).mkdir(mode=0o700)
        self.assertIsNone(read_terminal_adoption_record_locked(self.state_root, self.new_repo))

    def test_read_fails_closed_on_tampered_payload_links_and_modes(self) -> None:
        published_path = publish_terminal_adoption_record_locked(
            self.state_root,
            self.new_repo,
            self.old_epoch_basename,
            ISSUED_AT,
        )

        os.chmod(published_path, 0o644)
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        os.chmod(published_path, 0o600)

        hardlink_path = self.state_root / ADOPTIONS_DIRNAME / "hardlink.json"
        os.link(published_path, hardlink_path)
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        hardlink_path.unlink()

        orig_bytes = published_path.read_bytes()
        published_path.unlink()
        symlink_target = self.state_root / "real_adoption.json"
        symlink_target.write_bytes(orig_bytes)
        os.chmod(symlink_target, 0o600)
        published_path.symlink_to(symlink_target)
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        published_path.unlink()
        symlink_target.unlink()
        self._write_file(published_path, orig_bytes)

        self._write_file(published_path, orig_bytes + b"\n")
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)

        raw_dict = json.loads(orig_bytes.decode("utf-8"))
        raw_dict["approved"] = True
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self._write_file(published_path, encoder.encode(raw_dict).encode("utf-8"))
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)

        self._write_file(published_path, orig_bytes)
        stale_repo = copy.deepcopy(self.new_repo)
        stale_repo["head"] = "9" * 40
        # Repository identity changes the deterministic filename. A genuinely
        # absent file for that identity is not a malformed current record.
        self.assertIsNone(read_terminal_adoption_record_locked(self.state_root, stale_repo))

        self._write_file(self.checkpoint_file, b"{}")
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)

    def test_broken_symlink_or_bad_mode_adoptions_dir_fails_closed(self) -> None:
        adoptions_dir = self.state_root / ADOPTIONS_DIRNAME
        adoptions_dir.mkdir(mode=0o755)
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)
        adoptions_dir.rmdir()

        adoptions_dir.symlink_to(self.state_root / "nonexistent")
        with self.assertRaises(ColdStartAdoptionStorageError):
            read_terminal_adoption_record_locked(self.state_root, self.new_repo)

    def test_injected_post_link_failures_retain_final_record(self) -> None:
        target_name = f"{rescope_epoch_basename(self.new_repo)}.json"
        target_path = self.state_root / ADOPTIONS_DIRNAME / target_name

        real_fsync = os.fsync
        fsync_counts = {"count": 0}

        def faulty_fsync_first(fd: int) -> None:
            if target_path.exists():
                fsync_counts["count"] += 1
                if fsync_counts["count"] == 1:
                    raise OSError("Disk error during post-link first fsync")
            real_fsync(fd)

        with patch("os.fsync", side_effect=faulty_fsync_first):
            with self.assertRaises(ColdStartAdoptionStorageAmbiguousPostPublicationError):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        self.assertTrue(target_path.exists())
        st = os.lstat(target_path)
        self.assertEqual(st.st_mode & 0o777, 0o600)
        self.assertGreater(st.st_size, 0)

        target_path.unlink()
        for f in (self.state_root / ADOPTIONS_DIRNAME).iterdir():
            f.unlink()

        fsync_counts["count"] = 0

        def faulty_fsync_second(fd: int) -> None:
            if target_path.exists():
                fsync_counts["count"] += 1
                if fsync_counts["count"] == 2:
                    raise OSError("Disk error during post-link second fsync")
            real_fsync(fd)

        with patch("os.fsync", side_effect=faulty_fsync_second):
            with self.assertRaises(ColdStartAdoptionStorageAmbiguousPostPublicationError):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        self.assertTrue(target_path.exists())
        st2 = os.lstat(target_path)
        self.assertEqual(st2.st_mode & 0o777, 0o600)
        self.assertGreater(st2.st_size, 0)

    def test_post_link_directory_close_failure_retains_final_record(self) -> None:
        target_name = f"{rescope_epoch_basename(self.new_repo)}.json"
        adoptions_dir = self.state_root / ADOPTIONS_DIRNAME
        target_path = adoptions_dir / target_name
        real_close = os.close
        injected = {"value": False}

        def faulty_close(fd: int) -> None:
            if target_path.exists() and not injected["value"]:
                opened = os.fstat(fd)
                directory = os.stat(adoptions_dir)
                if (opened.st_dev, opened.st_ino) == (directory.st_dev, directory.st_ino):
                    injected["value"] = True
                    real_close(fd)
                    raise OSError("ambiguous close after publication")
            real_close(fd)

        with patch("os.close", side_effect=faulty_close):
            with self.assertRaises(ColdStartAdoptionStorageAmbiguousPostPublicationError):
                publish_terminal_adoption_record_locked(
                    self.state_root, self.new_repo, self.old_epoch_basename, ISSUED_AT
                )

        self.assertTrue(injected["value"])
        self.assertTrue(target_path.is_file())

    def test_post_link_drift_and_mutation_failure_retains_record(self) -> None:
        target_name = f"{rescope_epoch_basename(self.new_repo)}.json"
        target_path = self.state_root / ADOPTIONS_DIRNAME / target_name

        call_count = {"val": 0}
        orig_inspect = storage_mod.inspect_terminal_rescope_epoch

        def drifting_inspect(p: Path) -> dict[str, object]:
            call_count["val"] += 1
            res = orig_inspect(p)
            if call_count["val"] > 1:
                res["terminal_receipt_sha256"] = "9" * 64
            return res

        with patch.object(storage_mod, "inspect_terminal_rescope_epoch", side_effect=drifting_inspect):
            with self.assertRaises(ColdStartAdoptionStorageAmbiguousPostPublicationError):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        self.assertTrue(target_path.exists())

    def test_keyboard_interrupt_and_system_exit_propagation(self) -> None:
        with patch.object(storage_mod, "inspect_terminal_rescope_epoch", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        with patch.object(storage_mod, "inspect_terminal_rescope_epoch", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        target_name = f"{rescope_epoch_basename(self.new_repo)}.json"
        target_path = self.state_root / ADOPTIONS_DIRNAME / target_name
        real_fsync = os.fsync

        def interrupt_on_post_link_fsync(fd: int) -> None:
            if target_path.exists():
                raise KeyboardInterrupt("Interrupt after link")
            real_fsync(fd)

        with patch("os.fsync", side_effect=interrupt_on_post_link_fsync):
            with self.assertRaises(KeyboardInterrupt):
                publish_terminal_adoption_record_locked(
                    self.state_root,
                    self.new_repo,
                    self.old_epoch_basename,
                    ISSUED_AT,
                )

        self.assertTrue(target_path.exists())


if __name__ == "__main__":
    unittest.main()
