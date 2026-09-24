#!/usr/bin/env python3
"""Hermetic unit tests for sequential next rescope receipt publication (AE-03)."""
from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_codex_autopilot import (
    CONTROL_PATHS,
    PROTECTED_REFS,
    AutopilotError,
    _RESCOPE_RECEIPT_FILENAMES,
    load_latest_rescope_receipt,
    load_rescope_receipt,
    publish_initial_rescope_receipt,
    publish_next_rescope_receipt,
    serialize_rescope_receipt,
)


def _make_repo(head: str = "a" * 40) -> dict[str, object]:
    return {
        "head": head,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "assign-1",
            "stage_id": "P1-00b",
            "sha256": "d" * 64,
            "expires_at": "2026-12-31T23:59:59Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


def _make_result(head: str = "a" * 40, summary: str = "rescope summary") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": summary,
        "commit": None,
        "next_action": "rescope next action",
        "blockers": ["scope reduction needed"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": head,
        "head_after": head,
        "head_changed": False,
    }


class TestTwoSiteAE03NextPublish(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="two-site-test-ae03-next-")
        self.state_root = Path(self._temp_dir.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)
        self.repo = _make_repo()
        self.res1 = _make_result(summary="attempt 1")
        self.res2 = _make_result(summary="attempt 2")
        self.res3 = _make_result(summary="attempt 3")
        self.res4 = _make_result(summary="attempt 4")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_next_publish_initial_none_fails_no_file_created(self) -> None:
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res1, self.repo)
        self.assertIn("no prior rescope receipt found", str(ctx.exception))
        remaining = list(self.state_root.iterdir())
        self.assertEqual(remaining, [])

    def test_next_publish_sequential_progression_and_restart(self) -> None:
        publish_initial_rescope_receipt(self.state_root, self.res1, self.repo)
        file1 = self.state_root / "rescope_receipt.json"
        self.assertTrue(file1.is_file())

        publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
        file2 = self.state_root / "rescope_receipt_2.json"
        self.assertTrue(file2.is_file())
        expected_payload_2 = serialize_rescope_receipt(self.res2, self.repo, attempt=2)
        self.assertEqual(file2.read_bytes(), expected_payload_2)
        st2 = file2.stat(follow_symlinks=False)
        self.assertEqual(st2.st_mode & 0o777, 0o600)
        self.assertEqual(st2.st_nlink, 1)

        # Simulate restart via separate fresh call to same state_root path
        publish_next_rescope_receipt(self.state_root, self.res3, self.repo)
        file3 = self.state_root / "rescope_receipt_3.json"
        self.assertTrue(file3.is_file())
        expected_payload_3 = serialize_rescope_receipt(self.res3, self.repo, attempt=3, terminal=True)
        self.assertEqual(file3.read_bytes(), expected_payload_3)
        st3 = file3.stat(follow_symlinks=False)
        self.assertEqual(st3.st_mode & 0o777, 0o600)
        self.assertEqual(st3.st_nlink, 1)

        # Terminal fourth attempt fails closed without extra files
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res4, self.repo)
        self.assertIn("previous attempt must be 1 or 2 to advance", str(ctx.exception))

        # Exact leaves on success: only three fixed safe leaves, no temp files
        leaves = sorted(p.name for p in self.state_root.iterdir())
        self.assertEqual(leaves, ["rescope_receipt.json", "rescope_receipt_2.json", "rescope_receipt_3.json"])
        for name in leaves:
            path = self.state_root / name
            st = path.stat(follow_symlinks=False)
            self.assertEqual(st.st_mode & 0o777, 0o600)
            self.assertEqual(st.st_nlink, 1)

    def test_next_publish_fails_on_gap_unsafe_or_stale_prior(self) -> None:
        # Gap prior: leaf 2 exists without leaf 1
        payload2 = serialize_rescope_receipt(self.res2, self.repo, attempt=2)
        file2 = self.state_root / "rescope_receipt_2.json"
        file2.write_bytes(payload2)
        os.chmod(file2, 0o600)
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res3, self.repo)
        self.assertIn("non-contiguous rescope receipt sequence", str(ctx.exception))
        self.assertFalse((self.state_root / "rescope_receipt_3.json").exists())
        file2.unlink()

        # Unsafe prior mode: leaf 1 has mode 0666 instead of 0600
        publish_initial_rescope_receipt(self.state_root, self.res1, self.repo)
        file1 = self.state_root / "rescope_receipt.json"
        os.chmod(file1, 0o666)
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
        self.assertIn("mode must be 0600", str(ctx.exception))
        self.assertFalse((self.state_root / "rescope_receipt_2.json").exists())
        os.chmod(file1, 0o600)

        # Unsafe prior symlink: leaf 1 replaced with symlink
        file1.unlink()
        target_dummy = self.state_root / ".target_dummy"
        target_dummy.write_bytes(payload2)
        os.chmod(target_dummy, 0o600)
        os.symlink(target_dummy, file1)
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
        self.assertTrue(
            "symlink" in str(ctx.exception) or "pinned directory does not match" in str(ctx.exception)
        )
        self.assertFalse((self.state_root / "rescope_receipt_2.json").exists())
        file1.unlink()
        target_dummy.unlink()

        # Stale prior: leaf 1 has different repository head facts
        stale_repo = _make_repo(head="f" * 40)
        stale_res = _make_result(head="f" * 40)
        publish_initial_rescope_receipt(self.state_root, stale_res, stale_repo)
        with self.assertRaises(AutopilotError) as ctx:
            publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
        self.assertIn("receipt repository facts do not match expected repository facts", str(ctx.exception))
        self.assertFalse((self.state_root / "rescope_receipt_2.json").exists())

    def test_next_publish_preexisting_target_collision_never_overwrites(self) -> None:
        publish_initial_rescope_receipt(self.state_root, self.res1, self.repo)
        file2 = self.state_root / "rescope_receipt_2.json"

        real_load = load_latest_rescope_receipt

        def load_and_collide(root: Path, expected_repo: object) -> dict[str, object] | None:
            res = real_load(root, expected_repo)
            file2.write_bytes(b"COLLISION_DO_NOT_OVERWRITE")
            os.chmod(file2, 0o600)
            return res

        with patch("scripts.two_site_codex_autopilot.load_latest_rescope_receipt", side_effect=load_and_collide):
            with self.assertRaises(AutopilotError) as ctx:
                publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
        self.assertTrue("target receipt already exists" in str(ctx.exception) or "collision" in str(ctx.exception))
        self.assertEqual(file2.read_bytes(), b"COLLISION_DO_NOT_OVERWRITE")
        temps = [p.name for p in self.state_root.iterdir() if p.name.startswith(".rescope_receipt_tmp_")]
        self.assertEqual(temps, [])

    def test_next_publish_post_publication_mismatch_or_reader_error_is_ambiguous(self) -> None:
        # Injected post-publication envelope mismatch
        publish_initial_rescope_receipt(self.state_root, self.res1, self.repo)
        real_load = load_latest_rescope_receipt
        call_count = 0

        def mismatch_load(root: Path, expected_repo: object) -> dict[str, object] | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return real_load(root, expected_repo)
            envelope = real_load(root, expected_repo)
            if envelope is not None:
                tampered = dict(envelope)
                tampered["attempt"] = 99
                return tampered
            return None

        with patch("scripts.two_site_codex_autopilot.load_latest_rescope_receipt", side_effect=mismatch_load):
            with self.assertRaises(AutopilotError) as ctx:
                publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
            self.assertTrue(str(ctx.exception).startswith("ambiguous"))

        # Target file was linked and evidence preserved
        file2 = self.state_root / "rescope_receipt_2.json"
        self.assertTrue(file2.is_file())
        file2.unlink()

        # Injected post-publication reader error
        call_count = 0

        def error_load(root: Path, expected_repo: object) -> dict[str, object] | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return real_load(root, expected_repo)
            raise OSError("injected post-link reader error")

        with patch("scripts.two_site_codex_autopilot.load_latest_rescope_receipt", side_effect=error_load):
            with self.assertRaises(AutopilotError) as ctx:
                publish_next_rescope_receipt(self.state_root, self.res2, self.repo)
            self.assertTrue(str(ctx.exception).startswith("ambiguous"))
        self.assertTrue(file2.is_file())

    def test_next_publish_interrupt_propagates_bare(self) -> None:
        publish_initial_rescope_receipt(self.state_root, self.res1, self.repo)

        with patch(
            "scripts.two_site_codex_autopilot._publish_rescope_payload_noreplace",
            side_effect=KeyboardInterrupt("simulated interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                publish_next_rescope_receipt(self.state_root, self.res2, self.repo)

        with patch(
            "scripts.two_site_codex_autopilot._publish_rescope_payload_noreplace",
            side_effect=SystemExit(2),
        ):
            with self.assertRaises(SystemExit):
                publish_next_rescope_receipt(self.state_root, self.res2, self.repo)


if __name__ == "__main__":
    unittest.main()
