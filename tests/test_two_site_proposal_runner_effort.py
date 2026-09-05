#!/usr/bin/env python3
"""Hermetic tests for Gemini reasoning effort configuration and audit metadata."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import two_site_proposal_runner as runner


class TestTwoSiteProposalRunnerEffort(unittest.TestCase):
    """Hermetic unit tests for reasoning effort constant, invocation, and audit metadata."""

    def test_effort_constant_value(self) -> None:
        """Prove that EFFORT is configured to 'medium' and MODEL is preserved."""
        self.assertEqual(runner.EFFORT, "medium")
        self.assertEqual(runner.MODEL, "gemini-3.8-flash-high")
        self.assertIsInstance(runner.EFFORT, str)

    def test_runner_source_does_not_contain_hardcoded_high_effort(self) -> None:
        """Verify that scripts/two_site_proposal_runner.py does not contain hard-coded high effort."""
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn('--effort", "high"', source)
        self.assertNotIn("--effort', 'high'", source)
        self.assertIn('--effort", EFFORT', source)

    def _execute_mock_run(
        self,
        role: str = "worker",
        effort_override: str | None = None,
        simulate_error: bool = False,
    ) -> tuple[list[list[str]], dict]:
        """Execute runner.run with completely hermetic mocks and return commands and status."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            task_file = temp_path / "task.json"
            task_file.write_text("{}", encoding="utf-8")
            cli_file = temp_path / "mock_agy"
            cli_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli_file.chmod(0o755)
            proposals_dir = temp_path / "proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)

            mock_task = {
                "task_id": "GEMINI-TEST-EFFORT-001",
                "goal": "Hermetic test goal",
                "context_files": [],
                "allowed_files": [],
                "requirements": ["1. Test requirement."],
                "test_selectors": ["tests.test_two_site_proposal_runner_effort"],
            }
            mock_sources: dict = {}
            captured_commands: list[list[str]] = []

            class DummyProcess:
                pid = 98765
                returncode = 0

                def __enter__(self) -> DummyProcess:
                    return self

                def __exit__(self, *exc: object) -> bool:
                    return False

            def mock_popen(cmd: list[str], **_kwargs: object) -> DummyProcess:
                captured_commands.append(list(cmd))
                return DummyProcess()

            def mock_capture(
                process: object,
                input_bytes: bytes,
                stdout_path: Path,
                stderr_path: Path,
                timeout: float,
                max_output: int = runner.MAX_OUTPUT,
            ) -> None:
                if simulate_error:
                    raise runner.ProposalError("simulated cli bounded process failure")
                stdout_path.write_bytes(b"")
                stderr_path.write_bytes(b"")

            mock_result = {
                "status": "PROPOSAL_READY" if role == "worker" else "VERIFIED",
                "summary": "Proposal verified hermetically.",
                "covered_requirements": [1],
                "findings": [],
                "changes": [],
            }
            mock_envelope = {
                "status": "SUCCESS",
                "conversation_id": "conv-test-effort-42",
                "usage": {},
            }

            proposal_arg = None
            if role == "verifier":
                proposal_file = temp_path / "worker_proposal.json"
                proposal_file.write_text('{"changes": []}', encoding="utf-8")
                proposal_arg = proposal_file

            patches = [
                mock.patch.object(runner, "load_task", return_value=(mock_task, mock_sources)),
                mock.patch.object(runner, "git", side_effect=lambda root, *args: "fake_head_sha" if "rev-parse" in args else ""),
                mock.patch.object(runner, "bounded_file_text", side_effect=lambda root, path, limit, label: "{}" if label == "proposal" else "template"),
                mock.patch.object(runner, "acquire_proposal_lock", return_value=(42, proposals_dir)),
                mock.patch.object(runner, "release_proposal_lock"),
                mock.patch.object(runner, "cli_identity", return_value={"path": str(cli_file), "version": runner.EXPECTED_CLI_VERSION, "sha256": runner.EXPECTED_CLI_SHA256}),
                mock.patch.object(runner, "capture_bounded_process", side_effect=mock_capture),
                mock.patch.object(runner, "parse_stream", return_value=(mock_result, mock_envelope)),
                mock.patch.object(runner, "load_prior_worker", return_value=(mock_result, {"run_id": "prior-worker", "conversation_id": "prior-conv"}, runner.sha(b"{}"))),
                mock.patch("subprocess.Popen", side_effect=mock_popen),
            ]
            if effort_override is not None:
                patches.append(mock.patch.object(runner, "EFFORT", effort_override))

            with ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)

                run_id = "run-effort-check"
                if simulate_error:
                    with self.assertRaises(runner.ProposalError):
                        runner.run(
                            temp_path,
                            task_file,
                            role,
                            run_id,
                            30,
                            proposal=proposal_arg,
                            cli=cli_file,
                        )
                else:
                    out = runner.run(
                        temp_path,
                        task_file,
                        role,
                        run_id,
                        30,
                        proposal=proposal_arg,
                        cli=cli_file,
                    )
                    self.assertEqual(out, proposals_dir / run_id)

            status_path = proposals_dir / run_id / "status.json"
            self.assertTrue(status_path.is_file())
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
            return captured_commands, status_data

    def test_worker_command_and_metadata_uses_medium_effort(self) -> None:
        """Prove that worker invocation uses --effort medium and records effort in status.json."""
        commands, status_data = self._execute_mock_run(role="worker")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("--effort", command)
        idx = command.index("--effort")
        self.assertEqual(command[idx + 1], "medium")
        self.assertEqual(command[idx + 1], runner.EFFORT)
        self.assertNotIn("high", command)
        self.assertIn("effort", status_data)
        self.assertEqual(status_data["effort"], "medium")
        self.assertEqual(status_data["effort"], runner.EFFORT)

    def test_verifier_command_and_metadata_uses_medium_effort(self) -> None:
        """Prove that verifier invocation uses --effort medium and records effort in status.json."""
        commands, status_data = self._execute_mock_run(role="verifier")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("--effort", command)
        idx = command.index("--effort")
        self.assertEqual(command[idx + 1], "medium")
        self.assertEqual(command[idx + 1], runner.EFFORT)
        self.assertNotIn("high", command)
        self.assertIn("effort", status_data)
        self.assertEqual(status_data["effort"], "medium")
        self.assertEqual(status_data["effort"], runner.EFFORT)

    def test_dynamic_effort_constant_propagation(self) -> None:
        """Prove that subprocess command and status metadata dynamically track the EFFORT constant."""
        sentinel_effort = "medium-test-propagate"
        commands, status_data = self._execute_mock_run(role="worker", effort_override=sentinel_effort)
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("--effort", command)
        idx = command.index("--effort")
        self.assertEqual(command[idx + 1], sentinel_effort)
        self.assertEqual(status_data.get("effort"), sentinel_effort)

    def test_failure_preserves_effort_metadata(self) -> None:
        """Prove that status metadata records effort even when CLI execution fails (BLOCKED state)."""
        _commands, status_data = self._execute_mock_run(role="worker", simulate_error=True)
        self.assertEqual(status_data.get("state"), "BLOCKED")
        self.assertEqual(status_data.get("effort"), "medium")
        self.assertEqual(status_data.get("effort"), runner.EFFORT)


if __name__ == "__main__":
    unittest.main()
