"""Offline contracts; fake CLI output is not evidence of a real Gemini run."""
from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from scripts import two_site_proposal_runner as runner
class ProposalContracts(unittest.TestCase):
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

    def raw_result(self):
        raw = deepcopy(self.result())
        for change in raw["changes"]:
            change["replacements"] = []
        return raw

    def events(self, result=None):
        payload = self.raw_result() if result is None else result
        return [
            {"event": "init", "conversation_id": "session-one",
             "init": {"model": runner.MODEL}},
            {"event": "result", "result": {
                "status": "SUCCESS", "conversation_id": "session-one",
                "response": json.dumps(payload),
            }},
        ]

    def worker_evidence(self, directory, *, result=None, head="head",
                        conversation_id="worker-session"):
        directory.mkdir()
        proposal = directory / "result.json"
        runner.write_json(proposal, result or self.result())
        runner.write_json(directory / "task.json", {
            "task": self.task, "source_head": head, "sources": self.sources,
        })
        runner.write_json(directory / "status.json", {
            "run_id": directory.name,
            "role": "worker",
            "state": "PROPOSAL_READY",
            "conversation_id": conversation_id,
            "result_sha256": runner.file_sha256(proposal),
        })
        return proposal

    def parse(self, events):
        return runner.parse_stream("\n".join(json.dumps(e) for e in events),
                                   self.task, self.sources, "worker")

    def test_valid_new_and_existing_file(self):
        result = self.result()
        self.assertEqual(runner.validate_result(result, self.task, self.sources, "worker"), result)
        self.sources["test_new.py"] = {"content": "old\n", "sha256": runner.sha(b"old\n")}
        result["changes"][0]["original_sha256"] = runner.sha(b"old\n")
        self.assertEqual(runner.validate_result(result, self.task, self.sources, "worker"), result)

    def test_preparation_check_is_advisory_and_does_not_mutate_task(self):
        original = deepcopy(self.task)
        notes = runner.check(self.root, self.task_path)["preparation_notes"]
        self.assertEqual(notes["schema_version"], 1)
        self.assertEqual(json.loads(self.task_path.read_text()), original)

    def test_assignment_scope_failures_have_safe_actionable_codes(self):
        environment = {
            "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED": "1",
            "TWO_SITE_ASSIGNMENT_ID": "WA-TEST-001",
            "TWO_SITE_ASSIGNMENT_SHA256": "a" * 64,
        }
        cases = {
            "Task allowed_file is outside assignment scope": "TASK_PATH_OUTSIDE_SCOPE",
            "task.goal does not contain assignment ID as a bounded token": "TASK_ASSIGNMENT_TOKEN_MISSING",
            "Assignment has expired": "ASSIGNMENT_EXPIRED",
            "unexpected parser detail": "ASSIGNMENT_CONTRACT_INVALID",
        }
        for reason, code in cases.items():
            with self.subTest(code=code), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(runner, "load_expected_assignment_scope", side_effect=runner.AssignmentScopeError(reason)):
                with self.assertRaisesRegex(runner.ProposalError, rf"validation failed \[{code}\]"):
                    runner.validate_assignment_scope(self.root, self.task)

    def test_preparation_injected_before_provider_and_prompt_limits(self):
        with patch.object(runner, "git", return_value="head"), \
             patch.object(runner, "proposal_cli_context", side_effect=RuntimeError("sentinel")) as provider, \
             patch.object(runner, "task_preparation_notes", wraps=runner.task_preparation_notes) as notes:
            with self.assertRaisesRegex(RuntimeError, "sentinel"):
                runner.run(self.root, self.task_path, "worker", "notes-test", 10)
            self.assertEqual(notes.call_args.args, (self.task, self.sources))
            provider.assert_called_once()
        with patch.object(runner, "git", return_value="head"), \
             patch.object(runner, "proposal_cli_context") as provider, \
             patch.object(runner, "task_preparation_notes", return_value={"test": "x" * (2 * runner.MAX_CONTEXT)}):
            with self.assertRaisesRegex(runner.ProposalError, "smaller coherent unit"):
                runner.run(self.root, self.task_path, "worker", "notes-cap", 10)
            provider.assert_not_called()

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

    def test_generated_python_syntax_error_is_a_bounded_output_retry(self):
        result = self.result()
        result["changes"][0]["content"] = "def broken(:\n    pass\n"
        with self.assertRaisesRegex(
            runner.ProposalError,
            r"generated Python syntax is invalid: test_new\.py:1:",
        ) as ctx:
            runner.validate_result(result, self.task, self.sources, "worker")
        self.assertTrue(runner._provider_output_is_retryable(ctx.exception))

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

    def test_all_model_inputs_are_bounded_and_project_local(self):
        self.task_path.write_bytes(b"x" * (runner.MAX_CONTEXT + 1))
        with self.assertRaisesRegex(runner.ProposalError, "safe context budget"):
            runner.load_task(self.root, self.task_path)
        self.save_task()

        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "task.json"
            outside.write_text(json.dumps(self.task))
            with self.assertRaisesRegex(runner.ProposalError, "outside the project"):
                runner.load_task(self.root, outside)

        feedback = self.root / "feedback.json"
        feedback.write_bytes(b"x" * (runner.MAX_CONTEXT + 1))
        with patch.object(runner, "git", return_value="head"), \
                patch.object(runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.ProposalError, "safe context budget"):
                runner.run(self.root, self.task_path, "worker", "bounded-input", 30,
                           feedback=feedback, cli=Path(__file__).resolve())
            popen.assert_not_called()
        self.assertFalse((self.root / ".local").exists())
        self.assertNotIn("read_bytes", inspect.getsource(runner.load_task))

    def test_hardlink_to_file_outside_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside_directory:
            outside = Path(outside_directory) / "outside.py"
            outside.write_text("EXTERNAL = True\n")
            (self.root / "source.py").unlink()
            os.link(outside, self.root / "source.py")
            with self.assertRaisesRegex(runner.ProposalError, "hardlinked"):
                runner.load_task(self.root, self.task_path)

    def test_stream_success(self):
        result, envelope = self.parse(self.events())
        self.assertEqual(result, self.result())
        self.assertEqual(envelope["conversation_id"], "session-one")

    def test_raw_fixture_four_key_independence_and_negative_input_preservation(self):
        canonical_result = self.result()
        for change in canonical_result["changes"]:
            self.assertEqual(set(change.keys()), {"path", "original_sha256", "content"})
            self.assertNotIn("replacements", change)

        raw = self.raw_result()
        for change in raw["changes"]:
            self.assertEqual(set(change.keys()), {"path", "original_sha256", "content", "replacements"})
            self.assertEqual(change["replacements"], [])

        raw["changes"][0]["content"] = "mutated content\n"
        raw["changes"][0]["replacements"].append({"old": "x", "new": "y"})
        raw["extra_field"] = "unexpected"

        after_mutation = self.result()
        self.assertEqual(after_mutation, canonical_result)
        self.assertEqual(after_mutation["changes"][0]["content"], "assert 1 == 1\n")
        self.assertNotIn("replacements", after_mutation["changes"][0])
        self.assertNotIn("extra_field", after_mutation)

        empty_events = self.events({})
        empty_response = empty_events[1]["result"]["response"]
        self.assertEqual(empty_response, "{}")
        self.assertNotEqual(json.loads(empty_response), self.result())
        with self.assertRaises(runner.ProposalError):
            self.parse(empty_events)

        malformed = {"status": "PROPOSAL_READY", "changes": "not-a-list"}
        malformed_events = self.events(malformed)
        malformed_response = malformed_events[1]["result"]["response"]
        self.assertEqual(json.loads(malformed_response), malformed)
        self.assertNotEqual(json.loads(malformed_response), self.result())
        with self.assertRaises(runner.ProposalError):
            self.parse(malformed_events)

    def test_structured_output_is_the_canonical_schema_payload(self):
        events = self.events()
        events[1]["result"]["structured_output"] = self.raw_result()
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

    def test_interrupted_stream_is_retryable_without_retaining_provider_error(self):
        events = self.events()
        events[1]["result"].update(
            status="ERROR",
            error="The stream was interrupted. Please continue the task you were working on.",
            response=None,
        )
        raw = "\n".join(json.dumps(event) for event in events)
        self.assertTrue(runner.is_retryable_interrupted_stream(raw))
        with self.assertRaisesRegex(
            runner.RetryableProviderSessionError,
            "provider stream interrupted before a complete result",
        ) as ctx:
            self.parse(events)
        self.assertNotIn("Please continue", str(ctx.exception))

        events.insert(1, {"event": "step_update", "step_update": {"tool_name": "read_file"}})
        self.assertFalse(runner.is_retryable_interrupted_stream(
            "\n".join(json.dumps(event) for event in events)
        ))

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
                        git_side_effect=None, run_id="run-001",
                        cli_identities=None, feedback=None, proposal=None,
                        process_returncode=0):
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = process_returncode
        process.pid = 987654
        process.poll.return_value = None

        def capture(fake_process, _input, stdout_path, stderr_path, _timeout, **_kwargs):
            cmd = popen.call_args.args[0] if popen.call_args else getattr(fake_process, "args", [])
            if timeout:
                runner.terminate_process(fake_process)
                raise subprocess.TimeoutExpired("fake-cli", 30)
            if drift:
                drift()
            if raw_output is not None:
                if isinstance(raw_output, str):
                    output = raw_output.encode()
                else:
                    output = raw_output
            else:
                if events is not None:
                    evs = events
                elif result is not None:
                    evs = self.events(result=result)
                else:
                    schema = {}
                    if "--json-schema" in cmd:
                        schema = json.loads(cmd[cmd.index("--json-schema") + 1])
                    changes_schema = schema.get("properties", {}).get("changes", {})
                    items_schema = changes_schema.get("items", {})
                    branches = items_schema.get("anyOf") or ([items_schema] if "properties" in items_schema else [])
                    path_shas = {}
                    for branch in branches:
                        props = branch.get("properties", {})
                        p = props.get("path", {}).get("enum", [None])[0] or props.get("path", {}).get("const")
                        if p and p not in path_shas:
                            s_prop = props.get("original_sha256", {})
                            path_shas[p] = s_prop.get("enum", [None])[0] if "enum" in s_prop else s_prop.get("const")
                    if not path_shas:
                        path_shas = {"test_new.py": None}

                    context = {}
                    if _input:
                        try:
                            prompt_text = json.loads(_input).get("message", {}).get("content", "")
                            if "\nCONTEXT_JSON:\n" in prompt_text:
                                context = json.loads(prompt_text.split("\nCONTEXT_JSON:\n", 1)[1])
                        except Exception:
                            pass
                    if not context and (stdout_path.parent / "task.json").exists():
                        try:
                            context = json.loads((stdout_path.parent / "task.json").read_text())
                        except Exception:
                            pass

                    sources_ctx = context.get("sources", {})
                    changes = []
                    for p, s in path_shas.items():
                        baseline = sources_ctx.get(p, {}).get("content")
                        if baseline is None and hasattr(self, "sources"):
                            baseline = self.sources.get(p, {}).get("content")
                        if baseline == "assert 1 == 1\n" or s == runner.sha(b"assert 1 == 1\n"):
                            content = "assert 2 == 2\n"
                        else:
                            content = "assert 1 == 1\n"
                        changes.append({
                            "path": p,
                            "original_sha256": s,
                            "content": content,
                            "replacements": [],
                        })
                    req_count = len(context.get("task", {}).get("requirements", [None, None]))
                    derived = {
                        "status": "PROPOSAL_READY",
                        "summary": "Added focused regression.",
                        "covered_requirements": list(range(1, req_count + 1)),
                        "findings": [],
                        "changes": changes,
                    }
                    evs = self.events(result=derived)
                evs = json.loads(json.dumps(evs))
                if "--model" in cmd:
                    invoked_model = cmd[cmd.index("--model") + 1]
                    for event in evs:
                        if event.get("event") == "init":
                            event["init"]["model"] = invoked_model
                if denied:
                    evs[1]["result"]["denied_actions"] = ["read_file"]
                output = "\n".join(json.dumps(e) for e in evs).encode()
            stdout_path.write_bytes(output)
            stderr_path.write_bytes(b"")

        identity = {"path": str(Path(__file__).resolve()), "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256}
        git_effects = git_side_effect if git_side_effect is not None else ["head", "", "head", ""]
        identity_patch = (patch.object(runner, "cli_identity", return_value=identity)
                          if cli_identities is None else
                          patch.object(runner, "cli_identity", side_effect=cli_identities))
        with patch.object(runner, "git", side_effect=git_effects), \
                identity_patch, \
                patch.object(runner, "validate_runtime_contract", return_value=runner.RUNTIME_CONTRACT), \
                patch.object(runner, "capture_bounded_process", side_effect=capture), \
                patch.object(runner.subprocess, "Popen", return_value=process) as popen, \
                patch.object(runner.os, "killpg") as kill:
            try:
                out = runner.run(self.root, self.task_path, "worker", run_id, 30,
                                 feedback=feedback, proposal=proposal,
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
        self.assertEqual(status["cli"]["version"], runner.EXPECTED_CLI_VERSION)
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

    def test_cli_identity_change_during_generation_is_blocked(self):
        initial = {"path": "/reviewed/agy", "version": runner.EXPECTED_CLI_VERSION,
                   "sha256": runner.EXPECTED_CLI_SHA256}
        changed = dict(initial, version="unexpected-version")
        with self.assertRaisesRegex(runner.ProposalError, "identity changed"):
            self.invoke_fake_cli(cli_identities=[initial, changed])
        out = self.root / ".local/two-site-refactor/proposals/run-001"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("identity changed", status["error"])

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
        proposal = self.worker_evidence(previous)
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
        proposal = self.worker_evidence(previous)
        verified = dict(self.result(), status="VERIFIED", changes=[])
        process = MagicMock()
        process.__enter__.return_value = process
        process.pid, process.returncode = 987654, 0
        identity = {"path": str(Path(__file__).resolve()), "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256}

        def capture(_process, _input, stdout_path, stderr_path, _timeout, **_kwargs):
            stdout_path.write_bytes(
                "\n".join(json.dumps(e) for e in self.events(verified)).encode()
            )
            stderr_path.write_bytes(b"")

        with patch.object(runner, "git", side_effect=["head", "", "head", ""]), \
                patch.object(runner, "cli_identity", return_value=identity), \
                patch.object(runner, "capture_bounded_process", side_effect=capture), \
                patch.object(runner.subprocess, "Popen", return_value=process):
            out = runner.run(self.root, self.task_path, "verifier", "verify-001", 30,
                             proposal=proposal, cli=Path(__file__).resolve())
        self.assertEqual(json.loads((out / "status.json").read_text())["state"], "VERIFIED")
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["input_worker_run_id"], "worker")
        self.assertEqual(status["input_worker_conversation_id"], "worker-session")
        self.assertEqual(status["input_proposal_sha256"], runner.file_sha256(proposal))
        self.assertEqual(json.loads((out / "task.json").read_text())["sources"], self.sources)
        self.assertFalse((self.root / "test_new.py").exists())

    def test_correction_feedback_is_exactly_bound_to_prior_proposal(self):
        proposal = self.worker_evidence(self.root / "prior-worker")
        proposal_sha = runner.file_sha256(proposal)
        feedback = self.root / "feedback.json"
        runner.write_json(feedback, {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": proposal_sha,
            "findings": ["test_new.py lacks the required failure assertion"],
            "test_evidence": [],
        })
        out = self.invoke_fake_cli(
            feedback=feedback, proposal=proposal, run_id="correction-001",
        )
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["input_proposal_sha256"], proposal_sha)
        self.assertEqual(status["input_feedback_sha256"], runner.file_sha256(feedback))
        supplied = json.loads((out / "task.json").read_text())
        self.assertEqual(supplied["feedback"]["reviewer"], "gemini")

        invalid = json.loads(feedback.read_text())
        invalid["reviewed_proposal_sha256"] = "0" * 64
        runner.write_json(feedback, invalid)
        with self.assertRaisesRegex(runner.ProposalError, "different proposal"):
            self.invoke_fake_cli(
                feedback=feedback, proposal=proposal, run_id="correction-002",
            )

    def test_reviewer_or_correction_must_use_fresh_gemini_session(self):
        proposal = self.worker_evidence(
            self.root / "same-session-worker", conversation_id="session-one"
        )
        feedback = self.root / "same-session-feedback.json"
        runner.write_json(feedback, {
            "reviewer": "codex",
            "reviewed_proposal_sha256": runner.file_sha256(proposal),
            "findings": ["Add the missing regression assertion"],
            "test_evidence": ["focused selector failed before commit"],
        })
        with self.assertRaisesRegex(runner.ProposalError, "reused the worker session"):
            self.invoke_fake_cli(
                feedback=feedback, proposal=proposal, run_id="same-session-correction",
            )

    def test_cli_identity_requires_exact_reviewed_binary_and_version(self):
        cli = self.root / "agy"
        cli.write_text("#!/usr/bin/env python3\nprint('1.1.27')\n")
        cli.chmod(0o755)
        digest = runner.file_sha256(cli)
        with patch.object(runner, "EXPECTED_CLI_SHA256", digest):
            identity = runner.cli_identity(cli)
        self.assertEqual(identity, {
            "path": str(cli), "version": "1.1.27", "sha256": digest,
        })

        with patch.object(runner, "EXPECTED_CLI_SHA256", "0" * 64), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.ProposalError, "hash"):
                runner.cli_identity(cli)
            popen.assert_not_called()

        with patch.object(runner, "EXPECTED_CLI_SHA256", digest), \
                patch.object(runner, "EXPECTED_CLI_VERSION", "1.1.26"):
            with self.assertRaisesRegex(runner.ProposalError, "version"):
                runner.cli_identity(cli)

    def test_concurrent_runner_is_rejected_before_provider_start(self):
        identity = {"path": str(Path(__file__).resolve()),
                    "version": runner.EXPECTED_CLI_VERSION,
                    "sha256": runner.EXPECTED_CLI_SHA256}
        descriptor, _local = runner.acquire_proposal_lock(self.root)
        self.addCleanup(runner.release_proposal_lock, descriptor)
        with patch.object(runner, "git", side_effect=["head", ""]), \
                patch.object(runner, "cli_identity", return_value=identity), \
                patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(runner.ProposalError, "already active"):
                runner.run(self.root, self.task_path, "worker", "parallel-run", 30,
                           cli=Path(__file__).resolve())
            popen.assert_not_called()

    def test_bounded_capture_streams_without_communicate(self):
        stdout_path = self.root / "bounded.stdout"
        stderr_path = self.root / "bounded.stderr"
        payload = b"x" * 200_000
        with subprocess.Popen(
            [sys.executable, "-c",
             "import sys; data=sys.stdin.buffer.read(); print(len(data)); print('ok', file=sys.stderr)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            runner.capture_bounded_process(
                process, payload, stdout_path, stderr_path, 10, max_output=1024,
            )
        self.assertEqual(stdout_path.read_text().strip(), str(len(payload)))
        self.assertEqual(stderr_path.read_text().strip(), "ok")
        self.assertNotIn("communicate", inspect.getsource(runner.capture_bounded_process))

    def test_bounded_capture_kills_at_combined_limit_before_full_collection(self):
        marker = self.root / "child-finished"
        stdout_path = self.root / "limited.stdout"
        stderr_path = self.root / "limited.stderr"
        child = (
            "import os,pathlib,time; "
            "os.write(1,b'x'*65536); os.write(2,b'y'*65536); "
            "time.sleep(5); "
            f"pathlib.Path({str(marker)!r}).write_text('done')"
        )
        started_at = time.monotonic()
        with subprocess.Popen(
            [sys.executable, "-c", child],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        ) as process:
            with self.assertRaisesRegex(runner.ProposalError, "output exceeds"):
                runner.capture_bounded_process(
                    process, b"", stdout_path, stderr_path, 10, max_output=4096,
                )
        self.assertEqual(stdout_path.stat().st_size + stderr_path.stat().st_size, 4096)
        self.assertLess(time.monotonic() - started_at, 2)
        self.assertFalse(marker.exists())
        self.assertIsNotNone(process.returncode)

    def test_worker_and_verifier_schema_contracts(self):
        worker_schema = runner.schema("worker")
        self.assertEqual(worker_schema["type"], "object")
        self.assertFalse(worker_schema["additionalProperties"])
        self.assertEqual(
            set(worker_schema["required"]),
            {"status", "summary", "covered_requirements", "findings", "changes"},
        )
        self.assertEqual(worker_schema["properties"]["status"]["enum"], ["PROPOSAL_READY", "BLOCKED"])

        changes_schema = worker_schema["properties"]["changes"]
        self.assertEqual(changes_schema["type"], "array")
        change_item = changes_schema["items"]
        self.assertEqual(change_item["type"], "object")
        self.assertFalse(change_item["additionalProperties"])
        self.assertEqual(
            set(change_item["required"]),
            {"path", "original_sha256", "content", "replacements"},
        )
        self.assertEqual(change_item["properties"]["path"]["type"], "string")
        self.assertEqual(change_item["properties"]["original_sha256"]["type"], ["string", "null"])
        self.assertEqual(change_item["properties"]["content"]["type"], ["string", "null"])

        rep_schema = change_item["properties"]["replacements"]
        self.assertEqual(rep_schema["type"], "array")
        rep_item = rep_schema["items"]
        self.assertEqual(rep_item["type"], "object")
        self.assertFalse(rep_item["additionalProperties"])
        self.assertEqual(set(rep_item["required"]), {"old", "new"})
        self.assertEqual(rep_item["properties"]["old"]["type"], "string")
        self.assertEqual(rep_item["properties"]["new"]["type"], "string")

        verifier_schema = runner.schema("verifier")
        self.assertEqual(
            verifier_schema["properties"]["status"]["enum"],
            ["VERIFIED", "CHANGES_REQUIRED", "BLOCKED"],
        )
        self.assertEqual(verifier_schema["properties"]["changes"]["type"], "array")
        self.assertNotIn("minItems", verifier_schema["properties"]["changes"])

        verified = {
            "status": "VERIFIED",
            "summary": "Verified proposal without authoring changes.",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [],
        }
        self.assertEqual(
            runner.validate_result(verified, self.task, self.sources, "verifier"),
            verified,
        )
        with self.assertRaisesRegex(runner.ProposalError, "verifier cannot author changes"):
            runner.validate_result(
                dict(verified, changes=[{"path": "test_new.py", "original_sha256": None, "content": "x"}]),
                self.task,
                self.sources,
                "verifier",
            )

    def test_normalize_result_modes_and_immutability(self):
        task = dict(self.task, allowed_files=["source.py", "test_new.py"])
        source_text = "FIRST = 1\nSECOND = 2\nTHIRD = 3\n"
        source_sha = runner.sha(source_text.encode("utf-8"))
        sources = {
            "source.py": {"sha256": source_sha, "content": source_text},
            "test_new.py": {"sha256": None, "content": None},
        }

        # 1. Legacy three-key full content
        legacy_change = {
            "path": "test_new.py",
            "original_sha256": None,
            "content": "assert legacy == True\n",
        }
        legacy_result = dict(self.result(), changes=[legacy_change])
        legacy_copy = deepcopy(legacy_result)
        norm_legacy = runner.normalize_result(legacy_result, task, sources, "worker")
        self.assertEqual(legacy_result, legacy_copy)
        self.assertEqual(norm_legacy["changes"], [{
            "path": "test_new.py",
            "original_sha256": None,
            "content": "assert legacy == True\n",
        }])
        self.assertEqual(set(norm_legacy["changes"][0].keys()), {"path", "original_sha256", "content"})

        # 2. Explicit four-key full content
        four_key_change = {
            "path": "test_new.py",
            "original_sha256": None,
            "content": "assert four_key == True\n",
            "replacements": [],
        }
        four_key_result = dict(self.result(), changes=[four_key_change])
        four_key_copy = deepcopy(four_key_result)
        norm_four_key = runner.normalize_result(four_key_result, task, sources, "worker")
        self.assertEqual(four_key_result, four_key_copy)
        self.assertEqual(norm_four_key["changes"], [{
            "path": "test_new.py",
            "original_sha256": None,
            "content": "assert four_key == True\n",
        }])
        self.assertEqual(set(norm_four_key["changes"][0].keys()), {"path", "original_sha256", "content"})

        # 3. One existing-file exact replacement
        one_rep_change = {
            "path": "source.py",
            "original_sha256": source_sha,
            "content": None,
            "replacements": [{"old": "SECOND = 2", "new": "SECOND = 20"}],
        }
        one_rep_result = dict(self.result(), changes=[one_rep_change])
        one_rep_copy = deepcopy(one_rep_result)
        norm_one_rep = runner.normalize_result(one_rep_result, task, sources, "worker")
        self.assertEqual(one_rep_result, one_rep_copy)
        self.assertEqual(norm_one_rep["changes"], [{
            "path": "source.py",
            "original_sha256": source_sha,
            "content": "FIRST = 1\nSECOND = 20\nTHIRD = 3\n",
        }])
        self.assertEqual(set(norm_one_rep["changes"][0].keys()), {"path", "original_sha256", "content"})

        # 4. Multiple disjoint replacements in reverse order
        multi_rev_change = {
            "path": "source.py",
            "original_sha256": source_sha,
            "content": None,
            "replacements": [
                {"old": "THIRD = 3", "new": "THIRD = 300"},
                {"old": "FIRST = 1", "new": "FIRST = 100"},
            ],
        }
        multi_rev_result = dict(self.result(), changes=[multi_rev_change])
        multi_rev_copy = deepcopy(multi_rev_result)
        norm_multi_rev = runner.normalize_result(multi_rev_result, task, sources, "worker")
        self.assertEqual(multi_rev_result, multi_rev_copy)
        self.assertEqual(norm_multi_rev["changes"], [{
            "path": "source.py",
            "original_sha256": source_sha,
            "content": "FIRST = 100\nSECOND = 2\nTHIRD = 300\n",
        }])
        self.assertEqual(set(norm_multi_rev["changes"][0].keys()), {"path", "original_sha256", "content"})

        # 5. One proposal mixing replacement mode for an existing file with full mode for a new file
        mixed_changes = [
            {
                "path": "source.py",
                "original_sha256": source_sha,
                "content": None,
                "replacements": [{"old": "FIRST = 1", "new": "FIRST = 999"}],
            },
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "assert FIRST == 999\n",
                "replacements": [],
            },
        ]
        mixed_result = dict(self.result(), changes=mixed_changes)
        mixed_copy = deepcopy(mixed_result)
        norm_mixed = runner.normalize_result(mixed_result, task, sources, "worker")
        self.assertEqual(mixed_result, mixed_copy)
        self.assertEqual(norm_mixed["changes"], [
            {
                "path": "source.py",
                "original_sha256": source_sha,
                "content": "FIRST = 999\nSECOND = 2\nTHIRD = 3\n",
            },
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "assert FIRST == 999\n",
            },
        ])
        for ch in norm_mixed["changes"]:
            self.assertEqual(set(ch.keys()), {"path", "original_sha256", "content"})

    def test_rejection_table_for_normalization_and_validation(self):
        task = dict(self.task, allowed_files=["source.py", "test_new.py", "not_in_sources.py"])
        source_text = "VALUE = 1\n"
        source_sha = runner.sha(source_text.encode("utf-8"))
        sources = {
            "source.py": {"sha256": source_sha, "content": source_text},
            "test_new.py": {"sha256": None, "content": None},
        }

        table = [
            (
                "mixed_content_and_replacements",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": "VALUE = 2\n", "replacements": [{"old": "1", "new": "2"}]}],
                "mixed change content and replacements",
            ),
            (
                "empty_content_and_replacements",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": None, "replacements": []}],
                "empty change content and replacements",
            ),
            (
                "extra_key_in_change",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": "VALUE = 2\n", "replacements": [], "extra": "forbidden"}],
                "invalid change shape",
            ),
            (
                "missing_content_key",
                [{"path": "source.py", "original_sha256": source_sha, "replacements": []}],
                "invalid change shape",
            ),
            (
                "wrong_keys",
                [{"file": "source.py", "diff": "---"}],
                "invalid change shape",
            ),
            (
                "change_not_a_dict",
                ["not_a_dict"],
                "invalid change shape",
            ),
            (
                "content_is_int",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": 12345, "replacements": []}],
                "invalid change shape",
            ),
            (
                "replacements_is_none",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": "VALUE = 2\n", "replacements": None}],
                "invalid change shape",
            ),
            (
                "legacy_with_null_content",
                [{"path": "source.py", "original_sha256": source_sha, "content": None}],
                "invalid change shape",
            ),
            (
                "replacement_item_not_dict",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": None, "replacements": ["invalid"]}],
                "replacement item must be a dict with exactly 'old' and 'new'",
            ),
            (
                "replacement_item_empty_old",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": None, "replacements": [{"old": "", "new": "x"}]}],
                "replacement old must be a nonempty string",
            ),
            (
                "path_not_string",
                [{"path": 123, "original_sha256": None, "content": "assert True\n", "replacements": []}],
                "changed path is duplicated or outside assignment",
            ),
            (
                "path_not_in_allowed_files",
                [{"path": "other.py", "original_sha256": None, "content": "assert True\n", "replacements": []}],
                "changed path is duplicated or outside assignment",
            ),
            (
                "path_in_allowed_files_but_not_in_sources",
                [{"path": "not_in_sources.py", "original_sha256": None,
                  "content": "assert True\n", "replacements": []}],
                "changed path is duplicated or outside assignment",
            ),
            (
                "duplicate_path",
                [
                    {"path": "test_new.py", "original_sha256": None, "content": "assert True\n", "replacements": []},
                    {"path": "test_new.py", "original_sha256": None, "content": "assert False\n", "replacements": []},
                ],
                "changed path is duplicated or outside assignment",
            ),
            (
                "new_file_replacement_mode",
                [{"path": "test_new.py", "original_sha256": None,
                  "content": None, "replacements": [{"old": "foo", "new": "bar"}]}],
                "new-file replacement is not supported",
            ),
            (
                "existing_file_hash_mismatch",
                [{"path": "source.py", "original_sha256": "0" * 64,
                  "content": "VALUE = 2\n", "replacements": []}],
                "proposal is based on different source bytes",
            ),
            (
                "new_file_hash_mismatch",
                [{"path": "test_new.py", "original_sha256": "0" * 64,
                  "content": "assert True\n", "replacements": []}],
                "proposal is based on different source bytes",
            ),
            (
                "noop_unchanged_existing_file_full_mode",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": source_text, "replacements": []}],
                "empty or unchanged file",
            ),
            (
                "noop_empty_file_full_mode",
                [{"path": "test_new.py", "original_sha256": None,
                  "content": "   \n", "replacements": []}],
                "empty or unchanged file",
            ),
            (
                "unsafe_null_byte_in_replacement",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": None, "replacements": [{"old": "1", "new": "1\0"}]}],
                "unsafe generated content",
            ),
            (
                "unsafe_secret_in_replacement",
                [{"path": "source.py", "original_sha256": source_sha,
                  "content": None, "replacements": [{"old": "1", "new": "sk-" + "k" * 30}]}],
                "unsafe generated content",
            ),
            (
                "unsafe_null_byte_in_full_mode",
                [{"path": "test_new.py", "original_sha256": None,
                  "content": "assert True\0\n", "replacements": []}],
                "unsafe generated content",
            ),
            (
                "unsafe_secret_in_full_mode",
                [{"path": "test_new.py", "original_sha256": None,
                  "content": "KEY = 'sk-" + "s" * 30 + "'\n", "replacements": []}],
                "unsafe generated content",
            ),
        ]

        for case_name, changes_payload, expected_err in table:
            with self.subTest(case=case_name):
                candidate = dict(self.result(), changes=changes_payload)
                with self.assertRaisesRegex(runner.ProposalError, expected_err):
                    norm = runner.normalize_result(candidate, task, sources, "worker")
                    runner.validate_result(norm, task, sources, "worker")

    def test_out_of_assignment_path_rejected_before_source_lookup(self):
        mock_sources = MagicMock()
        task = {
            "task_id": "TEST-001",
            "goal": "Test out of assignment path rejection",
            "context_files": [],
            "allowed_files": ["allowed.py"],
            "requirements": ["req1"],
            "test_selectors": ["test.py"],
        }
        proposal = {
            "status": "PROPOSAL_READY",
            "summary": "Out of assignment path",
            "covered_requirements": [1],
            "findings": [],
            "changes": [{
                "path": "outside_assignment.py",
                "original_sha256": None,
                "content": "value = 1\n",
                "replacements": [],
            }],
        }
        with self.assertRaisesRegex(runner.ProposalError, "changed path is duplicated or outside assignment"):
            runner.normalize_result(proposal, task, mock_sources, "worker")
        mock_sources.__contains__.assert_not_called()
        mock_sources.__getitem__.assert_not_called()

    def test_parse_stream_converts_replacements_and_ignores_tool_parameters(self):
        task = dict(self.task, allowed_files=["source.py", "test_new.py"])
        source_sha = self.sources["source.py"]["sha256"]

        tool_parameters = {
            "status": "PROPOSAL_READY",
            "summary": "Noncanonical tool call parameters",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [{
                "path": "source.py",
                "original_sha256": source_sha,
                "content": "VALUE = 99999\n",
                "replacements": [],
            }],
        }
        structured_output = {
            "status": "PROPOSAL_READY",
            "summary": "Canonical structured output with replacement",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [{
                "path": "source.py",
                "original_sha256": source_sha,
                "content": None,
                "replacements": [{"old": "VALUE = 1", "new": "VALUE = 42"}],
            }],
        }

        stream_lines = [
            json.dumps({"event": "init", "conversation_id": "session-test", "init": {"model": runner.MODEL}}),
            json.dumps({
                "event": "step_update",
                "conversation_id": "session-test",
                "step_update": {
                    "step_type": "tool",
                    "tool_name": "finish",
                    "tool_info": {
                        "name": "finish",
                        "parameters": tool_parameters,
                    },
                },
            }),
            json.dumps({
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "conversation_id": "session-test",
                    "structured_output": structured_output,
                    "response": json.dumps(tool_parameters),
                },
            }),
        ]
        raw_stream = "\n".join(stream_lines)

        result, envelope = runner.parse_stream(raw_stream, task, self.sources, "worker")

        self.assertEqual(result["status"], "PROPOSAL_READY")
        self.assertEqual(result["summary"], "Canonical structured output with replacement")
        self.assertEqual(len(result["changes"]), 1)
        canonical_change = result["changes"][0]
        self.assertEqual(canonical_change["path"], "source.py")
        self.assertEqual(canonical_change["original_sha256"], source_sha)
        self.assertEqual(canonical_change["content"], "VALUE = 42\n")
        self.assertEqual(set(canonical_change.keys()), {"path", "original_sha256", "content"})
        self.assertNotIn("replacements", canonical_change)

        self.assertNotEqual(canonical_change["content"], tool_parameters["changes"][0]["content"])
        self.assertEqual(envelope["conversation_id"], "session-test")
        self.assertEqual(
            envelope["structured_output"]["changes"][0]["replacements"],
            [{"old": "VALUE = 1", "new": "VALUE = 42"}],
        )

    def test_fake_cli_persists_materialized_bytes_and_preserves_worktree(self):
        self.task["allowed_files"] = ["source.py", "test_new.py"]
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)

        source_path = self.root / "source.py"
        source_bytes_before = source_path.read_bytes()
        source_stat_before = runner.file_fingerprint(source_path.stat())

        source_sha = self.sources["source.py"]["sha256"]
        structured_proposal = {
            "status": "PROPOSAL_READY",
            "summary": "Exact replacement applied offline via CLI",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [
                {
                    "path": "source.py",
                    "original_sha256": source_sha,
                    "content": None,
                    "replacements": [{"old": "VALUE = 1\n", "new": "VALUE = 999\n"}],
                },
                {
                    "path": "test_new.py",
                    "original_sha256": None,
                    "content": "assert VALUE == 999\n",
                    "replacements": [],
                },
            ],
        }

        events = [
            {"event": "init", "conversation_id": "session-one", "init": {"model": runner.MODEL}},
            {
                "event": "step_update",
                "conversation_id": "session-one",
                "step_update": {
                    "step_type": "tool",
                    "tool_name": "finish",
                    "tool_info": {
                        "name": "finish",
                        "parameters": {
                            "status": "PROPOSAL_READY",
                            "summary": "Noncanonical parameter summary",
                            "covered_requirements": [1, 2],
                            "findings": [],
                            "changes": [
                                {
                                    "path": "source.py",
                                    "original_sha256": source_sha,
                                    "content": "NONCANONICAL\n",
                                    "replacements": [],
                                },
                            ],
                        },
                    },
                },
            },
            {"event": "result", "result": {
                "status": "SUCCESS",
                "conversation_id": "session-one",
                "structured_output": structured_proposal,
                "response": "Response text",
            }},
        ]

        out = self.invoke_fake_cli(events=events, run_id="cli-persist-001")

        result_file = out / "result.json"
        self.assertTrue(result_file.exists())
        saved_result = json.loads(result_file.read_text())
        self.assertEqual(saved_result["status"], "PROPOSAL_READY")
        self.assertEqual(saved_result["changes"], [
            {
                "path": "source.py",
                "original_sha256": source_sha,
                "content": "VALUE = 999\n",
            },
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "assert VALUE == 999\n",
            },
        ])
        for ch in saved_result["changes"]:
            self.assertEqual(set(ch.keys()), {"path", "original_sha256", "content"})

        patch_file = out / "proposal.patch"
        self.assertTrue(patch_file.exists())
        patch_text = patch_file.read_text()
        self.assertIn("--- a/source.py", patch_text)
        self.assertIn("+++ b/source.py", patch_text)
        self.assertIn("-VALUE = 1\n", patch_text)
        self.assertIn("+VALUE = 999\n", patch_text)
        self.assertIn("--- /dev/null", patch_text)
        self.assertIn("+++ b/test_new.py", patch_text)
        self.assertIn("+assert VALUE == 999\n", patch_text)

        self.assertEqual(source_path.read_bytes(), source_bytes_before)
        self.assertEqual(source_path.read_text(), "VALUE = 1\n")
        self.assertEqual(runner.file_fingerprint(source_path.stat()), source_stat_before)
        self.assertFalse((self.root / "test_new.py").exists())

        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "PROPOSAL_READY")
        self.assertEqual(status["result_sha256"], runner.file_sha256(result_file))

    def test_parse_stream_canonical_result_budget_boundary(self):
        events = self.events()
        events[1]["result"]["structured_output"] = self.raw_result()
        stream = "\n".join(json.dumps(e) for e in events)
        result = runner.validate_result(
            runner.normalize_result(events[1]["result"]["structured_output"], self.task, self.sources, "worker"),
            self.task,
            self.sources,
            "worker",
        )
        exact_length = len(runner.serialize_json(result))
        with patch.object(runner, "MAX_CONTEXT", exact_length):
            accepted, envelope = runner.parse_stream(stream, self.task, self.sources, "worker")
            self.assertEqual(accepted, result)
            self.assertEqual(envelope["conversation_id"], "session-one")
        with patch.object(runner, "MAX_CONTEXT", exact_length - 1):
            with self.assertRaisesRegex(runner.ProposalError, "canonical proposal exceeds the safe context budget"):
                runner.parse_stream(stream, self.task, self.sources, "worker")

    def test_write_json_unicode_formatting_and_cleanup(self):
        target = self.root / "payload.json"
        tmp_target = target.with_suffix(target.suffix + ".tmp")
        value = {"greeting": "こんにちは", "summary": "Café ☕"}
        runner.write_json(target, value)

        file_bytes = target.read_bytes()
        self.assertEqual(file_bytes, runner.serialize_json(value))

        # ensure_ascii false preserves raw UTF-8 characters rather than \\u escapes
        self.assertIn("こんにちは".encode("utf-8"), file_bytes)
        self.assertIn("Café ☕".encode("utf-8"), file_bytes)
        self.assertNotIn(b"\\u", file_bytes)
        text = file_bytes.decode("utf-8")
        self.assertIn("こんにちは", text)
        self.assertIn("Café ☕", text)

        # Indentation is present
        self.assertIn("\n  \"greeting\":", text)
        self.assertTrue(any(line.startswith("  ") for line in text.splitlines()))

        # Exactly one terminal newline exists
        self.assertTrue(file_bytes.endswith(b"\n"))
        self.assertFalse(file_bytes.endswith(b"\n\n"))
        self.assertEqual(len(file_bytes) - len(file_bytes.rstrip(b"\n")), 1)

        # No .tmp file remains after atomic replacement
        self.assertFalse(tmp_target.exists())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_correction_delta_rebasing_and_persisted_evidence(self):
        self.task["allowed_files"] = ["source.py", "test_new.py"]
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)
        source_sha = self.sources["source.py"]["sha256"]
        self.assertIsNotNone(source_sha)
        self.assertIsNone(self.sources["test_new.py"]["sha256"])

        prior_changes = [
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "assert 1 == 1\n",
            },
            {
                "path": "source.py",
                "original_sha256": source_sha,
                "content": "VALUE = 2\n",
            },
        ]
        prior_result = {
            "status": "PROPOSAL_READY",
            "summary": "Prior proposal changing both allowed files.",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": prior_changes,
        }
        prior_proposal = self.worker_evidence(
            self.root / "prior-worker-delta",
            result=prior_result,
            conversation_id="worker-delta-session",
        )
        prior_proposal_sha = runner.file_sha256(prior_proposal)
        prior_proposal_text = prior_proposal.read_text()

        feedback = self.root / "feedback-delta.json"
        runner.write_json(feedback, {
            "reviewer": "codex",
            "reviewed_proposal_sha256": prior_proposal_sha,
            "findings": ["test_new.py assertion must verify VALUE == 2"],
            "test_evidence": ["assertion failed on 1 != 2"],
        })

        expected_candidate_sources = runner.build_candidate_sources(
            self.task["allowed_files"], self.sources, prior_changes
        )
        candidate_new_sha = expected_candidate_sources["test_new.py"]["sha256"]
        candidate_source_sha = expected_candidate_sources["source.py"]["sha256"]
        self.assertEqual(candidate_new_sha, runner.sha(b"assert 1 == 1\n"))
        self.assertEqual(candidate_source_sha, runner.sha(b"VALUE = 2\n"))
        expected_candidate_sources_sha = runner.sha(runner.serialize_json(expected_candidate_sources))

        forced_schema = runner.schema(
            "worker", self.task, expected_candidate_sources, ("test_new.py",)
        )["properties"]["changes"]["items"]["anyOf"]
        new_file_branches = [
            branch for branch in forced_schema
            if branch["properties"]["path"]["enum"] == ["test_new.py"]
        ]
        self.assertEqual(len(new_file_branches), 1)
        self.assertEqual(new_file_branches[0]["properties"]["content"]["minLength"], 1)
        self.assertEqual(new_file_branches[0]["properties"]["replacements"]["maxItems"], 0)

        delta_payload = {
            "status": "PROPOSAL_READY",
            "summary": "Corrected assertion in test_new.py to assert 2 == 2.",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [
                {
                    "path": "test_new.py",
                    "original_sha256": candidate_new_sha,
                    "content": "assert 2 == 2\n",
                    "replacements": [],
                },
            ],
        }

        out = self.invoke_fake_cli(
            feedback=feedback,
            proposal=prior_proposal,
            result=delta_payload,
            run_id="correction-delta-run",
        )

        status_file = out / "status.json"
        delta_file = out / "delta-result.json"
        result_file = out / "result.json"
        patch_file = out / "proposal.patch"
        task_file = out / "task.json"
        prompt_file = out / "prompt.txt"

        self.assertTrue(status_file.exists())
        self.assertTrue(delta_file.exists())
        self.assertTrue(result_file.exists())
        self.assertTrue(patch_file.exists())
        self.assertTrue(task_file.exists())
        self.assertTrue(prompt_file.exists())

        status = json.loads(status_file.read_text())
        self.assertEqual(status["state"], "PROPOSAL_READY")
        self.assertEqual(status["candidate_sources_sha256"], expected_candidate_sources_sha)
        self.assertEqual(
            status["candidate_sources_sha256"],
            runner.sha(runner.serialize_json(expected_candidate_sources)),
        )
        self.assertEqual(status["delta_result_sha256"], runner.file_sha256(delta_file))
        self.assertEqual(status["delta_result_sha256"], runner.sha(delta_file.read_bytes()))
        self.assertEqual(status["result_sha256"], runner.file_sha256(result_file))
        self.assertEqual(status["result_sha256"], runner.sha(result_file.read_bytes()))

        # delta-result.json must contain only the candidate-SHA-bound one-file delta
        delta_data = json.loads(delta_file.read_text())
        self.assertEqual(delta_data["status"], "PROPOSAL_READY")
        self.assertEqual(delta_data["covered_requirements"], [1, 2])
        self.assertEqual(len(delta_data["changes"]), 1)
        self.assertEqual(delta_data["changes"][0], {
            "path": "test_new.py",
            "original_sha256": candidate_new_sha,
            "content": "assert 2 == 2\n",
        })

        # final result.json must retain the untouched prior file, apply the corrected file,
        # restore original repository SHA/null bindings and allowed-file order
        result_data = json.loads(result_file.read_text())
        self.assertEqual(result_data["status"], "PROPOSAL_READY")
        self.assertEqual(result_data["covered_requirements"], [1, 2])
        self.assertEqual([c["path"] for c in result_data["changes"]], self.task["allowed_files"])
        self.assertEqual(result_data["changes"], [
            {
                "path": "source.py",
                "original_sha256": source_sha,
                "content": "VALUE = 2\n",
            },
            {
                "path": "test_new.py",
                "original_sha256": None,
                "content": "assert 2 == 2\n",
            },
        ])

        # proposal.patch reflects the final rebased canonical proposal
        patch_text = patch_file.read_text()
        self.assertIn("--- a/source.py", patch_text)
        self.assertIn("+VALUE = 2\n", patch_text)
        self.assertIn("--- /dev/null", patch_text)
        self.assertIn("+++ b/test_new.py", patch_text)
        self.assertIn("+assert 2 == 2\n", patch_text)

        # task.json retains original sources and exact prior proposal
        task_data = json.loads(task_file.read_text())
        self.assertEqual(task_data["task"], self.task)
        self.assertEqual(task_data["sources"], self.sources)
        self.assertEqual(task_data["sources"]["source.py"]["sha256"], source_sha)
        self.assertIsNone(task_data["sources"]["test_new.py"]["sha256"])
        self.assertEqual(task_data["proposal"], prior_proposal_text)

        # prompt.txt contains candidate sources/feedback/prior identity and does not contain duplicate proposal
        prompt_text = prompt_file.read_text()
        self.assertIn("\nROLE: worker\nCONTEXT_JSON:\n", prompt_text)
        prompt_json = json.loads(prompt_text.split("\nCONTEXT_JSON:\n", 1)[1])
        self.assertEqual(prompt_json["sources"], expected_candidate_sources)
        self.assertEqual(prompt_json["sources"]["source.py"]["sha256"], candidate_source_sha)
        self.assertEqual(prompt_json["sources"]["test_new.py"]["sha256"], candidate_new_sha)
        self.assertEqual(prompt_json["feedback"]["reviewer"], "codex")
        self.assertEqual(prompt_json["feedback"]["reviewed_proposal_sha256"], prior_proposal_sha)
        self.assertEqual(
            prompt_json["feedback"]["findings"],
            ["test_new.py assertion must verify VALUE == 2"],
        )
        self.assertEqual(prompt_json["prior_identity"], {
            "run_id": "prior-worker-delta",
            "proposal_sha256": prior_proposal_sha,
        })
        self.assertEqual(prompt_json["correction_transport"], {
            "full_content_required_paths": ["test_new.py"],
        })
        self.assertNotIn("proposal", prompt_json)
        self.assertEqual(
            set(prompt_json.keys()),
            {"task", "source_head", "sources", "feedback", "prior_identity",
             "correction_transport", "preparation_notes"},
        )
        self.assertEqual(prompt_json["preparation_notes"],
                         runner.task_preparation_notes(self.task, self.sources))
        self.assertNotIn("preparation_notes", task_data)
        self.assertNotIn('"canonical_proposal_sha256"', prompt_text)

    def test_correction_revert_complete_noop_fails_closed(self):
        self.task["allowed_files"] = ["source.py"]
        self.save_task()
        _, self.sources = runner.load_task(self.root, self.task_path)
        source_sha = self.sources["source.py"]["sha256"]

        prior_result = {
            "status": "PROPOSAL_READY",
            "summary": "Prior modification modifying source.py.",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [
                {
                    "path": "source.py",
                    "original_sha256": source_sha,
                    "content": "VALUE = 2\n",
                },
            ],
        }
        prior_proposal = self.worker_evidence(
            self.root / "prior-worker-revert",
            result=prior_result,
            conversation_id="worker-revert-session",
        )
        prior_sha = runner.file_sha256(prior_proposal)

        feedback = self.root / "feedback-revert.json"
        runner.write_json(feedback, {
            "reviewer": "gemini",
            "reviewed_proposal_sha256": prior_sha,
            "findings": ["Revert changes in source.py"],
            "test_evidence": [],
        })

        candidate_sha = runner.sha(b"VALUE = 2\n")
        revert_delta = {
            "status": "PROPOSAL_READY",
            "summary": "Revert source.py back to original VALUE = 1.",
            "covered_requirements": [1, 2],
            "findings": [],
            "changes": [
                {
                    "path": "source.py",
                    "original_sha256": candidate_sha,
                    "content": None,
                    "replacements": [{"old": "VALUE = 2\n", "new": "VALUE = 1\n"}],
                },
            ],
        }

        with self.assertRaises(runner.RetryableProviderOutputError) as ctx:
            self.invoke_fake_cli(
                feedback=feedback,
                proposal=prior_proposal,
                result=revert_delta,
                run_id="revert-noop-run",
            )
        self.assertEqual("provider returned unusable correction delta", str(ctx.exception))

        out = self.root / ".local/two-site-refactor/proposals/revert-noop-run"
        self.assertTrue((out / "status.json").exists())
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")
        self.assertIn("RetryableProviderOutputError: provider returned unusable correction delta", status["error"])
        self.assertFalse((out / "delta-result.json").exists())
        self.assertFalse((out / "result.json").exists())
        self.assertFalse((out / "proposal.patch").exists())
        self.assertEqual((self.root / "source.py").read_text(), "VALUE = 1\n")

    def patch_runner_now(self, fixed_now=None):
        if fixed_now is None:
            fixed_now = datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc)

        class FakeDatetime(datetime):
            now = MagicMock(return_value=fixed_now)

        FakeDatetime.datetime = FakeDatetime

        try:
            now_patch = patch.object(runner.datetime, "now", return_value=fixed_now)
            now_patch.start()
        except (TypeError, AttributeError):
            now_patch = patch.object(runner, "datetime", FakeDatetime)
            now_patch.start()
        self.addCleanup(now_patch.stop)
        return fixed_now

    def worker_model(self):
        if callable(getattr(runner, "role_model", None)):
            return runner.role_model("worker")
        if hasattr(runner, "ROLE_MODELS") and "worker" in runner.ROLE_MODELS:
            return runner.ROLE_MODELS["worker"]
        return "gemini-3.8-flash-medium"

    def quota_events(self, *, error="RESOURCE_EXHAUSTED: quota exceeded, resets at 2026-09-24T19:00:00Z",
                     usage=None, conversation_id="session-quota", model=None):
        if usage is None:
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return [
            {
                "event": "init",
                "conversation_id": conversation_id,
                "init": {"model": model or self.worker_model()},
            },
            {
                "event": "result",
                "conversation_id": conversation_id,
                "result": {
                    "status": "ERROR",
                    "conversation_id": conversation_id,
                    "usage": usage,
                    "error": error,
                },
            },
        ]

    def test_actual_runner_records_quota_evidence_on_both_process_exit_branches(self):
        self.patch_runner_now(datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc))
        role_model = self.worker_model()
        expected_keys = {"kind", "provider", "model", "conversation_id", "reset_at_utc"}

        for rc in (0, 1):
            run_id = f"quota-exit-{rc}"
            with self.subTest(process_returncode=rc):
                events = self.quota_events(conversation_id=f"session-exit-{rc}")
                with self.assertRaises(runner.ProposalError):
                    self.invoke_fake_cli(
                        events=events,
                        run_id=run_id,
                        process_returncode=rc,
                    )

                out = self.root / f".local/two-site-refactor/proposals/{run_id}"
                status_file = out / "status.json"
                self.assertTrue(status_file.exists())
                status = json.loads(status_file.read_text())
                self.assertEqual(status["state"], "BLOCKED")

                self.assertIn("quota_evidence", status)
                evidence = status["quota_evidence"]
                self.assertEqual(set(evidence.keys()), expected_keys)
                self.assertEqual(evidence["kind"], "QUOTA_RESET_KNOWN")
                self.assertEqual(evidence["provider"], "gemini")
                self.assertEqual(evidence["model"], role_model)
                self.assertEqual(evidence["conversation_id"], f"session-exit-{rc}")
                self.assertEqual(evidence["reset_at_utc"], "2026-09-24T19:00:00Z")
                self.assertEqual(evidence, {
                    "kind": "QUOTA_RESET_KNOWN",
                    "provider": "gemini",
                    "model": role_model,
                    "conversation_id": f"session-exit-{rc}",
                    "reset_at_utc": "2026-09-24T19:00:00Z",
                })

                self.assertFalse((out / "result.json").exists())
                self.assertFalse((out / "proposal.patch").exists())
                self.assertEqual((self.root / "source.py").read_text(), "VALUE = 1\n")

    def test_ambiguous_quota_reset_records_unknown_with_null_reset_at(self):
        self.patch_runner_now(datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc))
        role_model = self.worker_model()
        run_id = "quota-ambiguous-001"
        ambiguous_error = "RESOURCE_EXHAUSTED: quota limit reached, reset at 8:09 AM"
        events = self.quota_events(error=ambiguous_error, conversation_id="session-ambiguous")

        with self.assertRaises(runner.ProposalError):
            self.invoke_fake_cli(events=events, run_id=run_id, process_returncode=0)

        out = self.root / f".local/two-site-refactor/proposals/{run_id}"
        status = json.loads((out / "status.json").read_text())
        self.assertEqual(status["state"], "BLOCKED")

        self.assertIn("quota_evidence", status)
        evidence = status["quota_evidence"]
        self.assertEqual(set(evidence.keys()), {"kind", "provider", "model", "conversation_id", "reset_at_utc"})
        self.assertEqual(evidence["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertEqual(evidence["provider"], "gemini")
        self.assertEqual(evidence["model"], role_model)
        self.assertEqual(evidence["conversation_id"], "session-ambiguous")
        self.assertIsNone(evidence["reset_at_utc"])
        self.assertNotEqual(evidence["reset_at_utc"], "UNKNOWN")
        self.assertEqual(evidence, {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": role_model,
            "conversation_id": "session-ambiguous",
            "reset_at_utc": None,
        })

        self.assertFalse((out / "result.json").exists())
        self.assertFalse((out / "proposal.patch").exists())
        self.assertEqual((self.root / "source.py").read_text(), "VALUE = 1\n")

    def test_negative_scenarios_do_not_record_quota_evidence(self):
        self.patch_runner_now(datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc))

        # 1. Nonquota ERROR
        nonquota_events = self.quota_events(
            error="RuntimeError: Internal model failure without quota",
            conversation_id="session-nonquota",
        )
        with self.assertRaises(runner.ProposalError):
            self.invoke_fake_cli(events=nonquota_events, run_id="neg-nonquota")
        out_nonquota = self.root / ".local/two-site-refactor/proposals/neg-nonquota"
        status_nonquota = json.loads((out_nonquota / "status.json").read_text())
        self.assertEqual(status_nonquota["state"], "BLOCKED")
        self.assertNotIn("quota_evidence", status_nonquota)
        self.assertFalse((out_nonquota / "result.json").exists())

        # 2. Nonzero usage
        nonzero_usage_events = self.quota_events(
            usage={"prompt_tokens": 12, "completion_tokens": 0, "total_tokens": 12},
            conversation_id="session-nonzero-usage",
        )
        with self.assertRaises(runner.ProposalError):
            self.invoke_fake_cli(events=nonzero_usage_events, run_id="neg-nonzero-usage")
        out_usage = self.root / ".local/two-site-refactor/proposals/neg-nonzero-usage"
        status_usage = json.loads((out_usage / "status.json").read_text())
        self.assertEqual(status_usage["state"], "BLOCKED")
        self.assertNotIn("quota_evidence", status_usage)
        self.assertFalse((out_usage / "result.json").exists())

        # 3. Changed CLI identity
        initial = {
            "path": str(Path(__file__).resolve()),
            "version": runner.EXPECTED_CLI_VERSION,
            "sha256": runner.EXPECTED_CLI_SHA256,
        }
        changed = dict(initial, version="unexpected-version")
        with self.assertRaisesRegex(runner.ProposalError, "identity changed"):
            self.invoke_fake_cli(
                cli_identities=[initial, changed],
                events=self.quota_events(conversation_id="session-changed-cli"),
                run_id="neg-changed-cli",
            )
        out_cli = self.root / ".local/two-site-refactor/proposals/neg-changed-cli"
        status_cli = json.loads((out_cli / "status.json").read_text())
        self.assertEqual(status_cli["state"], "BLOCKED")
        self.assertNotIn("quota_evidence", status_cli)
        self.assertFalse((out_cli / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
