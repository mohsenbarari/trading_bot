"""Unit tests for dormant accepted lineage storage."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import copy
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_runner import (
    unique_json as authoritative_unique_json,
)
import scripts.two_site_quota_accepted_lineage_storage as storage_mod
from scripts.two_site_quota_accepted_lineage_storage import (
    ACCEPTED_LINEAGE_FILENAME,
    MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    AcceptedLineageStorageError,
    publish_accepted_lineage_record_candidate,
    read_accepted_lineage_record_candidate,
)


def _create_cycle_dir(root: Path, run_id: str) -> Path:
    target = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / run_id
    curr = root
    for part in (".local", "two-site-refactor", "autopilot", "runs", run_id):
        curr = curr / part
        curr.mkdir(mode=0o700, exist_ok=True)
        os.chmod(curr, 0o700)
    return target


def _make_fixtures(
    run_id: str = "cycle-0001-20260929T120000Z-123",
) -> tuple[dict, dict, dict, dict]:
    head_before = "a" * 40
    head_after = "b" * 40
    orig_run_id = "cycle-0002-20260929T110000Z-124"
    assignment_id = "asgn-12345"
    assignment_sha = "1" * 64
    proposal_sha = "e" * 64

    continue_expected = {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Cycle progressed to step 2",
        "commit": head_after,
        "next_action": "Proceed to next step",
        "blockers": [],
        "gemini_worker_run_id": "worker-run-1",
        "gemini_reviewer_run_id": "reviewer-run-1",
        "proposal_sha256": proposal_sha,
        "head_before": head_before,
        "head_after": head_after,
        "head_changed": True,
    }

    continue_raw = {
        k: continue_expected[k]
        for k in continue_expected
        if k not in {"head_before", "head_after", "head_changed"}
    }

    context = {
        "controller_run_id": run_id,
        "protected_refs": {
            "refs/heads/main": "c" * 40,
            "refs/remotes/origin/main": "d" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "e" * 40,
        },
        "assignment_id": assignment_id,
        "assignment_sha256": assignment_sha,
        "current_head": head_after,
    }

    upstream = {
        "original_controller_run_id": orig_run_id,
        "continuation_controller_run_id": run_id,
        "original_request_id": "0" * 32,
        "resume_request_id": "1" * 32,
        "task_id": "task-test-1",
        "assignment_id": assignment_id,
        "source_head": head_before,
        "task_sha256": "3" * 64,
        "assignment_sha256": assignment_sha,
        "semantic_task_fingerprint": "4" * 64,
        "original_wait_sha256": "5" * 64,
        "claim_sha256": "6" * 64,
        "request_sha256": "7" * 64,
        "submission_intent_sha256": "8" * 64,
        "terminal_receipt_sha256": "9" * 64,
        "response_sha256": "0" * 64,
        "response_state": "COMPLETED",
        "exit_code": 0,
        "expected_lineage_snapshot_sha256": "a" * 64,
    }

    return continue_expected, continue_raw, context, upstream


class TestAcceptedLineageStorage(unittest.TestCase):
    def test_positive_canonical_publication_and_readback(self) -> None:
        self.assertIs(storage_mod.unique_json, authoritative_unique_json)
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"

            raw_text = json.dumps(continue_raw, indent=2) + "\n"
            result_path.write_text(raw_text, encoding="utf-8")
            os.chmod(result_path, 0o644)

            # Positive publication under caller-held Supervisor lock
            published = publish_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )

            self.assertEqual(published["accepted_state"], "CONTINUE")
            self.assertEqual(published["controller_run_id"], run_id)
            self.assertEqual(
                published["result_sha256"],
                hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            )

            target_path = cycle_dir / ACCEPTED_LINEAGE_FILENAME
            self.assertTrue(target_path.exists())

            st = os.stat(target_path)
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_nlink, 1)
            self.assertEqual(st.st_uid, os.geteuid())
            self.assertGreater(st.st_size, 0)
            self.assertLessEqual(st.st_size, MAX_ACCEPTED_LINEAGE_RECORD_BYTES)

            # Verify file content is canonical JSON bytes
            target_bytes = target_path.read_bytes()
            canonical_expected = json.dumps(
                published,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            self.assertEqual(target_bytes, canonical_expected)

            # Verify temp files unlinked
            all_files = os.listdir(cycle_dir)
            self.assertIn("result.json", all_files)
            self.assertIn(ACCEPTED_LINEAGE_FILENAME, all_files)
            self.assertEqual(len([f for f in all_files if f.startswith(".")]), 0)

            # Readback
            readback = read_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )
            self.assertEqual(readback, published)

            # Detached deep copy
            readback["accepted_state"] = "MUTATED"
            readback_second = read_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )
            self.assertEqual(readback_second["accepted_state"], "CONTINUE")

    def test_second_publication_collision_preserves_target_bytes(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(continue_raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            publish_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )

            target_path = cycle_dir / ACCEPTED_LINEAGE_FILENAME
            first_bytes = target_path.read_bytes()
            first_st = os.stat(target_path)

            # Second publication must fail closed without overwrite or unlink
            with self.assertRaises(AcceptedLineageStorageError) as ctx_err:
                publish_accepted_lineage_record_candidate(
                    root=root,
                    continuation_controller_run_id=run_id,
                    expected_transition_result=continue_exp,
                    acceptance_context=context,
                    upstream=upstream,
                )
            self.assertIn("already exists", str(ctx_err.exception))

            # Existing target bytes and identity are preserved
            self.assertEqual(target_path.read_bytes(), first_bytes)
            second_st = os.stat(target_path)
            self.assertEqual(first_st.st_ino, second_st.st_ino)
            self.assertEqual(first_st.st_mtime_ns, second_st.st_mtime_ns)

    def test_readback_mode_or_corruption_fails_closed(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(continue_raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            publish_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )
            target_path = cycle_dir / ACCEPTED_LINEAGE_FILENAME

            # 1. Mode 0644 rejected (must be exact 0600)
            os.chmod(target_path, 0o644)
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(
                    root, run_id, continue_exp, context, upstream
                )
            os.chmod(target_path, 0o600)

            # 2. Non-canonical JSON formatting rejected
            original_content = target_path.read_text(encoding="utf-8")
            reformatted = original_content.replace(",", ", ")
            target_path.write_text(reformatted, encoding="utf-8")
            os.chmod(target_path, 0o600)
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(
                    root, run_id, continue_exp, context, upstream
                )

            # 3. Mismatched result_sha256 rejected
            corrupted_data = json.loads(original_content)
            corrupted_data["result_sha256"] = "f" * 64
            corrupted_bytes = json.dumps(
                corrupted_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            target_path.write_bytes(corrupted_bytes)
            os.chmod(target_path, 0o600)
            with self.assertRaises(AcceptedLineageStorageError):
                read_accepted_lineage_record_candidate(
                    root, run_id, continue_exp, context, upstream
                )

    def test_readback_close_descriptor_failure_fails_closed(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(continue_raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            publish_accepted_lineage_record_candidate(
                root=root,
                continuation_controller_run_id=run_id,
                expected_transition_result=continue_exp,
                acceptance_context=context,
                upstream=upstream,
            )
            target_path = cycle_dir / ACCEPTED_LINEAGE_FILENAME
            expected_bytes = target_path.read_bytes()
            expected_st = os.stat(target_path)
            target_ino = expected_st.st_ino

            real_close = os.close

            def _fake_close_target(fd: int) -> None:
                try:
                    is_target = os.fstat(fd).st_ino == target_ino
                except OSError:
                    is_target = False
                real_close(fd)
                if is_target:
                    raise OSError(5, "Simulated close error on accepted lineage file descriptor")

            with patch("scripts.two_site_quota_accepted_lineage_storage.os.close", side_effect=_fake_close_target):
                with self.assertRaises(AcceptedLineageStorageError) as ctx_err:
                    read_accepted_lineage_record_candidate(
                        root=root,
                        continuation_controller_run_id=run_id,
                        expected_transition_result=continue_exp,
                        acceptance_context=context,
                        upstream=upstream,
                    )
                self.assertIn("cannot safely close", str(ctx_err.exception))

            # Target file was not mutated or unlinked
            self.assertTrue(target_path.exists())
            self.assertEqual(target_path.read_bytes(), expected_bytes)
            current_st = os.stat(target_path)
            self.assertEqual(current_st.st_ino, expected_st.st_ino)
            self.assertEqual(current_st.st_mtime_ns, expected_st.st_mtime_ns)
            self.assertEqual(current_st.st_mode, expected_st.st_mode)

    def test_publisher_temp_close_failure_cleans_temp_and_never_links(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(continue_raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            real_close = os.close

            def _fake_close_temp(fd: int) -> None:
                try:
                    st = os.fstat(fd)
                    is_temp = stat.S_ISREG(st.st_mode) and (stat.S_IMODE(st.st_mode) == 0o600)
                except OSError:
                    is_temp = False
                real_close(fd)
                if is_temp:
                    raise OSError(5, "Simulated close error on temp file descriptor")

            with patch("scripts.two_site_quota_accepted_lineage_storage.os.close", side_effect=_fake_close_temp):
                with self.assertRaises(AcceptedLineageStorageError) as ctx_err:
                    publish_accepted_lineage_record_candidate(
                        root=root,
                        continuation_controller_run_id=run_id,
                        expected_transition_result=continue_exp,
                        acceptance_context=context,
                        upstream=upstream,
                    )
                self.assertIn("cannot safely close temp file", str(ctx_err.exception))

            target_path = cycle_dir / ACCEPTED_LINEAGE_FILENAME
            self.assertFalse(target_path.exists())
            # Temp file was cleaned up
            entries = os.listdir(cycle_dir)
            self.assertEqual([f for f in entries if f.startswith(".")], [])
            self.assertIn("result.json", entries)

    def test_dormant_no_live_dispatch_or_wake(self) -> None:
        run_id = "cycle-0001-20260929T120000Z-123"
        continue_exp, continue_raw, context, upstream = _make_fixtures(run_id)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cycle_dir = _create_cycle_dir(root, run_id)
            result_path = cycle_dir / "result.json"
            result_path.write_text(json.dumps(continue_raw), encoding="utf-8")
            os.chmod(result_path, 0o644)

            # Patch subprocess and socket to verify zero live authority
            with patch("subprocess.run", side_effect=AssertionError("subprocess.run forbidden")), \
                 patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen forbidden")), \
                 patch("socket.socket", side_effect=AssertionError("socket forbidden")):
                pub = publish_accepted_lineage_record_candidate(
                    root, run_id, continue_exp, context, upstream
                )
                self.assertEqual(pub["controller_run_id"], run_id)
                rec = read_accepted_lineage_record_candidate(
                    root, run_id, continue_exp, context, upstream
                )
                self.assertEqual(rec["controller_run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
