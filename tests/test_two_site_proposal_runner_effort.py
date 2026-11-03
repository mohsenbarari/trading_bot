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
from tests.two_site_generation_fixture import install_current_contract, supported_profile
from scripts.two_site_worker_model_routing import (
    OPUS_EFFORT,
    OPUS_MODEL,
    PolicyValidationError,
    WorkerRoutingPolicy,
    compute_task_bucket,
)

ENABLED_POLICY = WorkerRoutingPolicy(
    schema_version="1.0",
    enabled=True,
    percent=22,
    model=OPUS_MODEL,
    effort=OPUS_EFFORT,
)
TASK_ID_OPUS = next(
    f"TASK-OPUS-{i:04d}"
    for i in range(1000)
    if compute_task_bucket(f"TASK-OPUS-{i:04d}") < 22
)
TASK_ID_GEMINI = next(
    f"TASK-GEMINI-{i:04d}"
    for i in range(1000)
    if compute_task_bucket(f"TASK-GEMINI-{i:04d}") >= 22
)


class TestTwoSiteProposalRunnerEffort(unittest.TestCase):
    """Hermetic tests for the canonical runtime contract and audit metadata."""

    def test_effort_constant_value(self) -> None:
        """Prove that executable constants are derived from the tracked contract."""
        contract = runner.load_runtime_contract(runner.ROOT)
        roles = runner.configured_gemini_roles(runner.ROOT)
        self.assertEqual(runner.EFFORT, roles["reviewer"]["reasoning_effort"])
        self.assertEqual(runner.WORKER_MODEL, roles["worker"]["model"])
        self.assertEqual(runner.WORKER_EFFORT, roles["worker"]["reasoning_effort"])
        self.assertEqual(runner.MODEL, roles["reviewer"]["model"])
        self.assertEqual(runner.EXPECTED_CLI_VERSION, contract["cli_version"])
        self.assertEqual(runner.EXPECTED_CLI_SHA256, contract["cli_sha256"])
        self.assertIsInstance(runner.EFFORT, str)
        self.assertIsInstance(runner.WORKER_EFFORT, str)

    def test_runner_source_does_not_duplicate_runtime_pins(self) -> None:
        """Verify that model/effort are not re-pinned outside the contract."""
        source = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertNotIn('MODEL = "gemini-', source)
        self.assertNotIn('EFFORT = "', source)
        self.assertNotIn('WORKER_EFFORT = "', source)
        self.assertNotIn('WORKER_MODEL = "', source)
        self.assertIn('--effort", role_effort', source)

    def test_runtime_contract_references_are_consistent(self) -> None:
        contract = runner.validate_runtime_contract(runner.ROOT)
        self.assertEqual(contract["model"], "gemini-4-argon-high")
        self.assertEqual(contract["effort"], "high")
        # Historical model fields remain provenance for retained proof hashes.
        self.assertEqual(contract, runner.load_runtime_contract(runner.ROOT))
        roles = runner.configured_gemini_roles(runner.ROOT)
        self.assertEqual(roles["worker"], roles["reviewer"])
        self.assertEqual(roles["worker"]["reasoning_effort"], "high")

    def _execute_mock_run(
        self,
        role: str = "worker",
        effort_override: str | None = None,
        model_override: str | None = None,
        change_config_after_prepare: bool = False,
        simulate_error: bool = False,
        task_id: str = "GEMINI-TEST-EFFORT-001",
        policy: WorkerRoutingPolicy | Exception | type[Exception] | None = None,
        run_id: str = "run-effort-check",
    ) -> tuple[list[list[str]], dict]:
        """Execute runner.run with completely hermetic mocks and return commands and status."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            install_current_contract(temp_path)
            if effort_override is not None or model_override is not None:
                config_path = temp_path / runner.AI_CONFIG_REL
                config = json.loads(config_path.read_text(encoding="utf-8"))
                role_config = config["roles"]["reviewer" if role == "verifier" else role]
                if effort_override is not None:
                    role_config["reasoning_effort"] = effort_override
                if model_override is not None:
                    role_config["model"] = model_override
                config_path.write_text(json.dumps(config), encoding="utf-8")
            task_file = temp_path / "task.json"
            task_file.write_text("{}", encoding="utf-8")
            cli_file = temp_path / "mock_agy"
            cli_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            cli_file.chmod(0o755)
            proposals_dir = temp_path / "proposals"
            proposals_dir.mkdir(parents=True, exist_ok=True)

            mock_task = {
                "task_id": task_id,
                "goal": "Hermetic test goal",
                "context_files": [],
                "allowed_files": ["file.py"],
                "requirements": ["1. Test requirement."],
                "test_selectors": ["tests.test_two_site_proposal_runner_effort"],
            }
            mock_sources: dict = {
                "file.py": {
                    "sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
                    "content": "old_file_content\n",
                }
            }
            captured_commands: list[list[str]] = []
            captured_cwds: list[Path | None] = []

            class DummyProcess:
                pid = 98765
                returncode = 0

                def __enter__(self) -> DummyProcess:
                    return self

                def __exit__(self, *exc: object) -> bool:
                    return False

            def mock_popen(cmd: list[str], cwd: object = None, **_kwargs: object) -> DummyProcess:
                captured_commands.append(list(cmd))
                captured_cwds.append(Path(cwd) if cwd is not None else None)
                if cwd is not None:
                    self.assertTrue(Path(cwd).is_dir())
                return DummyProcess()

            def mock_capture(
                process: object,
                input_bytes: bytes,
                stdout_path: Path,
                stderr_path: Path,
                timeout: float,
                max_output: int = runner.MAX_OUTPUT,
                *, generation_completion=None,
            ) -> None:
                if simulate_error:
                    raise runner.ProposalError("simulated cli bounded process failure")
                stdout_path.write_bytes(b"")
                stderr_path.write_bytes(b"")

            def mock_prepare_profile(
                cli: Path,
                workspace: Path,
                model: str,
                effort: str | None = None,
                **_kwargs: object,
            ) -> runner.PreparedProfile:
                self.assertTrue(Path(workspace).is_dir())
                if change_config_after_prepare:
                    config_path = temp_path / runner.AI_CONFIG_REL
                    updated = json.loads(config_path.read_text(encoding="utf-8"))
                    updated["roles"]["worker"]["model"] = "fixture-late-config"
                    config_path.write_text(json.dumps(updated), encoding="utf-8")
                return runner.PreparedProfile(
                    conversation_id="conv-test-effort-42",
                    workspace=Path(workspace),
                    model=model,
                    effort=effort,
                )

            prepare_profile_mock = mock.MagicMock(side_effect=mock_prepare_profile)
            validate_profile_mock = mock.MagicMock(
                side_effect=lambda prof, fresh=True: supported_profile(prof.conversation_id)
            )
            capture_mock = mock.MagicMock(side_effect=mock_capture)

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

            popen_mock = mock.MagicMock(side_effect=mock_popen)
            patches = [
                mock.patch.object(runner, "load_task", return_value=(mock_task, mock_sources)),
                mock.patch.object(runner, "git", side_effect=lambda root, *args: "fake_head_sha" if "rev-parse" in args else ""),
                mock.patch.object(runner, "bounded_file_text", side_effect=lambda root, path, limit, label: "{}" if label == "proposal" else "template"),
                mock.patch.object(runner, "acquire_proposal_lock", return_value=(42, proposals_dir)),
                mock.patch.object(runner, "release_proposal_lock"),
                mock.patch.object(runner, "cli_identity", return_value={"path": str(cli_file), "version": runner.EXPECTED_CLI_VERSION, "sha256": runner.EXPECTED_CLI_SHA256}),
                mock.patch.object(runner, "prepare_profile", prepare_profile_mock),
                mock.patch.object(runner, "validate_profile", validate_profile_mock),
                mock.patch.object(runner, "capture_bounded_process", capture_mock),
                mock.patch.object(runner, "_parse_stream", return_value=(mock_result, mock_envelope)),
                mock.patch.object(runner, "load_prior_worker", return_value=(mock_result, {"run_id": "prior-worker", "conversation_id": "prior-conv"}, runner.sha(b"{}"))),
                mock.patch.object(runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT),
                mock.patch("subprocess.Popen", popen_mock),
            ]
            loader_mock: mock.MagicMock | None = None
            if isinstance(policy, Exception) or (
                isinstance(policy, type) and issubclass(policy, Exception)
            ):
                loader_mock = mock.MagicMock(side_effect=policy)
                patches.append(
                    mock.patch.object(runner, "load_worker_routing_policy", loader_mock)
                )
            elif policy is not None:
                loader_mock = mock.MagicMock(return_value=policy)
                patches.append(
                    mock.patch.object(runner, "load_worker_routing_policy", loader_mock)
                )

            def _invoke_run() -> Path:
                return runner.run(temp_path, task_file, role, run_id, 30, proposal=proposal_arg, cli=cli_file)

            with ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)

                if simulate_error:
                    with self.assertRaises(runner.ProposalError):
                        _invoke_run()
                    self.assertEqual(prepare_profile_mock.call_count, 1)
                    self.assertEqual(validate_profile_mock.call_count, 2)
                    validate_profile_mock.assert_has_calls([
                        mock.call(mock.ANY, fresh=True),
                        mock.call(mock.ANY, fresh=True),
                    ])
                    self.assertEqual(popen_mock.call_count, 1)
                    self.assertEqual(capture_mock.call_count, 1)
                else:
                    out = _invoke_run()
                    self.assertEqual(out, proposals_dir / run_id)
                    self.assertEqual(prepare_profile_mock.call_count, 1)
                    self.assertEqual(validate_profile_mock.call_count, 3)
                    validate_profile_mock.assert_has_calls([
                        mock.call(mock.ANY, fresh=True),
                        mock.call(mock.ANY, fresh=True),
                        mock.call(mock.ANY, fresh=False),
                    ])
                    self.assertEqual(popen_mock.call_count, 1)
                    self.assertEqual(capture_mock.call_count, 1)
                if loader_mock is not None:
                    loader_mock.assert_not_called()

            self.assertEqual(len(captured_commands), 1)
            cmd = captured_commands[0]
            prep_args, prep_kwargs = prepare_profile_mock.call_args
            prep_ws = prep_args[1] if len(prep_args) > 1 else prep_kwargs["workspace"]
            prep_model = prep_args[2] if len(prep_args) > 2 else prep_kwargs["model"]
            prep_effort = prep_args[3] if len(prep_args) > 3 else prep_kwargs.get("effort")
            self.assertEqual(captured_cwds[0], prep_ws)
            self.assertEqual(prep_ws.name, "proposal-runtime-workspace")
            self.assertIn("--conversation", cmd)
            conv_idx = cmd.index("--conversation")
            self.assertEqual(cmd[conv_idx + 1], "conv-test-effort-42")
            if role == "verifier":
                self.assertNotEqual("conv-test-effort-42", "prior-conv")
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], prep_model)
            if prep_effort is None:
                self.assertNotIn("--effort", cmd)
            else:
                self.assertIn("--effort", cmd)
                effort_idx = cmd.index("--effort")
                self.assertEqual(cmd[effort_idx + 1], prep_effort)

            status_path = proposals_dir / run_id / "status.json"
            self.assertTrue(status_path.is_file())
            status_data = json.loads(status_path.read_text(encoding="utf-8"))
            if status_data["state"] != "BLOCKED":
                proof = json.loads((proposals_dir / run_id / runner._GENERATION_PROOF_NAME).read_text())
                self.assertEqual(proof["model_config_sha256"], status_data["model_config_sha256"])
            return captured_commands, status_data

    def test_worker_command_and_metadata_uses_contract_effort(self) -> None:
        """Prove that worker invocation uses the contract effort and records it."""
        commands, status_data = self._execute_mock_run(role="worker")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("--effort", command)
        idx = command.index("--effort")
        model_idx = command.index("--model")
        configured = runner.configured_gemini_role(runner.ROOT, "worker")
        self.assertEqual(command[model_idx + 1], configured["model"])
        self.assertEqual(status_data["model"], configured["model"])
        self.assertEqual(command[idx + 1], configured["reasoning_effort"])
        self.assertEqual(command[idx + 1], runner.WORKER_EFFORT)
        self.assertIn("effort", status_data)
        self.assertEqual(status_data["effort"], configured["reasoning_effort"])
        self.assertEqual(status_data["effort"], runner.WORKER_EFFORT)

    def test_verifier_command_and_metadata_uses_contract_effort(self) -> None:
        """Prove that verifier invocation uses the contract effort and records it."""
        commands, status_data = self._execute_mock_run(role="verifier")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("--effort", command)
        idx = command.index("--effort")
        model_idx = command.index("--model")
        self.assertEqual(command[model_idx + 1], runner.RUNTIME_CONTRACT["model"])
        self.assertEqual(status_data["model"], runner.RUNTIME_CONTRACT["model"])
        self.assertEqual(command[idx + 1], runner.RUNTIME_CONTRACT["effort"])
        self.assertEqual(command[idx + 1], runner.EFFORT)
        self.assertIn("effort", status_data)
        self.assertEqual(status_data["effort"], runner.RUNTIME_CONTRACT["effort"])
        self.assertEqual(status_data["effort"], runner.EFFORT)

    def test_dynamic_effort_constant_propagation(self) -> None:
        """Prove that subprocess command and status metadata dynamically track the EFFORT constant."""
        sentinel_effort = "medium"
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
        self.assertEqual(status_data.get("effort"), runner.configured_gemini_role(runner.ROOT, "worker")["reasoning_effort"])
        self.assertEqual(status_data.get("effort"), runner.WORKER_EFFORT)

    def test_legacy_policy_cannot_override_configured_new_worker(self) -> None:
        """Old rollout buckets remain irrelevant to fresh configured runs."""
        configured = runner.configured_gemini_role(runner.ROOT, "worker")
        for task_id in (TASK_ID_OPUS, TASK_ID_GEMINI):
            commands, status = self._execute_mock_run(
                role="worker", task_id=task_id, policy=ENABLED_POLICY,
            )
            command = commands[0]
            self.assertEqual(command[command.index("--model") + 1], configured["model"])
            self.assertEqual(command[command.index("--effort") + 1], configured["reasoning_effort"])
            self.assertEqual(status["model"], configured["model"])
            self.assertEqual(status["effort"], configured["reasoning_effort"])

    def test_retry_preserves_selected_model_and_reviewer_stays_gemini(self) -> None:
        """Repeat same task ID with distinct run IDs and prove identical model; verifier stays Gemini."""
        run_id_1 = "run-opus-retry-001"
        run_id_2 = "run-opus-retry-002"
        self.assertNotEqual(run_id_1, run_id_2)

        commands_run1, status_run1 = self._execute_mock_run(
            role="worker",
            task_id=TASK_ID_OPUS,
            policy=ENABLED_POLICY,
            run_id=run_id_1,
        )
        commands_run2, status_run2 = self._execute_mock_run(
            role="worker",
            task_id=TASK_ID_OPUS,
            policy=ENABLED_POLICY,
            run_id=run_id_2,
        )
        self.assertEqual(len(commands_run1), 1)
        self.assertEqual(len(commands_run2), 1)
        model_1 = commands_run1[0][commands_run1[0].index("--model") + 1]
        model_2 = commands_run2[0][commands_run2[0].index("--model") + 1]
        self.assertEqual(model_1, runner.configured_gemini_role(runner.ROOT, "worker")["model"])
        self.assertEqual(model_2, runner.configured_gemini_role(runner.ROOT, "worker")["model"])
        self.assertEqual(model_1, model_2)
        self.assertEqual(status_run1.get("model"), status_run2.get("model"))
        self.assertEqual(status_run1.get("effort"), status_run2.get("effort"))

        # Verifier role with enabled policy and bucket < 22 still uses Gemini Reviewer model/effort
        commands_verifier, status_verifier = self._execute_mock_run(
            role="verifier",
            task_id=TASK_ID_OPUS,
            policy=ENABLED_POLICY,
        )
        self.assertEqual(len(commands_verifier), 1)
        cmd_ver = commands_verifier[0]
        self.assertIn("--model", cmd_ver)
        self.assertEqual(cmd_ver[cmd_ver.index("--model") + 1], runner.RUNTIME_CONTRACT["model"])
        self.assertEqual(cmd_ver[cmd_ver.index("--model") + 1], runner.MODEL)
        self.assertIn("--effort", cmd_ver)
        self.assertEqual(cmd_ver[cmd_ver.index("--effort") + 1], runner.RUNTIME_CONTRACT["effort"])
        self.assertEqual(cmd_ver[cmd_ver.index("--effort") + 1], runner.EFFORT)
        self.assertEqual(status_verifier.get("model"), runner.RUNTIME_CONTRACT["model"])
        self.assertEqual(status_verifier.get("effort"), runner.RUNTIME_CONTRACT["effort"])

    def test_legacy_policy_is_not_loaded_for_new_configured_runs(self) -> None:
        """A dormant historical policy is not a new-run dependency."""
        commands_invalid, status_invalid = self._execute_mock_run(
            role="worker",
            task_id=TASK_ID_OPUS,
            policy=PolicyValidationError("Corrupt policy schema"),
            run_id="run-invalid-policy",
        )
        self.assertEqual(len(commands_invalid), 1)
        self.assertEqual(status_invalid["model"], runner.WORKER_MODEL)

        # Missing policy loader error
        commands_missing, status_missing = self._execute_mock_run(
            role="worker",
            task_id=TASK_ID_OPUS,
            policy=FileNotFoundError("Policy file not found"),
            run_id="run-missing-policy",
        )
        self.assertEqual(len(commands_missing), 1)
        self.assertEqual(status_missing["model"], runner.WORKER_MODEL)


if __name__ == "__main__":
    unittest.main()
