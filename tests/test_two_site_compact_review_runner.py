"""Hermetic unit tests for compact review runner verifier workflow."""
import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )

    install_p1_00_network_fence()
except (ImportError, AttributeError):
    pass

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

try:
    from scripts.two_site_agent_sandbox import SandboxError
except ImportError:
    from two_site_agent_sandbox import SandboxError

from scripts import two_site_proposal_runner as runner
from tests.two_site_generation_fixture import seal_completion_fixture, supported_profile


class CompactReviewRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

        for relative in (
            runner.GEMINI_RUNTIME_CONTRACT_REL,
            *runner.GEMINI_RUNTIME_REFERENCE_PATHS,
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runner.ROOT / relative, target)

        self.source_content = (
            "def target_func():\n"
            "    return 42\n\n"
            + (("# " + ("x" * 80) + "\n") * 110)
        )
        (self.root / "source.py").write_text(self.source_content)
        self.source_sha = runner.sha(self.source_content.encode("utf-8"))

        self.compact_spec_json = json.dumps(
            {"path": "source.py", "symbols": ["target_func"]}
        )
        self.task = {
            "task_id": "TEST-COMPACT-001",
            "goal": (
                "Refactor compact review target function.\n"
                f"COMPACT_REVIEW_SYMBOLS_JSON:\n{self.compact_spec_json}"
            ),
            "context_files": ["source.py"],
            "allowed_files": ["source.py"],
            "requirements": ["Refactor target_func", "Preserve behavior"],
            "test_selectors": ["test_source.py"],
        }
        self.task_path = self.root / "task.json"
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)

    def save_task(self):
        self.task_path.write_text(json.dumps(self.task))

    def worker_evidence(self, directory, *, result=None, head="head"):
        directory.mkdir(parents=True, exist_ok=True)
        proposal = directory / "result.json"
        if result is None:
            new_content = self.source_content.replace("return 42", "return 43")
            result = {
                "status": "PROPOSAL_READY",
                "summary": "Updated target_func in source.py.",
                "covered_requirements": [1, 2],
                "findings": [],
                "changes": [
                    {
                        "path": "source.py",
                        "original_sha256": self.source_sha,
                        "content": new_content,
                    }
                ],
            }
        runner.write_json(proposal, result)
        runner.write_json(
            directory / "task.json",
            {"task": self.task, "source_head": head, "sources": self.sources},
        )
        runner.write_json(
            directory / "status.json",
            {
                "run_id": directory.name,
                "role": "worker",
                "state": "PROPOSAL_READY",
                "conversation_id": "worker-session-001",
                "result_sha256": runner.file_sha256(proposal),
            },
        )
        seal_completion_fixture(self.root, directory)
        return proposal

    def create_receipt(self, proposal_sha, *, state="PASSED", returncode=0):
        receipt = {
            "state": state,
            "proposal_sha256": proposal_sha,
            "selectors": ["tests/test_two_site_compact_review_runner.py"],
            "unsupported_specs": [],
            "returncode": returncode,
            "stdout_tail": "",
            "stderr_tail": "",
            "snapshot_files": 1,
            "snapshot_bytes": 1,
            "isolation": "bwrap",
        }
        receipt_path = self.root / "test_receipt.json"
        runner.write_json(receipt_path, receipt)
        return receipt_path, receipt

    def invoke_fake_cli(
        self,
        *,
        run_id="run-001",
        role="verifier",
        proposal=None,
        test_evidence=None,
        result=None,
        events=None,
        task_path=None,
    ):
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 0
        process.pid = 987654
        process.poll.return_value = None

        if result is None and events is None:
            result = {
                "status": "VERIFIED",
                "summary": "Verified compact review proposal.",
                "covered_requirements": [1, 2],
                "findings": [],
                "changes": [],
            }

        if events is None:
            events = [
                {
                    "event": "init",
                    "conversation_id": "session-verifier",
                    "init": {"model": runner.MODEL},
                },
                {
                    "event": "result",
                    "result": {
                        "status": "SUCCESS",
                        "conversation_id": "session-verifier",
                        "response": json.dumps(result),
                        "structured_output": result,
                    },
                },
            ]

        def capture(_fake_process, _input, stdout_path, stderr_path, _timeout, **_kwargs):
            stdout_path.write_bytes("\n".join(json.dumps(e) for e in events).encode("utf-8"))
            stderr_path.write_bytes(b"")

        identity = {
            "path": str(Path(__file__).resolve()),
            "version": runner.EXPECTED_CLI_VERSION,
            "sha256": runner.EXPECTED_CLI_SHA256,
        }

        def fake_git(_root, *args):
            if "rev-parse" in args:
                return "head"
            return ""

        target_task_path = task_path if task_path is not None else self.task_path
        with (
            patch.object(runner, "git", side_effect=fake_git),
            patch.object(runner, "cli_identity", return_value=identity),
            patch.object(runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT),
            patch.object(runner, "prepare_profile", side_effect=lambda cli, ws, model, effort=None, **kw: runner.PreparedProfile("session-verifier", ws, model, effort)),
            patch.object(runner, "validate_profile", side_effect=lambda prof, **kw: supported_profile(prof.conversation_id)),
            patch.object(runner, "capture_bounded_process", side_effect=capture),
            patch.object(runner.subprocess, "Popen", return_value=process) as popen,
        ):
            self.last_popen = popen
            return runner.run(
                self.root,
                target_task_path,
                role,
                run_id,
                30,
                proposal=proposal,
                test_evidence=test_evidence,
                cli=Path(__file__).resolve(),
            )

    def test_compact_review_success_separates_prompt_and_snapshot_with_exact_bindings(self):
        worker_dir = self.root / "worker-success"
        proposal = self.worker_evidence(worker_dir)
        proposal_sha = runner.file_sha256(proposal)
        receipt_path, _ = self.create_receipt(proposal_sha)

        out = self.invoke_fake_cli(
            run_id="run-compact-success",
            proposal=proposal,
            test_evidence=receipt_path,
        )

        self.last_popen.assert_called_once()
        cli_args = self.last_popen.call_args.args[0]
        self.assertIn("--sandbox", cli_args)
        self.assertNotIn("--dangerously-skip-permissions", cli_args)

        status_file = out / "status.json"
        task_file = out / "task.json"
        prompt_file = out / "prompt.txt"
        result_file = out / "result.json"

        self.assertTrue(status_file.exists())
        self.assertTrue(task_file.exists())
        self.assertTrue(prompt_file.exists())
        self.assertTrue(result_file.exists())

        status = json.loads(status_file.read_text())
        self.assertEqual(status["state"], "VERIFIED")
        self.assertEqual(status["input_proposal_sha256"], proposal_sha)
        self.assertEqual(status["input_worker_run_id"], "worker-success")

        task_snapshot = json.loads(task_file.read_text())
        self.assertEqual(task_snapshot["sources"]["source.py"]["content"], self.source_content)
        self.assertEqual(task_snapshot["sources"]["source.py"]["sha256"], self.source_sha)
        self.assertEqual(task_snapshot["test_evidence"]["proposal_sha256"], proposal_sha)
        self.assertEqual(task_snapshot["test_evidence"]["state"], "PASSED")

        prompt_text = prompt_file.read_text()
        self.assertIn(
            "Compact review notice: one original source is only a whole-file-SHA-bound AST excerpt",
            prompt_text,
        )
        self.assertIn("\nROLE: verifier\nCONTEXT_JSON:\n", prompt_text)
        context_text, schema_text = prompt_text.split("\nCONTEXT_JSON:\n", 1)[1].split("\nREQUIRED_RESPONSE_SCHEMA_DATA:\n", 1)
        prompt_json = json.loads(context_text)
        prompt_schema, end = json.JSONDecoder().raw_decode(schema_text)
        self.assertEqual(prompt_schema, json.loads((out / "response-schema.json").read_text()))
        self.assertEqual(schema_text[end:], "\nReturn exactly one JSON object directly, no Markdown or fences. The same exact schema is supplied to the CLI; no tool, shell or file action is authorized.\n")
        self.assertEqual(
            prompt_json["scope_marker"],
            "EXCERPT_NOT_FULL_SOURCE_CODEX_FULL_REVIEW_REQUIRED",
        )
        self.assertNotIn("sources", prompt_json)
        self.assertEqual(prompt_json["exact_original_source_excerpt"]["path"], "source.py")
        self.assertEqual(prompt_json["exact_original_source_excerpt"]["symbols"], ["target_func"])
        self.assertEqual(prompt_json["exact_original_source_excerpt"]["sha256"], self.source_sha)
        self.assertEqual(prompt_json["pre_review_test_evidence"]["proposal_sha256"], proposal_sha)
        self.assertEqual(prompt_json["pre_review_test_evidence"]["state"], "PASSED")
        self.assertLessEqual(len(prompt_text.encode("utf-8")), 160_000)

        self.assertEqual((self.root / "source.py").read_text(), self.source_content)

    def test_default_verifier_unchanged_when_compact_marker_absent(self):
        self.task["goal"] = "Normal refactor goal without compact review marker."
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)

        worker_dir = self.root / "worker-default"
        proposal = self.worker_evidence(worker_dir)
        proposal_sha = runner.file_sha256(proposal)
        receipt_path, _ = self.create_receipt(proposal_sha)

        out = self.invoke_fake_cli(
            run_id="run-default-verifier",
            proposal=proposal,
            test_evidence=receipt_path,
        )

        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "VERIFIED")

        prompt_text = (out / "prompt.txt").read_text()
        self.assertNotIn("Compact review notice:", prompt_text)
        context_text, schema_text = prompt_text.split("\nCONTEXT_JSON:\n", 1)[1].split("\nREQUIRED_RESPONSE_SCHEMA_DATA:\n", 1)
        prompt_json = json.loads(context_text)
        prompt_schema, end = json.JSONDecoder().raw_decode(schema_text)
        self.assertEqual(prompt_schema, json.loads((out / "response-schema.json").read_text()))
        self.assertEqual(schema_text[end:], "\nReturn exactly one JSON object directly, no Markdown or fences. The same exact schema is supplied to the CLI; no tool, shell or file action is authorized.\n")
        self.assertIn("sources", prompt_json)
        self.assertNotIn("exact_original_source_excerpt", prompt_json)
        self.assertEqual(prompt_json["sources"]["source.py"]["content"], self.source_content)

    def test_compact_review_pre_cli_failure_on_bad_selector(self):
        bad_specs = [
            ("malformed_json", "Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{invalid json"),
            (
                "missing_symbols",
                "Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n" + json.dumps({"path": "source.py"}),
            ),
            (
                "not_py",
                "Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n"
                + json.dumps({"path": "source.txt", "symbols": ["target_func"]}),
            ),
            (
                "empty_symbols",
                "Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n"
                + json.dumps({"path": "source.py", "symbols": []}),
            ),
            (
                "duplicate_marker",
                "Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{}\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{}",
            ),
        ]
        for name, bad_goal in bad_specs:
            with self.subTest(case=name):
                self.task["goal"] = bad_goal
                self.save_task()
                _, self.sources = runner.load_task(self.root, self.task_path)
                worker_dir = self.root / f"worker-bad-{name}"
                proposal = self.worker_evidence(worker_dir)
                proposal_sha = runner.file_sha256(proposal)
                receipt_path, _ = self.create_receipt(proposal_sha)
                with self.assertRaisesRegex(
                    runner.ProposalError, "invalid compact review specification"
                ):
                    self.invoke_fake_cli(
                        run_id=f"run-bad-{name}",
                        proposal=proposal,
                        test_evidence=receipt_path,
                    )
                self.last_popen.assert_not_called()

    def test_compact_review_pre_cli_failure_on_missing_or_failed_receipt(self):
        worker_dir = self.root / "worker-missing-receipt"
        proposal = self.worker_evidence(worker_dir)
        with self.assertRaisesRegex(
            runner.ProposalError, "compact review requires a validated passed test receipt"
        ):
            self.invoke_fake_cli(
                run_id="run-missing-receipt",
                proposal=proposal,
                test_evidence=None,
            )
        self.last_popen.assert_not_called()

        worker_dir_fail = self.root / "worker-failed-receipt"
        proposal_fail = self.worker_evidence(worker_dir_fail)
        proposal_sha_fail = runner.file_sha256(proposal_fail)
        failed_receipt_path, _ = self.create_receipt(
            proposal_sha_fail, state="FAILED", returncode=1
        )
        with self.assertRaises(SandboxError):
            self.invoke_fake_cli(
                run_id="run-failed-receipt",
                proposal=proposal_fail,
                test_evidence=failed_receipt_path,
            )
        self.last_popen.assert_not_called()

        worker_dir_mismatch = self.root / "worker-mismatched-receipt"
        proposal_mismatch = self.worker_evidence(worker_dir_mismatch)
        mismatched_receipt_path, _ = self.create_receipt(
            "0" * 64, state="PASSED", returncode=0
        )
        with self.assertRaises(SandboxError):
            self.invoke_fake_cli(
                run_id="run-mismatched-receipt",
                proposal=proposal_mismatch,
                test_evidence=mismatched_receipt_path,
            )
        self.last_popen.assert_not_called()

    def test_compact_review_pre_cli_failure_on_stale_ast_symbol(self):
        stale_spec = json.dumps(
            {"path": "source.py", "symbols": ["stale_symbol_not_defined"]}
        )
        self.task["goal"] = f"Goal\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{stale_spec}"
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)
        worker_dir = self.root / "worker-stale-symbol"
        proposal = self.worker_evidence(worker_dir)
        proposal_sha = runner.file_sha256(proposal)
        receipt_path, _ = self.create_receipt(proposal_sha)
        with self.assertRaisesRegex(
            runner.ProposalError, "compact review material build failed"
        ):
            self.invoke_fake_cli(
                run_id="run-stale-symbol",
                proposal=proposal,
                test_evidence=receipt_path,
            )
        self.last_popen.assert_not_called()

    def test_compact_review_pre_cli_failure_on_oversized_compact_prompt(self):
        worker_dir = self.root / "worker-oversized-prompt"
        proposal = self.worker_evidence(worker_dir)
        proposal_sha = runner.file_sha256(proposal)
        receipt_path, _ = self.create_receipt(proposal_sha)
        with (
            patch.object(
                runner,
                "task_preparation_notes",
                return_value={"bloat": "x" * 165_000},
            ),
            self.assertRaisesRegex(
                runner.ProposalError, "compact review prompt exceeds 160000-byte limit"
            ),
        ):
            self.invoke_fake_cli(
                run_id="run-oversized-prompt",
                proposal=proposal,
                test_evidence=receipt_path,
            )
        self.last_popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
