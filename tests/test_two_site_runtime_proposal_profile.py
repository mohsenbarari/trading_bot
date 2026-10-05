"""Independent, hermetic no-tools runtime identity and failure witnesses."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from scripts.two_site_antigravity_session import message
from scripts import two_site_proposal_runtime_profile as profile
from scripts import two_site_proposal_runner as runner

CID = "12345678-1234-1234-1234-123456789abc"
MODEL = "gemini-3.8-flash-high"
REFS = ("user_information", "mcp_servers", "skills", "messaging", "artifacts", "guidelines", "communication_style")
CHECKS = ("message_continue_check", "terminal_step_check", "max_generator_invocations_check", "empty_output_continuation_check", "force_invocation", "no_tool_call_check")
AUX = bytes.fromhex("0a086a0642041a023001")
FLAGS = bytes.fromhex("0a021001120208011a020800220208013a020801")
HOOK_ORACLE = {
    "proposal-only": {"PreToolUse": [{"matcher": "*", "hooks": [{
        "type": "command", "timeout": 5,
        "command": "/usr/bin/printf '%s\\n' '{\"decision\":\"deny\",\"reason\":\"Proposal-only session: tools are disabled. Return JSON directly from supplied context.\"}'",
    }]}]},
    "proposal-single-invocation": {"PostInvocation": [{
        "type": "command", "timeout": 5,
        "command": "/usr/bin/printf '%s\\n' '{\"injectSteps\":[],\"terminationBehavior\":\"terminate\"}'",
    }]},
}
HOOK_BYTES = (json.dumps(HOOK_ORACLE, ensure_ascii=False, indent=2) + "\n").encode()


def tool_literal(body):
    return message(
        (2, message((1, message((1, "System Prompt"), (2, body))))),
        *[(2, message((2, name))) for name in REFS],
        *[(5, message((1, name), (2, 1))) for name in CHECKS],
    )


def literal_metadata(work, body, *, name="two-site-proposal-only", policy="off", extra=b"", tools_extra=b"", source=None, root_id=CID, selected_extra=b"", flags=FLAGS):
    container = tool_literal(body) + tools_extra
    resolved = message(
        (5, policy), (11, AUX),
        (17, message((16, source or (work/".agents/agents/two-site-proposal-only/agent.md").as_uri()))),
        (18, message((1, container), (3, AUX), (4, flags))),
    )
    selected = message(
        (1, name), (8, "Isolated proposal-only author or reviewer; no native tools or delegation."),
        (12, 0), (13, 1), (15, 1), (7, resolved),
    ) + selected_extra
    return message((1, b"non-authority-ui"), (2, b"time"), (3, CID), (4, selected), (6, root_id), (7, work.as_uri()), (10, resolved), (18, b"runtime")) + extra


class RuntimeProfileContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root/"workspace"
        self.work.mkdir(mode=0o700)
        self.candidate = profile.PreparedProfile(CID, self.work, MODEL, "high")
        self.source = self.work/".agents/agents/two-site-proposal-only/agent.md"
        self.source.parent.mkdir(parents=True, mode=0o700)
        self.source.write_text(profile.DEFINITION)
        self.source.chmod(0o600)
        self.hooks = self.work/".agents/hooks.json"
        self.hooks.write_bytes(HOOK_BYTES)
        self.hooks.chmod(0o600)
        self.home = self.root/"cli-home"
        (self.home/"conversations").mkdir(parents=True)
        self.db_path = self.home/"conversations"/(CID+".db")
        self.body = (
            "Use internal reasoning only and the complete supplied source snapshot. "
            "Paths are labels, not permission to read files. Return the supplied JSON schema. "
            "Never call tools, shell, files, browser, network, subagents or delegation. "
            "You have no direct Source, Git, deployment or Writer authority."
        )
        self.oracle = literal_metadata(self.work, self.body)
        self.expected = {"conversation_id":CID,"name":"two-site-proposal-only","native_tools":0,"command_policy":"off","actual_executor_checked":False}
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE trajectory_meta(cascade_id TEXT,trajectory_type INTEGER,source INTEGER)")
            db.execute("CREATE TABLE trajectory_metadata_blob(id TEXT,data BLOB)")
            db.execute("CREATE TABLE steps(status INTEGER)")
            db.execute("CREATE TABLE executor_metadata(data BLOB)")
            db.execute("INSERT INTO trajectory_meta VALUES(?,?,?)", (CID,4,17))
            db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (self.oracle,))
        self.db_path.chmod(0o600)

    def check_positive(self):
        before = self.db_path.read_bytes()
        source = self.source.read_bytes()
        result = profile.validate_profile(self.candidate, fresh=True, cli_home=self.home)
        self.assertEqual(result, self.expected)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(self.oracle, literal_metadata(self.work, self.body))

    def replace_meta(self, data):
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE trajectory_metadata_blob SET data=? WHERE id='main'", (data,))

    def test_complete_independent_positive(self):
        self.check_positive()
        self.assertEqual(profile.expected_tool_container(), tool_literal(self.body))

    def test_fixed_hooks_are_independent_detached_and_tamper_reject(self):
        self.assertEqual(profile.proposal_hooks(), HOOK_ORACLE)
        detached = profile.proposal_hooks()
        detached["proposal-single-invocation"]["PostInvocation"][0]["command"] = "unsafe"
        self.assertEqual(profile.proposal_hooks(), HOOK_ORACLE)
        variants = [b"{}\n", HOOK_BYTES+b" ", HOOK_BYTES.replace(b"terminate", b"force_continue"),
                    HOOK_BYTES.replace(b"deny", b"allow"),
                    HOOK_BYTES.replace(b'"timeout": 5', b'"timeout": 50'),
                    HOOK_BYTES.replace(b'"PostInvocation"', b'"Stop"')]
        for bad in variants:
            with self.subTest(hook_bytes=len(bad)):
                self.check_positive()
                self.hooks.write_bytes(bad)
                try:
                    with self.assertRaises(profile.ProposalProfileError):
                        profile.validate_profile(self.candidate, fresh=True, cli_home=self.home)
                finally:
                    self.hooks.write_bytes(HOOK_BYTES)
                self.check_positive()
        link = self.hooks.with_name("hook-link")
        os.link(self.hooks, link)
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate, fresh=True, cli_home=self.home)
        finally:
            link.unlink()
        self.check_positive()
        self.hooks.rename(link); self.hooks.symlink_to(link)
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate, fresh=True, cli_home=self.home)
        finally:
            self.hooks.unlink(); link.rename(self.hooks)
        self.check_positive()

    def test_complete_metadata_positive_fault_same_positive(self):
        variants = [
            literal_metadata(self.work,self.body,name="Default"),
            literal_metadata(self.work,self.body,policy="sandbox"),
            literal_metadata(self.work,self.body,extra=message((99,1))),
            literal_metadata(self.work,self.body,extra=message((7,self.work.as_uri()))),
            literal_metadata(self.work,self.body,tools_extra=message((3,message((1,"run_command"),(2,1))))),
            literal_metadata(self.work,self.body,tools_extra=message((2,message((2,"unknown_reference"))))),
            literal_metadata(self.work,self.body,tools_extra=message((5,message((1,"unknown_check"),(2,1))))),
            literal_metadata(self.work,self.body,source=(self.work/"foreign-agent.md").as_uri()),
            literal_metadata(self.work,self.body,root_id="different-root"),
            literal_metadata(self.work,self.body,selected_extra=message((16,1))),
            literal_metadata(self.work,self.body,flags=FLAGS+message((99,1))),
            literal_metadata(self.work,self.body+" injected prompt"),
            b"", b"not protobuf", self.oracle*2,
        ]
        for bad in variants:
            with self.subTest(size=len(bad)):
                self.check_positive()
                self.replace_meta(bad)
                before = self.db_path.read_bytes()
                try:
                    with self.assertRaises(profile.ProposalProfileError):
                        profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
                    self.assertEqual(self.db_path.read_bytes(),before)
                finally:
                    self.replace_meta(self.oracle)
                self.check_positive()

    def test_source_hardlink_symlink_and_content_change_reject(self):
        original = self.source.read_bytes()
        link = self.source.with_name("hardlink.md")
        self.check_positive()
        os.link(self.source,link)
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            link.unlink()
        self.check_positive()
        self.source.write_bytes(original+b"extra\n")
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            self.source.write_bytes(original)
        self.check_positive()
        self.source.rename(link);self.source.symlink_to(link)
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            self.source.unlink();link.rename(self.source)
        self.check_positive()

    def test_fresh_session_has_no_steps_and_exact_database_identity(self):
        self.check_positive()
        with sqlite3.connect(self.db_path) as db:db.execute("INSERT INTO steps VALUES(3)")
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            with sqlite3.connect(self.db_path) as db:db.execute("DELETE FROM steps")
        self.check_positive()
        with sqlite3.connect(self.db_path) as db:db.execute("UPDATE trajectory_meta SET cascade_id='foreign'")
        try:
            with self.assertRaises(profile.ProposalProfileError):
                profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            with sqlite3.connect(self.db_path) as db:db.execute("UPDATE trajectory_meta SET cascade_id=?",(CID,))
        self.check_positive()

    def executor(self, model=MODEL, tools_extra=b"", kind=9):
        config=message((28,model),(46,tool_literal(self.body)+tools_extra))
        return message((1,kind),(2,1),(3,3),(9,CID),(10,message((1,config))),(11,b"opaque telemetry"),(17,b"source"))

    def test_actual_executor_model_and_tools_and_unknown_field_reject(self):
        good=self.executor()
        for bad in (self.executor(model="different-model"), self.executor(tools_extra=message((3,message((1,"read_file"),(2,1))))), self.executor(kind=4), self.executor(kind=6), good+message((99,1))):
            with sqlite3.connect(self.db_path) as db:db.execute("INSERT INTO executor_metadata VALUES(?)",(good,))
            self.assertEqual(profile.validate_profile(self.candidate,fresh=False,cli_home=self.home),{**self.expected,"actual_executor_checked":True})
            with sqlite3.connect(self.db_path) as db:db.execute("UPDATE executor_metadata SET data=?",(bad,))
            try:
                with self.assertRaises(profile.ProposalProfileError):profile.validate_profile(self.candidate,fresh=False,cli_home=self.home)
            finally:
                with sqlite3.connect(self.db_path) as db:db.execute("DELETE FROM executor_metadata")
            self.check_positive()
        with self.assertRaises(profile.ProposalProfileError):profile.validate_profile(self.candidate,fresh=False,cli_home=self.home)

    def test_database_links_size_and_duplicate_rows_reject(self):
        link=self.db_path.with_suffix(".other")
        os.link(self.db_path,link)
        try:
            with self.assertRaises(profile.ProposalProfileError):profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:link.unlink()
        self.check_positive()
        with sqlite3.connect(self.db_path) as db:db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)",(self.oracle,))
        try:
            with self.assertRaises(profile.ProposalProfileError):profile.validate_profile(self.candidate,fresh=True,cli_home=self.home)
        finally:
            with sqlite3.connect(self.db_path) as db:db.execute("DELETE FROM trajectory_metadata_blob WHERE rowid=(SELECT MAX(rowid) FROM trajectory_metadata_blob)")
        self.check_positive()

    def test_session_directory_bound_and_small_file_noreplace(self):
        target=self.root/"small"
        profile._new_file(target,b"data")
        self.assertEqual(target.read_bytes(),b"data")
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):profile._new_file(target,b"overwrite")
        self.assertEqual(target.read_bytes(),b"data")
        with mock.patch.object(profile,"MAX_DIRECTORY_ENTRIES",0):
            with self.assertRaises(profile.ProposalProfileError):profile._sessions(self.home/"conversations")

    def test_trust_dialog_is_exact_owned_path_and_never_general_approval(self):
        dialog=f"Do you trust this workspace? {self.work} 1. Yes, I trust this folder  No  Enter to confirm"
        self.assertEqual(profile.trust_choice(dialog,self.work),b"1\r")
        styled=dialog.replace("Enter","\x1b[1mEnter\x1b[0m")
        self.assertEqual(profile.trust_choice(styled,self.work),b"1\r")
        self.assertIsNone(profile.trust_choice("Do you trust this workspace?",self.work))
        for text in ("login please Enter", "Yes, I trust", "Do you trust me", "Available Agents"):
            self.assertIsNone(profile.trust_choice(text,self.work))
        for text in (dialog.replace(str(self.work),str(self.root/"foreign")),dialog.replace(str(self.work),str(self.work)+"-other"),dialog.replace("Yes, I trust","Trust everything")):
            with self.assertRaises(profile.ProposalProfileError):profile.trust_choice(text,self.work)
        self.assertEqual(profile.trust_choice(dialog,self.work),b"1\r")

    def test_preparation_failure_cleans_owned_process_and_never_sends_prompt(self):
        self.source.unlink()
        self.source.parent.rmdir()
        process=mock.Mock()
        process.poll.return_value=None
        closed=[]
        clock=iter([0,0,0,46])
        with mock.patch.object(profile.subprocess,"run",return_value=mock.Mock(returncode=0)), mock.patch.object(profile.subprocess,"Popen",return_value=process) as popen, mock.patch.object(profile.time,"monotonic",side_effect=lambda:next(clock)), mock.patch.object(profile.pty,"openpty",return_value=(31,32)), mock.patch.object(profile.termios,"tcsetwinsize"), mock.patch.object(profile.os,"set_blocking"), mock.patch.object(profile.os,"close",side_effect=closed.append), mock.patch.object(profile.selectors,"DefaultSelector") as selector, mock.patch.object(profile.os,"write") as write, mock.patch.object(profile,"_sessions",return_value=set()):
            selector.return_value.select.return_value=[]
            terminate=mock.Mock()
            with self.assertRaises(profile.ProposalProfileError):
                profile.prepare_profile(Path("/tmp/owned-disposable-cli"),self.work,MODEL,"high",terminate=terminate)
            terminate.assert_called_once_with(process)
            self.assertEqual(sorted(closed),[31,32])
            selector.return_value.close.assert_called_once()
            write.assert_not_called()
            command=popen.call_args.args[0]
            self.assertNotIn("--input-format",command)
            self.assertNotIn("--dangerously-skip-permissions",command)

    def test_preparation_termination_failure_still_closes_local_descriptors(self):
        self.source.unlink(); self.source.parent.rmdir()
        process=mock.Mock(); process.poll.return_value=None
        closed=[]; clock=iter([0,0,0,46])
        with mock.patch.object(profile.subprocess,"run",return_value=mock.Mock(returncode=0)) as git, mock.patch.object(profile.subprocess,"Popen",return_value=process), mock.patch.object(profile.time,"monotonic",side_effect=lambda:next(clock)), mock.patch.object(profile.pty,"openpty",return_value=(31,32)), mock.patch.object(profile.termios,"tcsetwinsize"), mock.patch.object(profile.os,"set_blocking"), mock.patch.object(profile.os,"close",side_effect=closed.append), mock.patch.object(profile.selectors,"DefaultSelector") as selector, mock.patch.object(profile,"_sessions",return_value=set()):
            terminate=mock.Mock(side_effect=OSError("owned process cleanup failed"))
            with self.assertRaisesRegex(OSError,"owned process cleanup failed"):
                profile.prepare_profile(Path("/tmp/owned-disposable-cli"),self.work,MODEL,"high",terminate=terminate)
            terminate.assert_called_once_with(process)
            self.assertEqual(sorted(closed),[31,32])
            selector.return_value.close.assert_called_once()
            self.assertEqual(git.call_args.kwargs["env"]["GIT_CONFIG_GLOBAL"],"/dev/null")
            self.assertEqual(git.call_args.kwargs["env"]["GIT_TEMPLATE_DIR"],"")


class RunnerProfileIntegration(unittest.TestCase):
    def setUp(self):
        from tests.test_two_site_proposal_runner import ProposalContracts
        self.legacy=ProposalContracts("runTest")
        self.legacy.setUp()
        self.addCleanup(self.legacy.doCleanups)

    def test_real_run_adds_bound_conversation_before_model_and_keeps_flags(self):
        out=self.legacy.invoke_fake_cli()
        command=self.legacy.last_popen.call_args.args[0]
        self.assertIn("--conversation",command)
        self.assertEqual(command[command.index("--conversation")+1],"session-one")
        self.assertIn("--json-schema",command)
        self.assertIn("--sandbox",command)
        self.assertNotIn("--agent",command)
        self.assertTrue((out/"result.json").exists())
        self.assertFalse((out/"workspace").exists())
        self.assertEqual(self.legacy.last_schema_data,self.legacy.last_cli_schema)
        self.assertEqual(__import__('json').loads((out/"response-schema.json").read_text()),self.legacy.last_cli_schema)

    def test_compile_rejects_duplicate_parameter_that_ast_parse_accepts(self):
        result=self.legacy.result()
        result["changes"][0]["content"]="def duplicated(value, value):\n    return value\n"
        with self.assertRaisesRegex(runner.ProposalError,"generated Python syntax is invalid"):
            runner.validate_result(result,self.legacy.task,self.legacy.sources,"worker")

    def test_response_schema_drift_before_and_during_dispatch_reject(self):
        def alter_schema():
            candidates=list((self.legacy.root/".local/two-site-refactor/proposals").glob("*/response-schema.json"))
            self.assertEqual(len(candidates),1)
            candidates[0].write_text('{}\n')
        for phase in ("before","during"):
            with self.subTest(phase=phase),self.assertRaisesRegex(runner.ProposalError,"response schema binding changed"):
                self.legacy.invoke_fake_cli(run_id='schema-drift-'+phase,pre_provider_check=alter_schema if phase=='before' else None,drift=alter_schema if phase=='during' else None)
            folder=self.legacy.root/".local/two-site-refactor/proposals"/('schema-drift-'+phase)
            self.assertFalse((folder/"result.json").exists())
            (folder/"response-schema.json").unlink()

    def test_mismatched_completion_session_is_not_accepted(self):
        events=self.legacy.events()
        events[0]["conversation_id"]="different"
        events[1]["result"]["conversation_id"]="different"
        with self.assertRaisesRegex(runner.ProposalError,"prepared runtime session"):
            self.legacy.invoke_fake_cli(events=events)
