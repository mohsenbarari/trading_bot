"""Actual Runner/capture with local fake children; NOT Gemini qualification."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import two_site_proposal_runner as runner
from scripts import two_site_worker_model_routing as routing
from scripts import two_site_json_prompt_fidelity as fidelity
from tests import test_two_site_proposal_runner as fixtures
from tests import test_two_site_json_prompt_fidelity_737k as history


ROLE = {
    "worker": ("gemini-4-argon-medium", "medium", "11111111-1111-4111-8111-111111111111"),
    "verifier": ("gemini-4-argon-high", "high", "22222222-2222-4222-8222-222222222222"),
}
PUBLIC_STATUS = {"worker": "PROPOSAL_READY", "verifier": "VERIFIED"}
BARE_SOURCE_SHA = "502b26fb4528349d8bf7d3c9c6f7b45eca59e681cb5d05cf71ed88847b7c1f52"
BARE_RUN_AST_SHA = "ccc7d3403a6f9ae8a1738a0ef558ff47af8358b0296a286124603d3dd98c06ad"
PRINT_SOURCE_SHA = "f53373164b60f700452e1f2b9cbfb3b5e2670abf79b41a25d36d0ea131ef8ccb"
PRINT_RUN_AST_SHA = "285043997d50c96e9dee72eb030ca82c44663f3ecfd6a5370318438ab6181551"
BARE_DIAGNOSTIC = (
    'Error: --print took "--mode" as its prompt, so the intended prompt was left as an argument and ignored.\n'
    "Attach the prompt to the flag (--print='your prompt') and move --mode elsewhere on the command line.\n"
)


class ExplicitPrintInvocation(unittest.TestCase):
    """Reuse the existing full Task/prior/result fixture, not a fake Runner result."""

    def setUp(self):
        self.legacy = fixtures.ProposalContracts("runTest")
        self.legacy.setUp()
        self.addCleanup(self.legacy.doCleanups)
        self.original_source = (self.legacy.root / "source.py").read_bytes()

    def invoke(self, role, run_id, *, fault=None, remove_print=False, bare_print=False):
        fixture = self.legacy
        model, effort, cid = ROLE[role]
        trace = {"spawned": [], "fresh": [], "disposed": False, "capture": []}
        self.trace = trace
        out = fixture.root / ".local/two-site-refactor/proposals" / run_id
        trace["out"] = out
        trace_path = fixture.root / (run_id + "-child.json")
        trace["child_trace_path"] = trace_path
        proposal = None
        if role == "verifier":
            proposal = fixture.worker_evidence(fixture.root / (run_id + "-worker"))
        response = fixture.raw_result()
        if role == "verifier":
            response.update(status="VERIFIED", changes=[])
        events = fixture.events(result=response)
        events[0]["init"]["model"] = model
        for event in events:
            if event["event"] == "init":
                event["conversation_id"] = cid
            else:
                event["result"]["conversation_id"] = cid
        if fault == "session":
            events[0]["conversation_id"] = "wrong-prepared-session"
            events[1]["result"]["conversation_id"] = "wrong-prepared-session"
        stream = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
        child = (
            "#!" + sys.executable + "\n"
            "import json,os,pathlib,sys,time\n"
            "wire=sys.stdin.buffer.read()\n"
            "schema_path=pathlib.Path(sys.argv[sys.argv.index('--json-schema')+1])\n"
            "record={'argv':sys.argv,'wire':wire.decode('utf-8'),'stdin_eof':True,"
            "'schema':json.loads(schema_path.read_text()),"
            "'auto_update':os.environ.get('AGY_CLI_DISABLE_AUTO_UPDATE'),"
            "'hooks':json.loads((pathlib.Path.cwd()/'.agents/hooks.json').read_text())}\n"
            + "pathlib.Path(" + repr(str(trace_path)) + ").write_text(json.dumps(record))\n"
            # Downstream argv rejection deliberately follows full input EOF and
            # trace retention; early-input failures have separate capture tests.
            + "if '--print' in sys.argv and sys.argv[sys.argv.index('--print')+1:] == []:\n"
            + " sys.stderr.write('flag needs an argument: -print\\n');sys.exit(2)\n"
            + "if '--print' in sys.argv and sys.argv[sys.argv.index('--print')+1] == '--mode':\n"
            + " sys.stderr.write(" + repr(BARE_DIAGNOSTIC) + ");sys.exit(2)\n"
            + "if sys.argv.count('--print=') != 1 or '--print' in sys.argv:\n"
            + " sys.stderr.write('MISSING_EXPLICIT_PRINT\\n');sys.exit(23)\n"
            + "sys.stdout.buffer.write(" + repr(stream) + ");sys.stdout.buffer.flush()\n"
            + ("time.sleep(5)\n" if fault == "hang" else "")
            + ("sys.exit(9)\n" if fault == "nonzero" else "")
        )
        local = fixture.root / ".local/two-site-refactor/proposals"
        local.mkdir(parents=True, exist_ok=True)
        identity = {}

        @contextmanager
        def cli_context(root, cli=None):
            self.assertEqual(root, fixture.root)
            disposable = None
            try:
                with tempfile.TemporaryDirectory(prefix="print-cli-", dir=root) as name:
                    disposable = Path(name)
                    executable = disposable / "local-fake-cli"
                    executable.write_text(child)
                    executable.chmod(0o700)
                    trace["cli"] = executable
                    trace["disposable"] = disposable
                    identity.update(path=str(executable), version="1.4.2",
                                    sha256=hashlib.sha256(child.encode()).hexdigest())
                    yield local, executable, {"source_path": "local-test-fixture", "sha256": identity["sha256"]}
            finally:
                trace["disposed"] = disposable is not None and not disposable.exists()

        def prepare(cli, workspace, selected_model, selected_effort=None, **kwargs):
            self.assertEqual(cli, trace["cli"])
            self.assertEqual((selected_model, selected_effort), (model, effort))
            self.assertEqual(set(kwargs), {"terminate"})
            self.assertIs(kwargs["terminate"], runner.terminate_process)
            self.assertTrue(workspace.is_dir())
            return runner.PreparedProfile(cid, workspace, selected_model, selected_effort)

        def profile(value, fresh=True):
            self.assertEqual((value.conversation_id, value.model, value.effort), (cid, model, effort))
            trace["fresh"].append(fresh)
            if fault == "profile" and not fresh:
                raise ValueError("fixture profile drift")
            return {"native_tools": 0, "command_policy": "off", "conversation_id": cid}

        identities = 0

        def identify(cli):
            nonlocal identities
            identities += 1
            self.assertEqual(cli, trace["cli"])
            return {**identity, "sha256": "0" * 64} if fault == "identity" and identities == 3 else dict(identity)

        real_popen = subprocess.Popen

        def spawn(command, **kwargs):
            trace["spawned"].append(list(command))
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertEqual(kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(kwargs["stderr"], subprocess.PIPE)
            self.assertIs(kwargs["start_new_session"], True)
            self.assertEqual(kwargs["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"], "true")
            self.assertFalse(remove_print and bare_print)
            actual = [part for part in command if part != "--print="] if remove_print else command
            if bare_print:
                actual = ["--print" if part == "--print=" else part for part in command]
            process = real_popen(actual, **kwargs)
            trace["process"] = process
            return process

        real_capture = runner.capture_bounded_process

        def capture(process, wire, stdout_path, stderr_path, timeout, **kwargs):
            trace["capture"].append((wire, timeout, dict(kwargs)))
            self.assertIs(process, trace["process"])
            self.assertEqual((stdout_path, stderr_path), (out / "stream.jsonl", out / "stderr.txt"))
            self.assertEqual(timeout, 45)
            self.assertEqual(kwargs, {})
            # The actual capture/child/kill/reap path runs. This test-only shortened
            # deadline prevents a deliberately sleeping fake from costing45s;
            # the unchanged45s Runner argument is independently asserted above.
            real_capture(process, wire, stdout_path, stderr_path,
                         1 if fault == "hang" else timeout, **kwargs)
            if fault == "schema":
                (out / "response-schema.json").write_text("{}\n")
            elif fault == "source":
                (fixture.root / "source.py").write_text("VALUE = 99\n")

        def git(root, *args):
            self.assertEqual(root, fixture.root)
            if args == ("rev-parse", "HEAD"):
                return "head"
            self.assertEqual(args, ("status", "--porcelain=v1", "--untracked-files=all"))
            return ""

        def before_provider():
            trace["pre_provider"] = trace.get("pre_provider", 0) + 1
            if fault == "stop":
                raise runner.ProposalError("fixture STOP before dispatch")

        disabled = routing.WorkerRoutingPolicy("1.0", False, 22, routing.OPUS_MODEL, routing.OPUS_EFFORT)
        with ExitStack() as stack:
            for patcher in (
                patch.object(runner, "git", side_effect=git),
                patch.object(runner, "proposal_cli_context", side_effect=cli_context),
                patch.object(runner, "cli_identity", side_effect=identify),
                patch.object(runner, "prepare_profile", side_effect=prepare),
                patch.object(runner, "validate_profile", side_effect=profile),
                patch.object(runner, "load_worker_routing_policy", return_value=disabled),
                patch.object(runner, "capture_bounded_process", side_effect=capture),
                patch.object(runner.subprocess, "Popen", side_effect=spawn),
            ):
                stack.enter_context(patcher)
            try:
                returned = runner.run(fixture.root, fixture.task_path, role, run_id, 30,
                                      proposal=proposal, pre_provider_check=before_provider)
            finally:
                trace["identity_calls"] = identities
        self.assertEqual(returned, out)
        trace["child"] = json.loads(trace_path.read_text())
        trace["prior_proposal"] = proposal
        return out

    def whole_invocation_oracle(self, role, out):
        fixture, trace = self.legacy, self.trace
        model, effort, cid = ROLE[role]
        expected_argv = [str(trace["cli"]), "--print=", "--mode", "plan", "--sandbox",
                         "--model", model, "--effort", effort, "--input-format", "text",
                         "--output-format", "stream-json", "--print-timeout", "30s",
                         "--json-schema", str(out / "response-schema.json"), "--conversation", cid]
        self.assertEqual(trace["spawned"], [expected_argv])
        self.assertEqual(trace["child"]["argv"], expected_argv)
        self.assertTrue(trace["child"]["stdin_eof"])
        self.assertEqual(trace["child"]["auto_update"], "true")
        self.assertEqual(trace["pre_provider"], 1)
        self.assertEqual(trace["fresh"], [True, True, False])
        self.assertEqual(trace["identity_calls"], 3)
        self.assertTrue(trace["disposed"])
        process = trace["process"]
        self.assertEqual(process.returncode, 0)
        self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))
        self.assertEqual(len(trace["capture"]), 1)
        wire, timeout, kwargs = trace["capture"][0]
        self.assertEqual(wire, trace["child"]["wire"].encode("utf-8"))
        self.assertEqual((timeout, kwargs), (45, {}))
        schema = json.loads((out / "response-schema.json").read_text())
        self.assertEqual(trace["child"]["schema"], schema)
        context = fixture.parse_cli_input(wire, schema, input_format="text")
        self.assertEqual(context["task"], fixture.task)
        self.assertEqual(context["sources"], fixture.sources)
        self.assertEqual(context["source_head"], "head")
        self.assertIn("\nROLE: " + role + "\nCONTEXT_JSON:\n", wire.decode())
        self.assertEqual(trace["child"]["hooks"], {
            "proposal-only": {"PreToolUse": [{"matcher": "*", "hooks": [{
                "type": "command", "timeout": 5,
                "command": "/usr/bin/printf '%s\\n' '{\"decision\":\"deny\",\"reason\":\"Proposal-only session: tools are disabled. Return JSON directly from supplied context.\"}'",
            }]}]},
            "proposal-single-invocation": {"PostInvocation": [{
                "type": "command",
                "command": "/usr/bin/printf '%s\\n' '{\"injectSteps\":[],\"terminationBehavior\":\"terminate\"}'",
                "timeout": 5,
            }]}
        })
        result = json.loads((out / "result.json").read_text())
        expected = fixture.result()
        if role == "verifier":
            expected.update(status="VERIFIED", changes=[])
            self.assertEqual(context["proposal"]["canonical_proposal_sha256"],
                             runner.file_sha256(trace["prior_proposal"]))
            self.assertEqual(context["proposal"]["changed_paths"], ["test_new.py"])
            self.assertIn("+assert 1 == 1\n", context["proposal"]["unified_diff"])
        self.assertEqual(result, expected)
        status = json.loads((out / "status.json").read_text())
        self.assertEqual((status["state"], status["model"], status["effort"], status["conversation_id"]),
                         (PUBLIC_STATUS[role], model, effort, cid))
        self.assertEqual(status["result_sha256"], runner.file_sha256(out / "result.json"))
        self.assertEqual((out / "stderr.txt").read_bytes(), b"")
        self.assertEqual((fixture.root / "source.py").read_bytes(), self.original_source)
        self.assertFalse((fixture.root / "test_new.py").exists())

    def test_both_roles_reach_exact_print_argv_payload_schema_cid_and_real_eof(self):
        for role in ROLE:
            with self.subTest(role=role):
                out = self.invoke(role, role + "-print-good")
                self.whole_invocation_oracle(role, out)

    def test_exact_fifth_fidelity_factory_pair_and_all_historical_crosspairs(self):
        source = fidelity._read_runner_source()
        self.assertEqual(hashlib.sha256(source).hexdigest(), PRINT_SOURCE_SHA)
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "run"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(hashlib.sha256(ast.dump(nodes[0], include_attributes=True).encode()).hexdigest(),
                         PRINT_RUN_AST_SHA)
        self.assertEqual(fidelity._CONTEXT_CALL_LINES[PRINT_RUN_AST_SHA], 3237)
        self.assertEqual(fidelity._RUNNER_IDENTITIES, {
            "55cd2cc5f0dc8489514db8843d3e3114532d2beced0566b0456e2cf87d5c8af4":
                "90afa2143a2172212027a01092aa80107cd1b5f19cc6bdf3349671c9add0232b",
            "d786e58266893481c9bbd8447d0228170b2037f91073ac2b0ad447bc1f59367f":
                "8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d",
            "cb5f6fa91ce195e6dbd5dffa73183ddbe440e05829bbbdd100c9dfef015b7bf6":
                "9785eb03a32ac1f13cd6ea197e96b64101eb6c1bfa4d3a7859052a3508dfea94",
            BARE_SOURCE_SHA: BARE_RUN_AST_SHA,
            PRINT_SOURCE_SHA: PRINT_RUN_AST_SHA,
        })
        before = dict(vars(runner))

        def prior_loader(*args, **kwargs):
            raise AssertionError("factory proof must never execute returned run")

        private = fidelity.make_root_review_run(prior_loader)
        self.assertIsNot(private, runner.run)
        self.assertIs(private.__globals__["load_prior_worker"], prior_loader)
        self.assertEqual(set(vars(runner)), set(before))
        self.assertTrue(all(vars(runner)[key] is value for key, value in before.items()))
        _, historical = history._three_versions()
        self.assertEqual(source.count(history._EMPTY_PRINT_COMMAND), 1)
        bare = source.replace(history._EMPTY_PRINT_COMMAND, history._PRINT_COMMAND, 1)
        self.assertEqual(hashlib.sha256(bare).hexdigest(), BARE_SOURCE_SHA)
        versions = (*historical, bare, source)
        for own in versions:
            original = history._unexecuted_original(runner, own)
            fidelity._validated_run_code(own, original)
            for other in versions:
                if own == other:
                    continue
                with self.subTest(own_sha=hashlib.sha256(own).hexdigest(),
                                  other_sha=hashlib.sha256(other).hexdigest()):
                    wrong = history._unexecuted_original(runner, other)
                    with self.assertRaisesRegex(fidelity.PromptFidelityError, "actual SDK run code mismatch"):
                        fidelity._validated_run_code(own, wrong)
                    other_ast = hashlib.sha256(ast.dump(history._run_node(other),
                                                       include_attributes=True).encode()).hexdigest()
                    with patch.dict(fidelity._RUNNER_IDENTITIES, {hashlib.sha256(own).hexdigest(): other_ast}):
                        with self.assertRaisesRegex(fidelity.PromptFidelityError, "paired SDK source/run AST pin mismatch"):
                            fidelity._validated_run_code(own, original)
        with patch.dict(fidelity._CONTEXT_CALL_LINES, {PRINT_RUN_AST_SHA: 3238}):
            with self.assertRaisesRegex(fidelity.PromptFidelityError, "CONTEXT JSON call shape drift"):
                fidelity._validated_run_code(source, runner.run)
        with self.assertRaisesRegex(fidelity.PromptFidelityError, "SDK source pin mismatch"):
            fidelity._validated_run_code(source + b"\n", runner.run)

    def test_reached_missing_print_child_rejects_and_same_whole_oracle_restores(self):
        for role in ROLE:
            with self.subTest(role=role):
                self.whole_invocation_oracle(role, self.invoke(role, role + "-print-before"))
                with self.assertRaisesRegex(runner.ProposalError, "CLI failed with exit 23"):
                    self.invoke(role, role + "-print-removed", remove_print=True)
                trace = self.trace
                child = json.loads(trace["child_trace_path"].read_text())
                self.assertIs(child["stdin_eof"], True)
                self.assertEqual(len(trace["capture"]), 1)
                wire, timeout, kwargs = trace["capture"][0]
                self.assertEqual(child["wire"].encode("utf-8"), wire)
                self.assertEqual((timeout, kwargs), (45, {}))
                self.assertEqual(trace["spawned"][0].count("--print="), 1)
                self.assertNotIn("--print=", child["argv"])
                self.assertEqual(child["argv"], [part for part in trace["spawned"][0]
                                                 if part != "--print="])
                self.assertTrue(all(pipe.closed for pipe in (
                    trace["process"].stdin, trace["process"].stdout, trace["process"].stderr)))
                self.assertEqual(trace["process"].returncode, 23)
                self.assertTrue(trace["disposed"])
                self.assertEqual((trace["out"] / "stderr.txt").read_text(), "MISSING_EXPLICIT_PRINT\n")
                self.assertFalse((trace["out"] / "result.json").exists())
                self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
                self.whole_invocation_oracle(role, self.invoke(role, role + "-print-restored"))

    def test_reached_actual_bare_flag_grammar_failure_and_same_whole_oracle_restores(self):
        self.assertEqual(len(BARE_DIAGNOSTIC.encode()), 205)
        self.assertEqual(hashlib.sha256(BARE_DIAGNOSTIC.encode()).hexdigest(),
                         "350b176cae58d2e0b8eda4ce36afa96c53cdac05e48089aa1d180101eaa16769")
        for role in ROLE:
            with self.subTest(role=role):
                self.whole_invocation_oracle(role, self.invoke(role, role + "-empty-before"))
                with self.assertRaisesRegex(runner.ProposalError, "CLI failed with exit 2"):
                    self.invoke(role, role + "-actual-bare", bare_print=True)
                trace = self.trace
                child = json.loads(trace["child_trace_path"].read_text())
                self.assertIs(child["stdin_eof"], True)
                self.assertEqual(len(trace["capture"]), 1)
                wire, timeout, kwargs = trace["capture"][0]
                self.assertEqual(child["wire"].encode("utf-8"), wire)
                self.assertEqual((timeout, kwargs), (45, {}))
                self.assertEqual(trace["spawned"][0].count("--print="), 1)
                self.assertEqual(child["argv"], ["--print" if part == "--print=" else part
                                                  for part in trace["spawned"][0]])
                self.assertEqual(child["argv"][1:4], ["--print", "--mode", "plan"])
                self.assertEqual(child["schema"], json.loads((trace["out"] / "response-schema.json").read_text()))
                self.assertTrue(all(pipe.closed for pipe in (
                    trace["process"].stdin, trace["process"].stdout, trace["process"].stderr)))
                self.assertEqual(trace["process"].returncode, 2)
                self.assertTrue(trace["disposed"])
                self.assertEqual((trace["out"] / "stderr.txt").read_text(), BARE_DIAGNOSTIC)
                self.assertEqual((trace["out"] / "stream.jsonl").read_bytes(), b"")
                self.assertFalse((trace["out"] / "result.json").exists())
                self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
                self.whole_invocation_oracle(role, self.invoke(role, role + "-empty-restored"))

    def test_success_terminal_does_not_bypass_nonzero_or_hanging_process_exit(self):
        for fault, error, message in (("nonzero", runner.ProposalError, "CLI failed with exit 9"),
                                      ("hang", subprocess.TimeoutExpired, "timed out")):
            with self.subTest(fault=fault):
                with self.assertRaisesRegex(error, message):
                    self.invoke("worker", "print-exit-" + fault, fault=fault)
                trace = self.trace
                self.assertIn('"status": "SUCCESS"', (trace["out"] / "stream.jsonl").read_text())
                self.assertTrue(trace["disposed"])
                self.assertIsNotNone(trace["process"].returncode)
                self.assertFalse((trace["out"] / "result.json").exists())
                self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
        self.whole_invocation_oracle("worker", self.invoke("worker", "print-exit-restored"))

    def test_stop_and_post_child_drift_still_block_and_dispose_without_result(self):
        cases = (("stop", runner.ProposalError, "fixture STOP before dispatch"),
                 ("schema", runner.ProposalError, "response schema binding changed during generation"),
                 ("identity", runner.ProposalError, "CLI identity changed during generation"),
                 ("source", runner.ProposalError, "task or source bytes changed during generation"),
                 ("session", runner.ProposalError, "prepared runtime session"),
                 ("profile", ValueError, "fixture profile drift"))
        for fault, error, message in cases:
            with self.subTest(fault=fault):
                try:
                    with self.assertRaisesRegex(error, message):
                        self.invoke("worker", "print-drift-" + fault, fault=fault)
                    trace = self.trace
                    self.assertTrue(trace["disposed"])
                    self.assertFalse((trace["out"] / "result.json").exists())
                    self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
                    if fault == "stop":
                        self.assertEqual(trace["spawned"], [])
                        self.assertEqual(trace["capture"], [])
                    else:
                        self.assertEqual(trace["process"].returncode, 0)
                finally:
                    (self.legacy.root / "source.py").write_bytes(self.original_source)
        self.whole_invocation_oracle("worker", self.invoke("worker", "print-drift-restored"))


if __name__ == "__main__":
    unittest.main()
