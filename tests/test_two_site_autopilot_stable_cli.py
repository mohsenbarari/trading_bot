#!/usr/bin/env python3
"""Tests for stable Codex CLI pin derivation and constants."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from scripts import two_site_codex_autopilot as autopilot
except ModuleNotFoundError:
    import two_site_codex_autopilot as autopilot


def synthetic_scope() -> autopilot.AssignmentScope:
    return autopilot.AssignmentScope(
        assignment_id="WA-P1-00B-FREEZE-20260904-001",
        stage_id="stage-p1-freeze",
        allowed_paths=("tests/*",),
        forbidden_paths=(".git/*",),
        expires_at="2030-01-01T00:00:00Z",
        sha256="0" * 64,
    )


class TestStableCliPin(unittest.TestCase):
    def test_constants_and_model_effort_exact(self) -> None:
        self.assertEqual(autopilot.EXPECTED_CODEX_CLI_VERSION, "codex-cli 0.156.1")
        self.assertEqual(
            autopilot.EXPECTED_CODEX_CLI_SHA256,
            "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f",
        )
        self.assertEqual(autopilot.MODEL, "gpt-6-sol")
        self.assertEqual(autopilot.REASONING, "xhigh")

    @patch.object(autopilot, "codex_identity")
    @patch.object(autopilot, "validate_repository")
    def test_main_check_selects_derived_default_with_path_codex(
        self, mock_validate, mock_identity,
    ) -> None:
        mock_validate.return_value = {
            "head": "0" * 40,
            "assignment_scope": synthetic_scope(),
        }
        mock_identity.return_value = {
            "path": "dummy", "version": autopilot.EXPECTED_CODEX_CLI_VERSION,
            "sha256": autopilot.EXPECTED_CODEX_CLI_SHA256,
            "model": autopilot.MODEL, "reasoning": autopilot.REASONING,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            derived = root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                with patch("sys.argv", ["two_site_codex_autopilot.py", "--check", "--root", str(root)]):
                    self.assertEqual(autopilot.main(), 0)
            self.assertEqual(mock_identity.call_count, 1)
            self.assertEqual(mock_identity.call_args[0][0], derived)

    @patch.object(autopilot, "codex_identity")
    @patch.object(autopilot, "validate_repository")
    def test_main_check_preserves_explicit_codex(
        self, mock_validate, mock_identity,
    ) -> None:
        mock_validate.return_value = {
            "head": "0" * 40,
            "assignment_scope": synthetic_scope(),
        }
        mock_identity.return_value = {
            "path": "/custom/codex", "version": autopilot.EXPECTED_CODEX_CLI_VERSION,
            "sha256": autopilot.EXPECTED_CODEX_CLI_SHA256,
            "model": autopilot.MODEL, "reasoning": autopilot.REASONING,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom = Path(tmp_dir) / "custom-codex"
            with patch("sys.argv", ["two_site_codex_autopilot.py", "--check", "--codex", str(custom)]):
                self.assertEqual(autopilot.main(), 0)
            self.assertEqual(mock_identity.call_count, 1)
            self.assertEqual(mock_identity.call_args[0][0], custom)

    @patch.object(autopilot, "validate_repository")
    def test_absent_derived_executable_fails_without_path_fallback(
        self, mock_validate,
    ) -> None:
        mock_validate.return_value = {"head": "0" * 40}
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            missing = root.resolve() / ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"
            with tempfile.TemporaryDirectory() as scratch:
                with self.assertRaises(FileNotFoundError):
                    autopilot.codex_identity(missing, Path(scratch))
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                with patch("sys.argv", ["two_site_codex_autopilot.py", "--check", "--root", str(root)]):
                    with self.assertRaises(FileNotFoundError):
                        autopilot.main()


if __name__ == "__main__":
    unittest.main()
