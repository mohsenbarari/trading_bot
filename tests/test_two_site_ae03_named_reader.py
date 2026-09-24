import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from scripts.two_site_codex_autopilot import (
        CONTROL_PATHS,
        PROTECTED_REFS,
        AutopilotError,
        load_rescope_receipt,
        serialize_rescope_receipt,
        _RESCOPE_RECEIPT_FILENAMES,
    )
except ModuleNotFoundError:
    from two_site_codex_autopilot import (  # type: ignore[no-redef]
        CONTROL_PATHS,
        PROTECTED_REFS,
        AutopilotError,
        load_rescope_receipt,
        serialize_rescope_receipt,
        _RESCOPE_RECEIPT_FILENAMES,
    )

HEAD = "a" * 40


def _valid_repo():
    return {
        "head": HEAD,
        "status": "",
        "protected_refs": {ref: "b" * 40 for ref in PROTECTED_REFS},
        "control_blobs": {p.as_posix(): "c" * 40 for p in CONTROL_PATHS},
        "assignment_scope": {
            "assignment_id": "aid-1",
            "stage_id": "P1-00b",
            "sha256": "d" * 64,
            "expires_at": "2026-12-31T00:00:00Z",
            "allowed_paths": ["scripts/two_site_codex_autopilot.py"],
            "forbidden_paths": [],
        },
    }


def _valid_result():
    return {
        "schema_version": "1.0",
        "state": "BLOCKED",
        "disposition": "RESCOPE",
        "category": "INTERNAL_REJECTION",
        "summary": "rescope required",
        "commit": None,
        "next_action": "rescope step",
        "blockers": ["rejection"],
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
        "head_before": HEAD,
        "head_after": HEAD,
        "head_changed": False,
    }


class TestTwoSiteAe03NamedReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tmp.name).resolve(strict=True)
        os.chmod(self.state_root, 0o700)
        self.repo = _valid_repo()
        self.result = _valid_result()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_leaf(self, name: str, payload: bytes, mode: int = 0o600) -> Path:
        leaf = self.state_root / name
        leaf.write_bytes(payload)
        os.chmod(leaf, mode)
        return leaf

    def test_matching_attempts_and_default(self):
        for name, attempt in _RESCOPE_RECEIPT_FILENAMES.items():
            payload = serialize_rescope_receipt(self.result, self.repo, attempt=attempt)
            self._write_leaf(name, payload)
            env = load_rescope_receipt(self.state_root, self.repo, name)
            self.assertIsNotNone(env)
            self.assertEqual(env["attempt"], attempt)
            if attempt == 1:
                env_default = load_rescope_receipt(self.state_root, self.repo)
                self.assertIsNotNone(env_default)
                self.assertEqual(env_default["attempt"], 1)
            (self.state_root / name).unlink()

    def test_missing_leaf_returns_none_without_fallback(self):
        self.assertIsNone(load_rescope_receipt(self.state_root, self.repo, "rescope_receipt.json"))
        payload1 = serialize_rescope_receipt(self.result, self.repo, attempt=1)
        self._write_leaf("rescope_receipt.json", payload1)
        self.assertIsNone(load_rescope_receipt(self.state_root, self.repo, "rescope_receipt_2.json"))
        self.assertIsNone(load_rescope_receipt(self.state_root, self.repo, "rescope_receipt_3.json"))

    def test_valid_envelope_wrong_attempt_rejected(self):
        payload1 = serialize_rescope_receipt(self.result, self.repo, attempt=1)
        self._write_leaf("rescope_receipt_2.json", payload1)
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(self.state_root, self.repo, "rescope_receipt_2.json")
        payload2 = serialize_rescope_receipt(self.result, self.repo, attempt=2)
        self._write_leaf("rescope_receipt.json", payload2)
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(self.state_root, self.repo, "rescope_receipt.json")

    def test_invalid_filename_rejected_before_open(self):
        invalid_names = [
            Path("rescope_receipt.json"),
            True,
            False,
            "rescope_receipt_4.json",
            "../rescope_receipt.json",
            "rescope_receipt.json\x00",
        ]
        for bad_name in invalid_names:
            with patch("os.open") as mock_open:
                with self.assertRaises(AutopilotError):
                    load_rescope_receipt(self.state_root, self.repo, bad_name)
                mock_open.assert_not_called()

    def test_unsafe_leaf_rejected(self):
        payload = serialize_rescope_receipt(self.result, self.repo, attempt=1)
        target = self.state_root / "real_file.json"
        target.write_bytes(payload)
        os.chmod(target, 0o600)
        symlink_path = self.state_root / "rescope_receipt.json"
        os.symlink(target, symlink_path)
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(self.state_root, self.repo, "rescope_receipt.json")
        symlink_path.unlink()
        target.unlink()

        leaf = self._write_leaf("rescope_receipt.json", payload, mode=0o600)
        hardlink_path = self.state_root / "extra_link.json"
        os.link(leaf, hardlink_path)
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(self.state_root, self.repo, "rescope_receipt.json")
        hardlink_path.unlink()

        os.chmod(leaf, 0o644)
        with self.assertRaises(AutopilotError):
            load_rescope_receipt(self.state_root, self.repo, "rescope_receipt.json")


if __name__ == "__main__":
    unittest.main()
