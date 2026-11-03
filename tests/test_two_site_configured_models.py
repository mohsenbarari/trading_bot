"""Configured Gemini roles and offline dispatch; no provider is contacted."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import two_site_proposal_runner as runner
from tests import test_two_site_proposal_runner_effort as effort_fixture
from tests import test_two_site_generation_completion_contract as completion_fixture
from tests.two_site_generation_fixture import install_current_contract, seal_completion_fixture


class ConfiguredGeminiModels(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        install_current_contract(self.root)
        self.path = self.root / runner.AI_CONFIG_REL
        self.configuration = json.loads(self.path.read_text())

    def save(self):
        self.path.write_text(json.dumps(self.configuration))

    def test_defaults_use_high_for_both_roles(self):
        roles = runner.configured_gemini_roles(self.root)
        for settings in roles.values():
            self.assertEqual(settings, {
                "provider": "antigravity", "model": "gemini-4-argon-high",
                "reasoning_effort": "high",
            })

    def test_configuration_edit_changes_next_read_without_code_change(self):
        original = runner.configured_gemini_role(self.root, "worker")
        self.configuration["roles"]["worker"].update(
            model="fixture-next-worker", reasoning_effort="low",
        )
        self.save()
        selected = runner.configured_gemini_role(self.root, "worker")
        self.assertEqual(selected["model"], "fixture-next-worker")
        self.assertEqual(selected["reasoning_effort"], "low")
        self.assertNotEqual(selected, original)
        self.assertEqual(runner.configured_gemini_role(self.root, "verifier"),
                         runner.configured_gemini_role(self.root, "reviewer"))

    def test_actual_command_profile_and_status_use_each_configured_role(self):
        harness = effort_fixture.TestTwoSiteProposalRunnerEffort(methodName="runTest")
        for role in ("worker", "verifier"):
            with self.subTest(role=role):
                commands, status = harness._execute_mock_run(
                    role=role, model_override="fixture-next-model", effort_override="low",
                )
                command = commands[0]
                self.assertEqual(command[command.index("--model") + 1], "fixture-next-model")
                self.assertEqual(command[command.index("--effort") + 1], "low")
                self.assertEqual(status["model"], "fixture-next-model")
                self.assertEqual(status["effort"], "low")
                self.assertEqual(runner.bound_gemini_role(status, role), {
                    "provider": "antigravity", "model": "fixture-next-model",
                    "reasoning_effort": "low",
                })

    def test_invalid_configuration_fails_before_task_or_provider(self):
        self.configuration["roles"]["worker"]["model"] = "bad\nmodel"
        self.save()
        with patch.object(runner, "_generation_contract_sha", return_value="contract"), \
                patch.object(runner, "load_task") as task, \
                patch.object(runner.subprocess, "Popen") as provider:
            with self.assertRaisesRegex(runner.ProposalError, "AI_CONFIG_MODEL"):
                runner.run(self.root, self.root / "task.json", "worker", "invalid-config", 30)
            task.assert_not_called()
            provider.assert_not_called()

    def test_missing_configuration_is_an_explicit_error(self):
        self.path.unlink()
        with self.assertRaisesRegex(runner.ProposalError, "AI_CONFIG_UNREADABLE"):
            runner.configured_gemini_roles(self.root)

    def test_qoder_correction_feedback_keeps_exact_proposal_binding(self):
        sha = "a" * 64
        feedback = {"reviewer": "qoder", "reviewed_proposal_sha256": sha,
                    "findings": ["source.py: failure outcome misses its assertion"], "test_evidence": []}
        self.assertEqual(runner.validate_correction_feedback(json.dumps(feedback), sha), feedback)
        with self.assertRaisesRegex(runner.ProposalError, "different proposal"):
            runner.validate_correction_feedback(json.dumps(feedback), "b" * 64)
        for value in (dict(feedback, reviewer="unknown"), dict(feedback, findings=[])):
            with self.assertRaises(runner.ProposalError):
                runner.validate_correction_feedback(json.dumps(value), sha)

    def test_configuration_drift_is_rejected_before_dispatch(self):
        harness = effort_fixture.TestTwoSiteProposalRunnerEffort(methodName="runTest")
        with self.assertRaisesRegex(runner.ProposalError, "configuration changed before dispatch"):
            harness._execute_mock_run(change_config_after_prepare=True)

    def test_unsupported_gemini_effort_is_rejected(self):
        self.configuration["roles"]["worker"]["reasoning_effort"] = "xhigh"
        self.save()
        with self.assertRaisesRegex(runner.ProposalError, "invalid role settings"):
            runner.configured_gemini_roles(self.root)

    def test_bound_role_uses_recorded_snapshot_and_rejects_tampering(self):
        roles = runner.configured_gemini_roles(self.root)
        status = {"model_config": roles,
                  "model_config_sha256": runner.sha(runner.serialize_json(roles))}
        self.configuration["roles"]["worker"]["model"] = "fixture-future-model"
        self.save()
        self.assertEqual(runner.bound_gemini_role(status, "worker"), roles["worker"])
        tampered = deepcopy(status)
        tampered["model_config"]["worker"]["reasoning_effort"] = "medium"
        with self.assertRaisesRegex(runner.ProposalError, "snapshot binding mismatch"):
            runner.bound_gemini_role(tampered, "worker")
        self.assertIsNone(runner.bound_gemini_role({}, "worker"))
        with self.assertRaises(runner.ProposalError):
            runner.bound_gemini_role({"model_config_sha256": status["model_config_sha256"]}, "worker")

    def test_role_edits_leave_historical_runtime_hash_unchanged(self):
        contract = self.root / runner.GEMINI_RUNTIME_CONTRACT_REL
        original = contract.read_bytes()
        self.configuration["roles"]["worker"]["model"] = "fixture-new-model"
        self.save()
        self.assertEqual(contract.read_bytes(), original)

    def completion_case(self):
        case = completion_fixture.GenerationCompletionContracts(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_historical_medium_recovered_proof_keeps_original_pin(self):
        case = self.completion_case()
        status = case.status(case.worker)
        status.update(model=runner.RUNTIME_CONTRACT["worker_model"],
                      effort=runner.RUNTIME_CONTRACT["worker_effort"])
        status.pop("model_config", None)
        status.pop("model_config_sha256", None)
        runner.write_json(case.worker / "status.json", status)
        seal_completion_fixture(case.root, case.worker)
        self.assertEqual(case.status(case.worker)["effort"], "medium")
        self.assertIsNone(runner.bound_gemini_role(case.status(case.worker), "worker"))
        case.recovered(case.worker)
        self.assertEqual(case.gate("runner"), case.result)

    def test_retained_bound_proof_survives_a_later_role_config_edit(self):
        case = self.completion_case()
        config_path = case.root / runner.AI_CONFIG_REL
        config = json.loads(config_path.read_text())
        config["roles"]["worker"].update(model="fixture-later-model", reasoning_effort="low")
        config_path.write_text(json.dumps(config))
        self.assertEqual(case.gate("runner"), case.result)


if __name__ == "__main__":
    unittest.main()
