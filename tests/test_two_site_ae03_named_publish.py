#!/usr/bin/env python3
"""Hermetic unit tests for named rescope receipt publishing core and attempts 2/3."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    _RESCOPE_RECEIPT_FILENAME,
    _RESCOPE_RECEIPT_FILENAMES,
    AutopilotError,
    _publish_rescope_payload_noreplace,
    publish_initial_rescope_receipt,
    load_rescope_receipt,
    serialize_rescope_receipt,
)

HEAD_SHA = "a" * 40
CONTROL_BLOB_SHA = "b" * 40


def make_test_repository() -> dict[str, object]:
    return {
        "head": HEAD_SHA,
        "status": "",
        "protected_refs": {ref: HEAD_SHA for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): CONTROL_BLOB_SHA for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "test-scope-001",
            "stage_id": "P1-00b",
            "sha256": "c" * 64,
            "expires_at": "2026-12-31T00:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


def make_test_result() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "Rescope receipt required",
        "commit": None,
        "next_action": "Rescope current assignment",
        "blockers": ["Unit exceeds assignment scope"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": HEAD_SHA,
        "head_after": HEAD_SHA,
        "head_changed": False,
    }


class TestTwoSiteAe03NamedPublish(unittest.TestCase):
    def _create_state_root(self, parent: str) -> Path:
        state_root = Path(parent) / "state_root"
        state_root.mkdir(mode=0o700)
        os.chmod(state_root, 0o700)
        return state_root

    def test_publish_initial_rescope_receipt_wrapper_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()

            publish_initial_rescope_receipt(state_root, result, repo)

            target = state_root / _RESCOPE_RECEIPT_FILENAME
            self.assertTrue(target.is_file())
            st = target.stat(follow_symlinks=False)
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_nlink, 1)

            loaded = load_rescope_receipt(state_root, repo)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["attempt"], 1)
            self.assertFalse(loaded["terminal"])

    def test_publish_attempt_2_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()
            payload2 = serialize_rescope_receipt(result, repo, attempt=2)

            _publish_rescope_payload_noreplace(
                state_root, payload2, repo, "rescope_receipt_2.json"
            )

            target = state_root / "rescope_receipt_2.json"
            self.assertTrue(target.is_file())
            st = target.stat(follow_symlinks=False)
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_nlink, 1)

            temp_files = [p for p in state_root.iterdir() if p.name.startswith(".rescope_receipt_tmp_")]
            self.assertEqual(temp_files, [])

            self.assertFalse((state_root / _RESCOPE_RECEIPT_FILENAME).exists())
            self.assertFalse((state_root / "rescope_receipt_3.json").exists())

            loaded = load_rescope_receipt(state_root, repo, "rescope_receipt_2.json")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["attempt"], 2)
            self.assertFalse(loaded["terminal"])

    def test_publish_attempt_3_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()
            payload3 = serialize_rescope_receipt(result, repo, attempt=3)

            _publish_rescope_payload_noreplace(
                state_root, payload3, repo, "rescope_receipt_3.json"
            )

            target = state_root / "rescope_receipt_3.json"
            self.assertTrue(target.is_file())
            st = target.stat(follow_symlinks=False)
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
            self.assertEqual(st.st_nlink, 1)

            temp_files = [p for p in state_root.iterdir() if p.name.startswith(".rescope_receipt_tmp_")]
            self.assertEqual(temp_files, [])

            self.assertFalse((state_root / _RESCOPE_RECEIPT_FILENAME).exists())
            self.assertFalse((state_root / "rescope_receipt_2.json").exists())

            loaded = load_rescope_receipt(state_root, repo, "rescope_receipt_3.json")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["attempt"], 3)
            self.assertTrue(loaded["terminal"])

    def test_collision_preserves_existing_bytes_attempts_2_and_3(self) -> None:
        for attempt, filename in [(2, "rescope_receipt_2.json"), (3, "rescope_receipt_3.json")]:
            with tempfile.TemporaryDirectory() as tmpdir:
                state_root = self._create_state_root(tmpdir)
                repo = make_test_repository()
                result = make_test_result()
                payload = serialize_rescope_receipt(result, repo, attempt=attempt)

                target = state_root / filename
                initial_bytes = f"existing target {attempt} content".encode("utf-8")
                target.write_bytes(initial_bytes)
                os.chmod(target, 0o600)

                with self.assertRaises(AutopilotError) as ctx:
                    _publish_rescope_payload_noreplace(
                        state_root, payload, repo, filename
                    )
                self.assertIn("target receipt already exists", str(ctx.exception))
                self.assertEqual(target.read_bytes(), initial_bytes)
                self.assertEqual(stat.S_IMODE(target.stat(follow_symlinks=False).st_mode), 0o600)

    def test_invalid_name_and_mismatched_payload_reject_before_io(self) -> None:
        repo = make_test_repository()
        result = make_test_result()
        payload2 = serialize_rescope_receipt(result, repo, attempt=2)
        state_root = Path("/nonexistent/dummy/path")

        with patch("scripts.two_site_codex_autopilot._open_private_rescope_state_dir") as mock_open:
            for bad_name in ["rescope_receipt_4.json", "invalid.json", "", 123]:
                with self.assertRaises(AutopilotError):
                    _publish_rescope_payload_noreplace(
                        state_root, payload2, repo, bad_name  # type: ignore[arg-type]
                    )
            mock_open.assert_not_called()

            with self.assertRaises(AutopilotError) as ctx:
                _publish_rescope_payload_noreplace(
                    state_root, payload2, repo, "rescope_receipt_3.json"
                )
            self.assertIn("does not match expected attempt", str(ctx.exception))
            mock_open.assert_not_called()

            with self.assertRaises(AutopilotError):
                _publish_rescope_payload_noreplace(
                    state_root, "not-bytes-payload", repo, "rescope_receipt_2.json"  # type: ignore[arg-type]
                )
            mock_open.assert_not_called()

    def test_post_link_injected_eio_preserves_temp_and_target_nlink2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()
            payload2 = serialize_rescope_receipt(result, repo, attempt=2)

            def fail_unlink(path, *args, **kwargs):
                raise OSError(errno.EIO, "Injected disk I/O failure")

            with patch("os.unlink", side_effect=fail_unlink):
                with self.assertRaises(AutopilotError) as ctx:
                    _publish_rescope_payload_noreplace(
                        state_root, payload2, repo, "rescope_receipt_2.json"
                    )

            self.assertIn("ambiguous", str(ctx.exception))

            target = state_root / "rescope_receipt_2.json"
            self.assertTrue(target.is_file())
            target_st = target.stat(follow_symlinks=False)
            self.assertEqual(target_st.st_nlink, 2)

            temp_files = [p for p in state_root.iterdir() if p.name.startswith(".rescope_receipt_tmp_")]
            self.assertEqual(len(temp_files), 1)
            temp_st = temp_files[0].stat(follow_symlinks=False)
            self.assertEqual(temp_st.st_nlink, 2)
            self.assertEqual((target_st.st_dev, target_st.st_ino), (temp_st.st_dev, temp_st.st_ino))

    def test_post_link_injected_keyboard_interrupt_preserves_nlink2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()
            payload2 = serialize_rescope_receipt(result, repo, attempt=2)

            def interrupt_unlink(path, *args, **kwargs):
                raise KeyboardInterrupt("Simulated user interrupt after link")

            with patch("os.unlink", side_effect=interrupt_unlink):
                with self.assertRaises(KeyboardInterrupt):
                    _publish_rescope_payload_noreplace(
                        state_root, payload2, repo, "rescope_receipt_2.json"
                    )

            target = state_root / "rescope_receipt_2.json"
            self.assertTrue(target.is_file())
            target_st = target.stat(follow_symlinks=False)
            self.assertEqual(target_st.st_nlink, 2)

            temp_files = [p for p in state_root.iterdir() if p.name.startswith(".rescope_receipt_tmp_")]
            self.assertEqual(len(temp_files), 1)
            temp_st = temp_files[0].stat(follow_symlinks=False)
            self.assertEqual(temp_st.st_nlink, 2)
            self.assertEqual((target_st.st_dev, target_st.st_ino), (temp_st.st_dev, temp_st.st_ino))

    def test_post_link_wrong_envelope_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = self._create_state_root(tmpdir)
            repo = make_test_repository()
            result = make_test_result()
            payload2 = serialize_rescope_receipt(result, repo, attempt=2)

            with patch(
                "scripts.two_site_codex_autopilot.load_rescope_receipt",
                return_value={"schema_version": 1, "attempt": 2, "terminal": False, "result": {}, "repository": {}},
            ):
                with self.assertRaises(AutopilotError) as ctx:
                    _publish_rescope_payload_noreplace(
                        state_root, payload2, repo, "rescope_receipt_2.json"
                    )
            self.assertIn("ambiguous", str(ctx.exception))
            self.assertIn("loaded receipt does not match expected envelope", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
