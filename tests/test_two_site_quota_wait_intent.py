"""Tests for read-only verification of pending rescope intent markers."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts import two_site_codex_autopilot as autopilot
from scripts.two_site_quota_wait_intent import (
    RESCOPE_INTENT_FILENAMES,
    WaitIntentError,
    read_pending_rescope_intent,
)


class TestTwoSiteQuotaWaitIntent(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

        self.state_root = self.root / ".local" / "two-site-refactor" / "autopilot"
        self.state_root.mkdir(parents=True, mode=0o700)
        self.state_root.chmod(0o700)

        self.assignment_scope = autopilot.AssignmentScope(
            assignment_id="WA-TEST-SCOPE-001",
            stage_id="P1-TEST",
            allowed_paths=("change.py",),
            forbidden_paths=("blocked/**",),
            expires_at="2099-01-01T00:00:00Z",
            sha256="a" * 64,
        )
        self.repo_facts = {
            "head": "a" * 40,
            "status": "",
            "protected_refs": {ref: "2" * 40 for ref in autopilot.PROTECTED_REFS},
            "control_blobs": {p.as_posix(): "3" * 40 for p in autopilot.CONTROL_PATHS},
            "assignment_scope": self.assignment_scope,
        }
        self.norm_repo = autopilot.validate_accepted_repository(self.repo_facts)
        self.epoch = autopilot.rescope_epoch_basename(self.norm_repo)
        self.epoch_dir = autopilot.ensure_rescope_epoch_dir(self.state_root, self.norm_repo)
        autopilot.publish_rescope_intent(self.epoch_dir, 1)

    def _snapshot_tree(self, root: Path) -> dict[str, tuple[int, int, int, bytes | None]]:
        tree: dict[str, tuple[int, int, int, bytes | None]] = {}
        for p in sorted(root.rglob("*")):
            rel = str(p.relative_to(root))
            st = p.stat(follow_symlinks=False)
            content: bytes | None = None
            if stat.S_ISREG(st.st_mode):
                content = p.read_bytes()
            tree[rel] = (st.st_mode, st.st_size, st.st_mtime_ns, content)
        return tree

    def test_positive_publisher_to_pinned_reader_roundtrip(self) -> None:
        """Verify real published marker 1 reads cleanly without writes or source mocks."""
        snapshot_before = self._snapshot_tree(self.root)
        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            epoch_res, attempt_res, norm_repo_res = read_pending_rescope_intent(self.root)

        snapshot_after = self._snapshot_tree(self.root)
        self.assertEqual(snapshot_before, snapshot_after)
        self.assertEqual(epoch_res, self.epoch)
        self.assertEqual(attempt_res, 1)
        self.assertEqual(norm_repo_res, self.norm_repo)

    def test_negative_absent_marker(self) -> None:
        """Verify missing intent marker raises WaitIntentError and creates no files."""
        marker = self.epoch_dir / RESCOPE_INTENT_FILENAMES[1]
        marker.unlink()
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("cannot pre-stat intent leaf", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_negative_symlink_marker(self) -> None:
        """Verify symlinked marker fails closed against a safe temporary target."""
        marker = self.epoch_dir / RESCOPE_INTENT_FILENAMES[1]
        marker.unlink()
        safe_target = self.root / "safe_target.txt"
        safe_target.write_bytes(f"{RESCOPE_INTENT_FILENAMES[1]}\n".encode("ascii"))
        marker.symlink_to(safe_target)
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("regular non-symlink file", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_negative_hardlink_nlink2_marker(self) -> None:
        """Verify hardlinked marker with nlink > 1 fails closed against a safe temporary target."""
        marker = self.epoch_dir / RESCOPE_INTENT_FILENAMES[1]
        safe_target = self.root / "safe_hardlink.txt"
        os.link(marker, safe_target)
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("link count must be 1", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_negative_wrong_mode_marker(self) -> None:
        """Verify marker with permissions other than 0600 fails closed."""
        marker = self.epoch_dir / RESCOPE_INTENT_FILENAMES[1]
        marker.chmod(0o644)
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("mode must be 0600", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_negative_same_length_bad_content_marker(self) -> None:
        """Verify marker with payload mismatch of exact same length fails closed."""
        marker = self.epoch_dir / RESCOPE_INTENT_FILENAMES[1]
        payload = f"{RESCOPE_INTENT_FILENAMES[1]}\n".encode("ascii")
        marker.unlink()
        marker.write_bytes(b"X" * len(payload))
        marker.chmod(0o600)
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("payload mismatch", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_negative_future_intent2_present(self) -> None:
        """Verify presence of future attempt marker 2 fails closed when attempt 1 is pending."""
        autopilot.publish_rescope_intent(self.epoch_dir, 2)
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("must be absent", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_repository_drift_on_final_validation(self) -> None:
        """Verify repository drift detected between start and end of reader raises WaitIntentError."""
        drifted_facts = deepcopy(self.repo_facts)
        drifted_facts["head"] = "b" * 40
        snapshot_before = self._snapshot_tree(self.root)

        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=[deepcopy(self.repo_facts), drifted_facts],
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)

        self.assertIn("repository state drifted during pending intent verification", str(ctx.exception))
        self.assertEqual(snapshot_before, self._snapshot_tree(self.root))

    def test_load_latest_rescope_receipt_terminal_and_prior_attempts(self) -> None:
        """Verify receipts with terminal flag or prior attempts in an isolated test."""
        terminal_receipt = {"attempt": 1, "terminal": True}
        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ), patch(
            "scripts.two_site_quota_wait_intent.load_latest_rescope_receipt",
            return_value=terminal_receipt,
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)
        self.assertIn("latest receipt terminal flag must be False", str(ctx.exception))

        invalid_attempt_receipt = {"attempt": 3, "terminal": False}
        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ), patch(
            "scripts.two_site_quota_wait_intent.load_latest_rescope_receipt",
            return_value=invalid_attempt_receipt,
        ):
            with self.assertRaises(WaitIntentError) as ctx:
                read_pending_rescope_intent(self.root)
        self.assertIn("latest receipt attempt must be exact integer 1 or 2", str(ctx.exception))

        autopilot.publish_rescope_intent(self.epoch_dir, 2)
        valid_prior_receipt = {"attempt": 1, "terminal": False}
        with patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *args, **kwargs: deepcopy(self.repo_facts),
        ), patch(
            "scripts.two_site_quota_wait_intent.load_latest_rescope_receipt",
            return_value=valid_prior_receipt,
        ):
            epoch_res, attempt_res, norm_repo_res = read_pending_rescope_intent(self.root)
        self.assertEqual(epoch_res, self.epoch)
        self.assertEqual(attempt_res, 2)
        self.assertEqual(norm_repo_res, self.norm_repo)


if __name__ == "__main__":
    unittest.main()
