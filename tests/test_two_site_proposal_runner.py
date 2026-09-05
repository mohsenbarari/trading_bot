"""Offline contracts; fake CLI output is not evidence of a real Gemini run."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import two_site_proposal_runner as runner


class ProposalContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "source.py").write_text("VALUE = 1\n")
        self.task = {
            "task_id": "TEST-001", "goal": "Add one focused test.",
            "context_files": ["source.py"], "allowed_files": ["test_new.py"],
            "requirements": ["Test the value", "Test the failure"],
            "test_selectors": ["test_new.py"],
        }
        self.task_path = self.root / "task.json"
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)

    def save_task(self):
        self.task_path.write_text(json.dumps(self.task))

    def result(self):
        return {
            "status": "PROPOSAL_READY", "summary": "Added focused regression.",
            "covered_requirements": [1, 2], "findings": [],
            "changes": [{"path": "test_new.py", "original_sha256": None,
                         "content": "assert 1 == 1\n"}],
        }

    def events(self, result=None):
        return [
            {"event": "init", "conversation_id": "session-one",
             "init": {"model": runner.MODEL}},
            {"event": "result", "result": {
                "status": "SUCCESS", "conversation_id": "session-one",
                "response": json.dumps(result or self.result()),
            }},
        ]

    def parse(self, events):
        return runner.parse_stream("\n".join(json.dumps(e) for e in events),
                                   self.task, self.sources, "worker")

    def test_valid_new_and_existing_file(self):
        result = self.result()
        self.assertEqual(runner.validate_result(result, self.task, self.sources, "worker"), result)
        self.sources["test_new.py"] = {"content": "old\n", "sha256": runner.sha(b"old\n")}
        result["changes"][0]["original_sha256"] = runner.sha(b"old\n")
        self.assertEqual(runner.validate_result(result, self.task, self.sources, "worker"), result)

    def test_incomplete_worker_results_rejected(self):
        invalid = [
            {"changes": []}, {"covered_requirements": [1]},
            {"covered_requirements": [1, 1, 2]}, {"covered_requirements": [True, 2]},
            {"findings": ["still incomplete"]}, {"status": "COMPLETE"},
            {"summary": " "}, {"changes": "not a list"},
            {"covered_requirements": [1, 2, 3]},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(runner.ProposalError):
                runner.validate_result(dict(self.result(), **change), self.task, self.sources, "worker")

    def test_wrong_and_noop_changes_rejected(self):
        for field, value in [("path", "source.py"), ("original_sha256", "wrong"),
                             ("content", ""), ("content", "\0")]:
            result = self.result()
            result["changes"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(runner.ProposalError):
                runner.validate_result(result, self.task, self.sources, "worker")
        result = self.result()
        result["changes"] *= 2
        with self.assertRaises(runner.ProposalError):
            runner.validate_result(result, self.task, self.sources, "worker")
        result = self.result()
        self.sources["test_new.py"]["content"] = result["changes"][0]["content"]
        with self.assertRaises(runner.ProposalError):
            runner.validate_result(result, self.task, self.sources, "worker")

    def test_verifier_and_blocked_contracts(self):
        verified = dict(self.result(), status="VERIFIED", changes=[])
        self.assertEqual(runner.validate_result(verified, self.task, self.sources, "verifier"), verified)
        for changed in [dict(verified, changes=self.result()["changes"]),
                        dict(verified, covered_requirements=[1]),
                        dict(verified, findings=["bug"])]:
            with self.subTest(changed=changed), self.assertRaises(runner.ProposalError):
                runner.validate_result(changed, self.task, self.sources, "verifier")
        blocked = dict(verified, status="BLOCKED", covered_requirements=[], findings=["Missing context"])
        self.assertEqual(runner.validate_result(blocked, self.task, self.sources, "worker"), blocked)
        for changed in [dict(blocked, findings=[]), dict(blocked, changes=self.result()["changes"])]:
            with self.assertRaises(runner.ProposalError):
                runner.validate_result(changed, self.task, self.sources, "worker")

    def test_unique_keys_and_canonical_paths(self):
        with self.assertRaises(runner.ProposalError):
            runner.unique_json('{"key": 1, "key": 2}')
        (self.root / "alias.py").symlink_to(self.root / "source.py")
        for name in ["../source.py", "/source.py", "./source.py", "a//b.py", "a/../b.py",
                     "alias.py", ".git/config.json", ".local/a.py", "data/a.json", ".env.local",
                     "a\\b.py", "a\0.py"]:
            with self.subTest(name=name), self.assertRaises(runner.ProposalError):
                runner.relative_file(self.root, name)
        self.assertEqual(runner.relative_file(self.root, "source.py"), self.root / "source.py")

    def test_task_source_hashes_and_missing_context(self):
        self.assertEqual(self.sources["source.py"]["sha256"], runner.sha(b"VALUE = 1\n"))
        self.assertIsNone(self.sources["test_new.py"]["sha256"])
        (self.root / "source.py").unlink()
        with self.assertRaises(runner.ProposalError):
            runner.load_task(self.root, self.task_path)

    def test_bad_tasks(self):
        original = deepcopy(self.task)
        for field, value in [("requirements", []), ("requirements", ["same", "same"]),
                             ("task_id", 2), ("goal", ""), ("allowed_files", ["./bad.py"]),
                             ("test_selectors", [True]), ("unknown", 1)]:
            self.task = dict(original, **{field: value})
            self.save_task()
            with self.subTest(field=field), self.assertRaises(runner.ProposalError):
                runner.load_task(self.root, self.task_path)
        self.task = original
        self.save_task()
        (self.root / "source.py").write_text("x" * (runner.MAX_CONTEXT + 1))
        with self.assertRaises(runner.ProposalError):
            runner.load_task(self.root, self.task_path)

    def test_stream_success(self):
        result, envelope = self.parse(self.events())
        self.assertEqual(result, self.result())
        self.assertEqual(envelope["conversation_id"], "session-one")

    def test_structured_output_is_the_canonical_schema_payload(self):
        events = self.events()
        events[1]["result"]["structured_output"] = self.result()
        events[1]["result"]["response"] += '\n{"toolAction":"Submitting proposal"}'
        result, _ = self.parse(events)
        self.assertEqual(result, self.result())

    def test_invalid_structured_output_never_falls_back_to_valid_text(self):
        for invalid in [None, [], "text", {}, dict(self.result(), changes=[])]:
            events = self.events()
            events[1]["result"]["structured_output"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(runner.ProposalError):
                self.parse(events)

    def test_bad_stream_envelopes(self):
        variants = [[], [None], [{"event": "init", "init": []}],
                    [{"event": "result", "result": None}]]
        for field, value in [("status", "ERROR"), ("conversation_id", "other"),
                             ("response", None), ("denied_actions", ["read_file"])]:
            events = self.events()
            events[1]["result"][field] = value
            variants.append(events)
        for identity in [None, "", " "]:
            events = self.events()
            events[0]["conversation_id"] = events[1]["result"]["conversation_id"] = identity
            variants.append(events)
        events = self.events()
        events[0]["init"]["model"] = "wrong-model"
        variants.extend([events, self.events() + [self.events()[1]]])
        for events in variants:
            with self.subTest(events=events), self.assertRaises(runner.ProposalError):
                self.parse(events)

    def test_tool_and_subagent_attempts_rejected(self):
        for payload in [{"tool_name": "read_file"}, {"tool_info": {"name": "read_file"}},
                        {"subagent_info": {"id": "child"}}, {"step_type": "tool"},
                        {"step_type": "subagent"}]:
            events = self.events()
            events.insert(1, {"event": "step_update", "step_update": payload})
            with self.subTest(payload=payload), self.assertRaises(runner.ProposalError):
                self.parse(events)

    def test_patch_without_terminal_newlines(self):
        changes = [{"path": "test_new.py", "content": "new"}]
        self.sources["test_new.py"]["content"] = "old"
        result = runner.proposal_diff(changes, self.sources)
        self.assertIn("-old\n\\ No newline at end of file\n+new\n", result)
        self.assertEqual(result.count("\\ No newline at end of file"), 2)
        self.sources["test_new.py"]["content"] = None
        self.assertIn("--- /dev/null\n", runner.proposal_diff(changes, self.sources))

    def invoke_fake_cli(self, *, drift=None, timeout=False, denied=False,
                        result=None, events=None, raw_output=None,
                        git_side_effect=None, run_id="run-001"):
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 0
        process.pid = 987654
        def communicate(*args, **kwargs):
            if timeout:
                raise subprocess.TimeoutExpired("fake-cli", 30)
            if drift:
                drift()
            if raw_output is not None:
                if isinstance(raw_output, str):
                    return raw_output.encode(), b""
                return raw_output, b""
            evs = self.events(result=result) if events is None else events
            if denied:
                evs[1]["result"]["denied_actions"] = ["read_file"]
            return "\n".join(json.dumps(e) for e in evs).encode(), b""
        process.communicate.side_effect = communicate
        git_effects = git_side_effect if git_side_effect is not None else ["head", "", "head", ""]
        with patch.object(runner, "git", side_effect=git_effects), \
                patch.object(runner.subprocess, "Popen", return_value=process) as popen, \
                patch.object(runner.os, "killpg") as kill:
            try:
                out = runner.run(self.root, self.task_path, "worker", run_id, 30,
                                 cli=Path(__file__).resolve())
                self.assertNotIn("--dangerously-skip-permissions", popen.call_args.args[0])
                self.assertIn("--sandbox", popen.call_args.args[0])
                return out
            finally:
                if timeout:
                    kill.assert_called_once_with(process.pid, runner.signal.SIGKILL)

    def test_offline_run_only_writes_artifacts(self):
        out = self.invoke_fake_cli()
        self.assertFalse((self.root / "test_new.py").exists())
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "PROPOSAL_READY")
        self.assertEqual(status["authority"], "PROPOSAL_ONLY_NOT_ACCEPTANCE")
        self.assertEqual(status["cli_pid"], 987654)
        self.assertGreater(status["retain_until"], status["started_at"])
        self.assertEqual(status["result_sha256"], runner.sha((out / "result.json").read_bytes()))
        self.assertEqual(json.loads((out / "workspace/.agents/hooks.json").read_text()), runner.DENY_HOOK)
        with self.assertRaises(FileExistsError):
            self.invoke_fake_cli()

    def test_task_drift_records_blocked(self):
        def drift():
            self.task["goal"] = "Different goal"
            self.save_task()
        with self.assertRaisesRegex(runner.ProposalError, "task or source bytes changed"):
            self.invoke_fake_cli(drift=drift)
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "BLOCKED")
        self.assertFalse((out / "result.json").exists())

    def test_source_drift_without_git_status_change_records_blocked(self):
        def drift():
            (self.root / "source.py").write_text("VALUE = 2\n")
        with self.assertRaisesRegex(runner.ProposalError, "task or source bytes changed during generation"):
            self.invoke_fake_cli(drift=drift)
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("ProposalError: task or source bytes changed during generation", status["error"])
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((self.root / "test_new.py").exists())

    def test_git_head_change_during_generation_records_blocked(self):
        with self.assertRaisesRegex(runner.ProposalError, "Git state changed during generation"):
            self.invoke_fake_cli(git_side_effect=["head-start", "", "head-finish", ""])
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("ProposalError: Git state changed during generation", status["error"])
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((self.root / "test_new.py").exists())

    def test_git_status_change_during_generation_records_blocked(self):
        with self.assertRaisesRegex(runner.ProposalError, "Git state changed during generation"):
            self.invoke_fake_cli(git_side_effect=["head", "", "head", " M source.py\n"])
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("ProposalError: Git state changed during generation", status["error"])
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((self.root / "test_new.py").exists())

    def test_malformed_ndjson_cli_output_records_blocked(self):
        malformed = (
            json.dumps({"event": "init", "conversation_id": "session-one",
                        "init": {"model": runner.MODEL}}).encode() + b"\n"
            b'{"event": "result", "result": {malformed ndjson\n'
        )
        with self.assertRaises(json.JSONDecodeError):
            self.invoke_fake_cli(raw_output=malformed)
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("JSONDecodeError", status["error"])
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((self.root / "test_new.py").exists())

    def test_genuine_blocked_model_response_is_valid_output_never_acceptance(self):
        blocked_result = {
            "status": "BLOCKED",
            "summary": "Task blocked due to missing dependency.",
            "covered_requirements": [],
            "findings": ["Required module is missing from the environment."],
            "changes": [],
        }
        out = self.invoke_fake_cli(result=blocked_result)
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertNotEqual(status["state"], "PROPOSAL_READY")
        self.assertNotIn("error", status)
        self.assertEqual(status["authority"], "PROPOSAL_ONLY_NOT_ACCEPTANCE")
        self.assertTrue((out / "result.json").exists())
        saved_result = json.loads((out / "result.json").read_text())
        self.assertEqual(saved_result["status"], "BLOCKED")
        self.assertEqual(saved_result["findings"], ["Required module is missing from the environment."])
        self.assertEqual(saved_result["changes"], [])
        self.assertFalse((self.root / "test_new.py").exists())

    def test_timeout_and_denial_never_publish_success(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke_fake_cli(timeout=True)
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "BLOCKED")
        self.assertFalse((out / "result.json").exists())

    def test_denied_cli_is_blocked_even_with_success(self):
        with self.assertRaises(runner.ProposalError):
            self.invoke_fake_cli(denied=True)
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "BLOCKED")

    def test_task_credentials_block_before_artifacts_or_cli(self):
        self.task["goal"] = "Do not upload " + "sk-" + "x" * 30
        self.save_task()
        with patch.object(runner, "git", return_value="head"), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.ProposalError, "credential-like"):
                runner.run(self.root, self.task_path, "worker", "secret-test", 30)
            popen.assert_not_called()
        self.assertFalse((self.root / ".local").exists())

    def test_missing_cli_does_not_leave_a_running_job(self):
        with patch.object(runner, "git", return_value="head"), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaises(FileNotFoundError):
                runner.run(self.root, self.task_path, "worker", "missing-cli", 30,
                           cli=self.root / "missing-executable")
            popen.assert_not_called()
        self.assertFalse((self.root / ".local").exists())

    def test_verifier_rejects_changed_original_before_provider_call(self):
        previous = self.root / "worker"
        previous.mkdir()
        proposal = previous / "result.json"
        proposal.write_text(json.dumps(self.result()))
        baseline = {"task": self.task, "source_head": "head", "sources": self.sources}
        (previous / "task.json").write_text(json.dumps(baseline))
        (self.root / "test_new.py").write_text(self.result()["changes"][0]["content"])
        with patch.object(runner, "git", return_value="head"), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.ProposalError, "verify before applying"):
                runner.run(self.root, self.task_path, "verifier", "verify-001", 30,
                           proposal=proposal, cli=Path(__file__).resolve())
            popen.assert_not_called()
        self.assertFalse((self.root / ".local").exists())

    def test_verifier_receives_matching_original_and_cannot_apply(self):
        previous = self.root / "worker"
        previous.mkdir()
        proposal = previous / "result.json"
        proposal.write_text(json.dumps(self.result()))
        (previous / "task.json").write_text(json.dumps({
            "task": self.task, "source_head": "head", "sources": self.sources,
        }))
        verified = dict(self.result(), status="VERIFIED", changes=[])
        process = MagicMock()
        process.__enter__.return_value = process
        process.pid, process.returncode = 987654, 0
        process.communicate.return_value = (
            "\n".join(json.dumps(e) for e in self.events(verified)).encode(), b"",
        )
        with patch.object(runner, "git", side_effect=["head", "", "head", ""]), \
                patch.object(runner.subprocess, "Popen", return_value=process):
            out = runner.run(self.root, self.task_path, "verifier", "verify-001", 30,
                             proposal=proposal, cli=Path(__file__).resolve())
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "VERIFIED")
        self.assertEqual(json.loads((out / "task.json").read_text())["sources"], self.sources)
        self.assertFalse((self.root / "test_new.py").exists())


if __name__ == "__main__":
    unittest.main()
