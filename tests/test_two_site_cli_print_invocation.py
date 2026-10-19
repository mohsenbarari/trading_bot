"""Documented one-turn NDJSON with real local capture, NOT Gemini qualification."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import ast
import fcntl
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
STREAM_SOURCE_SHA = "46544061459af3d0df38079abe21817bf59606341bdf6d9100fe9607e79e9cb4"
STREAM_RUN_AST_SHA = "78ff37fd7f19153e4671f2cb7a000718c4abb28a5c51da2f227df2f16ed733e7"
STREAM_CONTEXT_LINE = 3252
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

    def invoke(self, role, run_id, *, fault=None, legacy_print=None, wire_fault=None):
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
        # This child is only a strict test oracle. All deliberate downstream
        # protocol/argv errors follow complete EOF+trace, unlike early-input.
        child_body = r'''import json,os,pathlib,sys,time
@EARLY@
wire=sys.stdin.buffer.read()
schema_path=pathlib.Path(sys.argv[sys.argv.index('--json-schema')+1])
record={'argv':sys.argv,'wire':wire.decode('utf-8'),'stdin_eof':True,
        'schema':json.loads(schema_path.read_text()),
        'auto_update':os.environ.get('AGY_CLI_DISABLE_AUTO_UPDATE'),
        'hooks':json.loads((pathlib.Path.cwd()/'.agents/hooks.json').read_text())}
trace=pathlib.Path(@TRACE@)
trace.write_text(json.dumps(record))
if '--print' in sys.argv and sys.argv[sys.argv.index('--print')+1:] == []:
 sys.stderr.write('flag needs an argument: -print\n');sys.exit(2)
if '--print' in sys.argv and sys.argv[sys.argv.index('--print')+1] == '--mode':
 sys.stderr.write(@BARE@);sys.exit(2)
if any(arg in ('--print=','--print','--prompt','-p') for arg in sys.argv):
 sys.stderr.write('Error: empty prompt. Usage: agy --print "your prompt here"\n');sys.exit(2)
def unique(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('duplicate')
  result[key]=value
 return result
try:
 if sys.argv.count('--input-format')!=1 or sys.argv[sys.argv.index('--input-format')+1]!='stream-json':raise ValueError('input')
 if sys.argv.count('--output-format')!=1 or sys.argv[sys.argv.index('--output-format')+1]!='stream-json':raise ValueError('output')
 if not wire.endswith(b'\n') or wire.count(b'\n')!=1:raise ValueError('oneLF')
 event=json.loads(wire,object_pairs_hook=unique)
 if type(event) is not dict or set(event)!={'event','message'} or event['event']!='user':raise ValueError('event')
 message=event['message']
 if type(message) is not dict or set(message)!={'content'} or type(message['content']) is not str:raise ValueError('content')
 if message['content']!=pathlib.Path(@PROMPT@).read_text():raise ValueError('changed')
 record['logical_prompt']=message['content']
 trace.write_text(json.dumps(record))
except (ValueError,TypeError,KeyError,IndexError):
 sys.stderr.write('INVALID_NDJSON_INPUT\n');sys.exit(23)
sys.stdout.buffer.write(@STREAM@);sys.stdout.buffer.flush()
@TAIL@
'''
        replacements = {
            "@EARLY@": ("sys.stdout.buffer.write(" + repr(stream)
                        + ");sys.stdout.buffer.flush();sys.exit(7)") if fault == "early-input" else "",
            "@TRACE@": repr(str(trace_path)), "@PROMPT@": repr(str(out / "prompt.txt")),
            "@BARE@": repr(BARE_DIAGNOSTIC), "@STREAM@": repr(stream),
            "@TAIL@": ("time.sleep(5)\n" if fault == "hang" else "")
                     + ("sys.exit(9)\n" if fault == "nonzero" else ""),
        }
        for marker, value in replacements.items():
            self.assertEqual(child_body.count(marker), 1)
            child_body = child_body.replace(marker, value)
        child = "#!" + sys.executable + "\n" + child_body
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
            self.assertIn(legacy_print, (None, "bare", "empty", "raw-text"))
            actual = list(command)
            if legacy_print in ("bare", "empty"):
                actual.insert(1, "--print" if legacy_print == "bare" else "--print=")
            if legacy_print is not None:
                actual[actual.index("--input-format") + 1] = "text"
            process = real_popen(actual, **kwargs)
            trace["process"] = process
            if fault == "early-input":
                fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
                trace["stdin_capacity"] = fcntl.fcntl(process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
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
            actual_wire = wire
            if legacy_print is not None:
                actual_wire = runner.unique_json(wire.decode())["message"]["content"].encode("utf-8")
            if wire_fault is not None:
                envelope = runner.unique_json(wire.decode())
                if wire_fault == "missing-event":
                    del envelope["event"]
                elif wire_fault == "wrong-type":
                    envelope["message"]["content"] = ["not a string"]
                elif wire_fault == "tamper":
                    envelope["message"]["content"] += "changed"
                elif wire_fault == "duplicate":
                    actual_wire = wire.replace(b'"event":', b'"event":"user","event":', 1)
                elif wire_fault == "duplicate-content":
                    actual_wire = wire.replace(b'"content":', b'"content":"duplicate","content":', 1)
                elif wire_fault == "second-record":
                    actual_wire = wire + wire
                elif wire_fault == "missing-LF":
                    actual_wire = wire[:-1]
                elif wire_fault == "truncated":
                    actual_wire = wire[:len(wire) // 2]
                else:
                    raise AssertionError("unknown wire fault")
                if wire_fault in ("missing-event", "wrong-type", "tamper"):
                    actual_wire = (json.dumps(envelope) + "\n").encode()
            trace["sent_wire"] = actual_wire
            real_capture(process, actual_wire, stdout_path, stderr_path,
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
        expected_argv = [str(trace["cli"]), "--mode", "plan", "--sandbox",
                         "--model", model, "--effort", effort, "--input-format", "stream-json",
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
        self.assertEqual(wire.count(b"\n"), 1)
        self.assertTrue(wire.endswith(b"\n"))
        logical = (out / "prompt.txt").read_text()
        self.assertEqual(wire, fixture.cli_wire(logical))
        self.assertEqual(trace["child"]["logical_prompt"], logical)
        context = fixture.parse_cli_input(wire, schema, input_format="stream-json")
        self.assertEqual(context["task"], fixture.task)
        self.assertEqual(context["sources"], fixture.sources)
        self.assertEqual(context["source_head"], "head")
        self.assertIn("\nROLE: " + role + "\nCONTEXT_JSON:\n", logical)
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

    def test_both_roles_reach_exact_ndjson_argv_payload_schema_cid_and_real_eof(self):
        for role in ROLE:
            with self.subTest(role=role):
                out = self.invoke(role, role + "-print-good")
                self.whole_invocation_oracle(role, out)

    def test_exact_sixth_fidelity_factory_pair_and_all_historical_crosspairs(self):
        source = fidelity._read_runner_source()
        self.assertEqual(hashlib.sha256(source).hexdigest(), STREAM_SOURCE_SHA)
        nodes = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "run"]
        self.assertEqual(len(nodes), 1)
        self.assertEqual(hashlib.sha256(ast.dump(nodes[0], include_attributes=True).encode()).hexdigest(),
                         STREAM_RUN_AST_SHA)
        self.assertEqual(fidelity._CONTEXT_CALL_LINES[STREAM_RUN_AST_SHA], STREAM_CONTEXT_LINE)
        self.assertEqual(fidelity._RUNNER_IDENTITIES, {
            "55cd2cc5f0dc8489514db8843d3e3114532d2beced0566b0456e2cf87d5c8af4":
                "90afa2143a2172212027a01092aa80107cd1b5f19cc6bdf3349671c9add0232b",
            "d786e58266893481c9bbd8447d0228170b2037f91073ac2b0ad447bc1f59367f":
                "8951f6c504fdfeeaaa047340f4b5557b581514e7d25da2bcafb938d42fa3197d",
            "cb5f6fa91ce195e6dbd5dffa73183ddbe440e05829bbbdd100c9dfef015b7bf6":
                "9785eb03a32ac1f13cd6ea197e96b64101eb6c1bfa4d3a7859052a3508dfea94",
            BARE_SOURCE_SHA: BARE_RUN_AST_SHA,
            PRINT_SOURCE_SHA: PRINT_RUN_AST_SHA,
            STREAM_SOURCE_SHA: STREAM_RUN_AST_SHA,
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
        empty = history._apply_ndjson_transport(source, inverse=True)
        self.assertEqual(hashlib.sha256(empty).hexdigest(), PRINT_SOURCE_SHA)
        self.assertEqual(empty.count(history._EMPTY_PRINT_COMMAND), 1)
        bare = empty.replace(history._EMPTY_PRINT_COMMAND, history._PRINT_COMMAND, 1)
        self.assertEqual(hashlib.sha256(bare).hexdigest(), BARE_SOURCE_SHA)
        versions = (*historical, bare, empty, source)
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
        with patch.dict(fidelity._CONTEXT_CALL_LINES, {STREAM_RUN_AST_SHA: STREAM_CONTEXT_LINE + 1}):
            with self.assertRaisesRegex(fidelity.PromptFidelityError, "CONTEXT JSON call shape drift"):
                fidelity._validated_run_code(source, runner.run)
        with self.assertRaisesRegex(fidelity.PromptFidelityError, "SDK source pin mismatch"):
            fidelity._validated_run_code(source + b"\n", runner.run)

    def test_reached_raw_text_child_rejects_and_same_whole_oracle_restores(self):
        for role in ROLE:
            with self.subTest(role=role):
                self.whole_invocation_oracle(role, self.invoke(role, role + "-print-before"))
                with self.assertRaisesRegex(runner.ProposalError, "CLI failed with exit 23"):
                    self.invoke(role, role + "-raw-text", legacy_print="raw-text")
                trace = self.trace
                child = json.loads(trace["child_trace_path"].read_text())
                self.assertIs(child["stdin_eof"], True)
                self.assertEqual(len(trace["capture"]), 1)
                wire, timeout, kwargs = trace["capture"][0]
                self.assertEqual(child["wire"].encode("utf-8"), trace["sent_wire"])
                self.assertEqual((timeout, kwargs), (45, {}))
                expected = list(trace["spawned"][0])
                expected[expected.index("--input-format") + 1] = "text"
                self.assertEqual(child["argv"], expected)
                self.assertTrue(all(pipe.closed for pipe in (
                    trace["process"].stdin, trace["process"].stdout, trace["process"].stderr)))
                self.assertEqual(trace["process"].returncode, 23)
                self.assertTrue(trace["disposed"])
                self.assertEqual((trace["out"] / "stderr.txt").read_text(), "INVALID_NDJSON_INPUT\n")
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
                    self.invoke(role, role + "-actual-bare", legacy_print="bare")
                trace = self.trace
                child = json.loads(trace["child_trace_path"].read_text())
                self.assertIs(child["stdin_eof"], True)
                self.assertEqual(len(trace["capture"]), 1)
                wire, timeout, kwargs = trace["capture"][0]
                self.assertEqual(child["wire"].encode("utf-8"), trace["sent_wire"])
                self.assertEqual((timeout, kwargs), (45, {}))
                self.assertNotIn("--print", trace["spawned"][0])
                expected = [trace["spawned"][0][0], "--print", *trace["spawned"][0][1:]]
                expected[expected.index("--input-format") + 1] = "text"
                self.assertEqual(child["argv"], expected)
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

    def test_encoder_exact_identity_types_utf8_and_both_logical_boundaries(self):
        encode = runner._cli_stream_input
        prompt = 'Quote" backslash\\ line\n nul\0 Persian\u0641\u0627 emoji\U0001f600 Cf\u202e'
        expected = (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode()
        self.assertEqual(encode(prompt), expected)
        self.assertEqual(expected.count(b"\n"), 1)
        self.assertEqual(runner.unique_json(expected.decode())["message"]["content"], prompt)
        for value in (None, False, 1, b"prompt", [], {}, ""):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(runner.ProposalError, "invalid CLI input prompt"):
                    encode(value)
        for mode in (None, 0, 1, "compact"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(runner.ProposalError, "invalid CLI input prompt"):
                    encode("prompt", is_compact_review=mode)
        with self.assertRaises(UnicodeEncodeError):
            encode("\ud800")
        for compact, limit in ((False, 320000), (True, 160000)):
            with self.subTest(compact=compact):
                prompt = "\0" * limit
                wire = encode(prompt, is_compact_review=compact)
                self.assertEqual(len(prompt.encode()), limit)
                self.assertEqual(len(wire), 6 * limit + 46)
                self.assertEqual(runner.unique_json(wire.decode())["message"]["content"], prompt)
                self.assertEqual(wire.count(b"\n"), 1)
                with self.assertRaisesRegex(runner.ProposalError, "logical prompt exceeds budget"):
                    encode(prompt + "\0", is_compact_review=compact)
                multi = "\u00e9" * (limit // 2)
                self.assertEqual(len(multi.encode()), limit)
                self.assertEqual(runner.unique_json(encode(multi, is_compact_review=compact).decode())
                                 ["message"]["content"], multi)
                with self.assertRaisesRegex(runner.ProposalError, "logical prompt exceeds budget"):
                    encode(multi + "x", is_compact_review=compact)
                # Honest wire-only fault: valid JSON output plus one trailing
                # space exceeds the derived maximum. Normal serialization
                # cannot exceed this bound while the logical prompt is legal.
                real_dumps = json.dumps
                with patch.object(runner.json, "dumps",
                                  side_effect=lambda value: real_dumps(value) + " ") as serializer:
                    with self.assertRaisesRegex(runner.ProposalError, "wire exceeds derived budget"):
                        encode(prompt, is_compact_review=compact)
                    serializer.assert_called_once_with(
                        {"event": "user", "message": {"content": prompt}})
                self.assertEqual(encode(prompt, is_compact_review=compact), wire)

    def test_unicode_quotes_newlines_preserve_whole_prompt_for_both_roles(self):
        addition = '\nQuote" slash\\ nul\0 Persian\u0641\u0627 emoji\U0001f600 Cf\u202e\n'
        self.legacy.task["goal"] += addition
        runner.write_json(self.legacy.task_path, self.legacy.task)
        for role in ROLE:
            with self.subTest(role=role):
                out = self.invoke(role, role + "-ndjson-unicode")
                self.whole_invocation_oracle(role, out)
                logical = (out / "prompt.txt").read_text()
                self.assertIn(json.dumps(self.legacy.task["goal"], ensure_ascii=False), logical)
                self.assertEqual(self.trace["child"]["logical_prompt"], logical)
                self.assertTrue(self.trace["sent_wire"].isascii())
                self.assertEqual(self.trace["sent_wire"].count(b"\n"), 1)

    def test_actual_encoder_failures_precede_cli_context_profile_or_process(self):
        actual = runner._cli_stream_input
        def wire_overflow(prompt, **kwargs):
            with patch.object(runner.json, "dumps", return_value=" " * 1920046):
                return actual(prompt, **kwargs)

        failures = (
            (lambda prompt, **kw: actual(None, **kw), runner.ProposalError, "invalid CLI input prompt"),
            (lambda prompt, **kw: actual("\ud800", **kw), UnicodeEncodeError, "surrogates"),
            (lambda prompt, **kw: actual("x" * 320001, **kw),
             runner.ProposalError, "logical prompt exceeds budget"),
            (wire_overflow, runner.ProposalError, "wire exceeds derived budget"),
        )
        for role in ROLE:
            for index, (failure, error, message) in enumerate(failures):
                with self.subTest(role=role, error=message):
                    self.whole_invocation_oracle(role, self.invoke(role, role + "-prefail-before-" + str(index)))
                    with patch.object(runner, "_cli_stream_input", side_effect=failure) as encoder:
                        with self.assertRaisesRegex(error, message):
                            self.invoke(role, role + "-prefail-" + str(index))
                        self.assertEqual(encoder.call_count, 1)
                    trace = self.trace
                    self.assertEqual(trace["spawned"], [])
                    self.assertEqual(trace["capture"], [])
                    self.assertEqual(trace["fresh"], [])
                    self.assertNotIn("cli", trace)
                    self.assertFalse(trace["out"].exists())
                    self.whole_invocation_oracle(role, self.invoke(role, role + "-prefail-after-" + str(index)))

    def test_strict_child_rejects_wrong_shape_duplicate_second_record_and_tamper(self):
        for role in ROLE:
            for fault in ("missing-event", "wrong-type", "duplicate", "duplicate-content", "second-record",
                          "missing-LF", "truncated", "tamper"):
                with self.subTest(role=role, fault=fault):
                    self.whole_invocation_oracle(role, self.invoke(role, role + "-wire-before-" + fault))
                    with self.assertRaisesRegex(runner.ProposalError, "CLI failed with exit 23"):
                        self.invoke(role, role + "-wire-bad-" + fault, wire_fault=fault)
                    trace = self.trace
                    child = json.loads(trace["child_trace_path"].read_text())
                    self.assertIs(child["stdin_eof"], True)
                    self.assertEqual(child["wire"].encode(), trace["sent_wire"])
                    self.assertNotEqual(trace["sent_wire"], trace["capture"][0][0])
                    self.assertEqual(trace["process"].returncode, 23)
                    self.assertTrue(all(pipe.closed for pipe in (
                        trace["process"].stdin, trace["process"].stdout, trace["process"].stderr)))
                    self.assertTrue(trace["disposed"])
                    self.assertEqual((trace["out"] / "stderr.txt").read_text(), "INVALID_NDJSON_INPUT\n")
                    self.assertFalse((trace["out"] / "result.json").exists())
                    self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
                    self.whole_invocation_oracle(role, self.invoke(role, role + "-wire-after-" + fault))

    def test_reached_accepted65_empty_print_raw_stdin_failure_then_healthy_restore(self):
        for role in ROLE:
            with self.subTest(role=role):
                self.whole_invocation_oracle(role, self.invoke(role, role + "-empty-before"))
                with self.assertRaisesRegex(runner.ProposalError, "CLI failed with exit 2"):
                    self.invoke(role, role + "-empty-legacy", legacy_print="empty")
                trace = self.trace
                child = json.loads(trace["child_trace_path"].read_text())
                expected = [trace["spawned"][0][0], "--print=", *trace["spawned"][0][1:]]
                expected[expected.index("--input-format") + 1] = "text"
                self.assertEqual(child["argv"], expected)
                self.assertEqual(child["wire"].encode(), (trace["out"] / "prompt.txt").read_bytes())
                self.assertEqual(child["wire"].encode(), trace["sent_wire"])
                self.assertIs(child["stdin_eof"], True)
                self.assertEqual((trace["out"] / "stderr.txt").read_text(),
                                 'Error: empty prompt. Usage: agy --print "your prompt here"\n')
                self.assertEqual(trace["process"].returncode, 2)
                self.assertTrue(trace["disposed"])
                self.assertFalse((trace["out"] / "result.json").exists())
                self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
                self.whole_invocation_oracle(role, self.invoke(role, role + "-empty-after"))

    def test_early_child_exit_retains_incomplete_input_guard_not_downstream_error(self):
        # A valid logical prompt larger than any normal pipe capacity makes
        # this separate early-input failure deterministic; child never reads.
        self.legacy.task["goal"] += "x" * 100000
        runner.write_json(self.legacy.task_path, self.legacy.task)
        with self.assertRaisesRegex(runner.ProposalError, "CLI input was not completely written"):
            self.invoke("worker", "ndjson-early-input", fault="early-input")
        trace = self.trace
        self.assertGreater(len(trace["capture"][0][0]), 100000)
        self.assertEqual(trace["stdin_capacity"], 4096)
        self.assertEqual(trace["process"].returncode, 7)
        self.assertTrue(all(pipe.closed for pipe in (
            trace["process"].stdin, trace["process"].stdout, trace["process"].stderr)))
        self.assertTrue(trace["disposed"])
        self.assertFalse(trace["child_trace_path"].exists())
        self.assertFalse((trace["out"] / "result.json").exists())
        self.assertEqual(json.loads((trace["out"] / "status.json").read_text())["state"], "BLOCKED")
        self.whole_invocation_oracle("worker", self.invoke("worker", "ndjson-early-restored"))

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
