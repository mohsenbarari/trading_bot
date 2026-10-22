"""Direct fixed-agent startup witnesses; no vendor CLI, auth, model or network.

CODEX_AUTHORED by /root/main_empty_sqlite_codex_author. Reuse the owned real
PTY/SQLite fixture as a module, never import/alias its TestCase for collection.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_proposal_runtime_profile as profile
from tests import test_two_site_profile_preparation_reselection as owned
from tests import test_two_site_runtime_proposal_profile as fixtures


class DirectAgentPreparationContracts(unittest.TestCase):
    def lab(self, **options):
        return owned.ProfilePreparationReselectionTests.lab(self, **options)

    def test_fixed_agent_argv_for_both_roles_and_optional_effort_without_prompt(self):
        for model, effort in (("gemini-4-argon-medium", "medium"),
                              ("gemini-4-argon-high", "high"),
                              ("gemini-4-argon-medium", None)):
            with self.subTest(model=model, effort=effort), self.lab(
                    model=model, effort=effort, fresh_panel=False) as trace:
                self.assertIsNone(trace.error)
                self.assertIs(type(trace.result), profile.PreparedProfile)
                self.assertEqual((trace.result.model, trace.result.effort), (model, effort))
                expected = ["/owned/fake-cli", "--mode", "plan", "--sandbox", "--model", model,
                            "--agent", "two-site-proposal-only", "--add-dir", str(trace.fixture.work)]
                if effort is not None:
                    expected += ["--effort", effort]
                expected += ["--input-format", "stream-json", "--output-format", "stream-json"]
                self.assertEqual(trace.commands, [expected])
                self.assertEqual(len(trace.processes), 1)
                self.assertEqual(trace.writes, [])
                self.assertEqual(expected.count("--input-format"), 1)
                self.assertEqual(expected[expected.index("--input-format") + 1], "stream-json")
                self.assertEqual(expected.count("--output-format"), 1)
                self.assertEqual(expected[expected.index("--output-format") + 1], "stream-json")
                self.assertNotIn("--print", expected)
                self.assertNotIn("--prompt", expected)
                self.assertNotIn("--continue", expected)
                self.assertNotIn("--conversation", expected)
                self.assertNotIn("--resume", expected)
                self.assertNotIn("--dangerously-skip-permissions", expected)
                self.assertEqual(trace.fixture.source.read_text(), profile.DEFINITION)
                self.assertEqual(trace.fixture.hooks.read_bytes(), profile.HOOK_DEFINITION)
                self.assertEqual(trace.validations, [False])

    def test_stream_initialization_needs_no_user_event_or_terminal_input(self):
        with self.lab(synchronous_ready=True, fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertEqual(len(trace.commands), 1)
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.writes, [])
            self.assertEqual(bytes(trace.processes[0].received), b"")
            self.assertEqual(trace.validations, [False])
            with sqlite3.connect(trace.fixture.db_path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM steps").fetchone(), (0,))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM executor_metadata").fetchone(), (0,))
            self.assertEqual(profile.validate_profile(
                trace.result, fresh=True, cli_home=trace.fixture.home), trace.fixture.expected)
        # Even the preserved fixed terminal/trust replies cannot encode a
        # stream user-event object; only the later Runner supplies that object.
        dialog = "Do you trust this workspace? /owned/work Yes, I trust this folder Enter"
        replies = [reply for _query, reply in profile.TERM_REPLIES]
        replies.append(profile.trust_choice(dialog, Path("/owned/work")))
        self.assertEqual(replies[-1], b"1\r")
        for reply in replies:
            with self.subTest(reply=reply):
                try:
                    value = json.loads(reply)
                except (ValueError, UnicodeDecodeError):
                    continue
                self.assertNotIsInstance(value, dict)

    def test_readiness_is_observed_from_startup_not_after_old_age20_threshold(self):
        with self.lab(first_ready=2, fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertTrue(trace.processes[0].published)
            self.assertLess(trace.processes[0].terminated_at, 10)
            self.assertEqual(trace.writes, [])

    def test_actual_c004_count_shape_cannot_be_accepted_or_trigger_a_retry(self):
        # C004 exposed only2new/1empty/0final-valid; this invalid second blob
        # is a synthetic realization, not a claim about its historical DB.
        with self.lab(mode="mixed", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=2 empty=1 final_valid=0; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=1 sqlite=0 multiple=1 ui=EMPTY")
            self.assertEqual(trace.error.observation["last_initialization_readiness_hint"], {
                "reason": "INITIAL_METADATA_EMPTY", "new_sessions": 2,
                "valid_profiles": 0, "empty_profiles": 1,
                "invalid_profiles": 1, "sqlite_unavailable": 0,
            })
            self.assertEqual(trace.error.observation["final_new_sessions"], 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(len(trace.processes), 1)
            self.assertEqual(trace.writes, [])
            self.assertFalse(trace.processes[0].published)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)

    def test_stale_ui_cannot_select_default_or_authorize_missing_metadata(self):
        for fragment in (False, "header_only", "foreign_name"):
            with self.subTest(fragment=fragment), self.lab(
                    mode="empty", fresh_panel=True, fragmented=fragment) as trace:
                self.assertIsNone(trace.result)
                self.assertIs(type(trace.error), profile.ProposalProfileError)
                self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
                self.assertEqual(trace.writes, [])
                self.assertEqual(len(trace.commands), 1)


    def test_already_queued_foreign_trust_cannot_be_skipped_by_immediate_valid_metadata(self):
        with self.lab(synchronous_ready=True, startup_ui="foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])

    def test_fragmented_foreign_trust_cannot_be_skipped_by_immediate_valid_metadata(self):
        with self.lab(synchronous_ready=True, startup_ui="partial_foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            self.assertTrue(trace.processes[0].trust_completed)
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 2)

    def test_trust_queued_during_real_ready_hint_is_drained_before_acceptance(self):
        with self.lab(synchronous_ready=True, fresh_panel=False, after_hint_foreign=True) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            self.assertEqual(trace.hint_injections, ["QUEUED_AFTER_REAL_READY_HINT"])
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])

    def test_incomplete_trust_or_meaningful_header_prefix_is_not_metadata_authority(self):
        for fragment in ("incomplete", "prefix_only"):
            with self.subTest(fragment=fragment), self.lab(
                    synchronous_ready=True, startup_ui=fragment, fresh_panel=False) as trace:
                self.assertIsNone(trace.result)
                self.assertIs(type(trace.error), profile.ProposalProfileError)
                self.assertEqual(str(trace.error), "proposal runtime trust dialog incomplete; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
                self.assertEqual(trace.writes, [])
                self.assertEqual(trace.validations, [])
                self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
                self.assertLess(trace.processes[0].terminated_at, 31)

    def test_fragmented_owned_trust_is_answered_once_before_final_strict_validation(self):
        with self.lab(synchronous_ready=True, startup_ui="partial_owned", fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertTrue(trace.processes[0].trust_completed)
            self.assertEqual(trace.writes, [b"1\r"])
            self.assertEqual(trace.validations, [False])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 2)
            self.assertLess(trace.processes[0].terminated_at, 20)

    def test_metadata_published_only_during_cleanup_never_creates_eligibility(self):
        with self.lab(mode="empty", fresh_panel=False, publish_on_terminate=True) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime profile was not ready during bounded preparation; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=1; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=EMPTY")
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)
            self.assertIs(trace.error.observation["ready_during_preparation"], False)
            self.assertEqual(trace.validations, [False])
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.writes, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)

    def test_ordinary_banner_suffix_is_not_misclassified_as_partial_trust(self):
        with self.lab(synchronous_ready=True, startup_ui="ordinary_banner", fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertEqual(trace.writes, [])
            self.assertLess(trace.processes[0].terminated_at, 10)


    def test_truthy_but_empty_legacy_selector_never_reads_unready_fake_descriptor(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        try:
            fixture.source.unlink()
            fixture.source.parent.rmdir()
            # Keep this READ-ONLY fixture's default MagicMock truthiness; only
            # iteration describes actual ready events, and that iterator is empty.
            with (
                fixture._preparation_mocks() as (_git, _popen, process, closed, selector),
                mock.patch.object(profile.os, "read", side_effect=AssertionError(
                    "unready fake descriptor must never be read")) as read,
            ):
                events = selector.return_value.select.return_value
                self.assertTrue(bool(events))
                self.assertEqual(list(events), [])
                terminate = mock.Mock()
                with self.assertRaises(profile.ProposalProfileError) as caught:
                    profile.prepare_profile(
                        Path("/owned/fake-cli"), fixture.work, fixtures.MODEL, "high",
                        terminate=terminate, cli_home=fixture.home,
                    )
                self.assertEqual(str(caught.exception), "proposal runtime fresh session discovery failed; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=EMPTY")
                read.assert_not_called()
                self.assertEqual(selector.return_value.select.call_args_list,
                                 [mock.call(0), mock.call(0.1)])
                terminate.assert_called_once_with(process)
                selector.return_value.register.assert_called_once_with(31, 1)
                selector.return_value.close.assert_called_once()
                self.assertEqual(closed, [32, 31])
            fixture.check_positive()
        finally:
            fixture.doCleanups()


    def test_failure_diagnostics_are_fixed_bounded_and_never_leak_ui_or_workspace(self):
        original_send = owned.OwnedReselectionCLI._send
        secret = "PRIVATE_DIAGNOSTIC_SENTINEL_NOT_A_REAL_CREDENTIAL"
        def send(cli, data):
            payload = ("\n" + secret + " " + str(cli.fixture.work) +
                       " Agent not found unknown agent " + profile.NAME +
                       " Enter sign in connection refused command not found\n").encode()
            return original_send(cli, data + payload)
        with mock.patch.object(owned.OwnedReselectionCLI, "_send", new=send), self.lab(
                mode="empty", startup_ui="ordinary_banner", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            facts = trace.error.preparation_observation
            self.assertEqual(set(facts), {
                "capture_state", "ui_bytes", "process_state_before_cleanup",
                "trust_answer_sent", "pending_trust", "ready_during_preparation",
                "agent_not_found_label", "unknown_agent_label", "not_found_label",
                "fixed_agent_name_present", "trust_marker_present",
                "owned_workspace_present", "enter_boundary_present",
                "authentication_prompt_present", "network_error_present",
                "dependency_missing_present",
            })
            self.assertEqual(facts["capture_state"], "CAPTURED")
            self.assertEqual(facts["process_state_before_cleanup"], "RUNNING")
            self.assertIs(type(facts["ui_bytes"]), int)
            self.assertGreater(facts["ui_bytes"], len(secret))
            self.assertLessEqual(facts["ui_bytes"], 65536)
            for name in set(facts) - {"capture_state", "ui_bytes", "process_state_before_cleanup"}:
                self.assertIs(type(facts[name]), bool)
            for name in ("agent_not_found_label", "unknown_agent_label", "not_found_label",
                         "fixed_agent_name_present", "owned_workspace_present",
                         "enter_boundary_present", "authentication_prompt_present",
                         "network_error_present", "dependency_missing_present"):
                self.assertIs(facts[name], True, name)
            for name in ("trust_answer_sent", "pending_trust", "ready_during_preparation",
                         "trust_marker_present"):
                self.assertIs(facts[name], False, name)
            serialized = json.dumps(trace.error.__dict__, sort_keys=True)
            for sensitive in (secret, str(trace.fixture.work), profile.NAME,
                              "Agent not found unknown agent", "connection refused"):
                self.assertNotIn(sensitive, serialized)
                self.assertNotIn(sensitive, str(trace.error))
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)
            # Detached facts survive actual cleanup; no buffer/prose is retained.
            self.assertFalse(trace.processes[0].thread.is_alive())
            self.assertEqual(facts["process_state_before_cleanup"], "RUNNING")

    def test_diagnostics_cannot_convert_valid_metadata_with_foreign_trust_into_success(self):
        with self.lab(synchronous_ready=True, startup_ui="foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            self.assertEqual(trace.error.preparation_observation["capture_state"], "CAPTURED")
            self.assertIs(trace.error.preparation_observation["trust_marker_present"], True)
            self.assertIs(trace.error.preparation_observation["trust_answer_sent"], False)
            self.assertIs(trace.error.preparation_observation["ready_during_preparation"], False)
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.validations, [])
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)

    def test_diagnostic_capture_failure_preserves_profile_error_and_all_gates(self):
        diagnostic_error = RuntimeError("PRIVATE_DIAGNOSTIC_FAILURE_PROSE")
        with mock.patch.object(profile, "_preparation_failure_observation",
                               side_effect=diagnostic_error) as capture, self.lab(
                mode="empty", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0; PREP_DIAG phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=UNKNOWN")
            self.assertEqual(trace.error.preparation_observation, {"capture_state": "UNAVAILABLE"})
            self.assertNotIn(str(diagnostic_error), repr(trace.error.__dict__))
            capture.assert_called_once()
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)

    def test_diagnostic_failure_never_masks_original_cleanup_error_or_skips_cleanup(self):
        cleanup_error = OSError("owned primary cleanup error")
        original_terminate = owned.OwnedReselectionCLI.terminate_owned
        def terminate(cli):
            original_terminate(cli)
            raise cleanup_error
        with mock.patch.object(profile, "_preparation_failure_observation",
                               side_effect=RuntimeError("PRIVATE_DIAGNOSTIC_FAILURE_PROSE")) as capture, \
             mock.patch.object(owned.OwnedReselectionCLI, "terminate_owned", new=terminate), \
             self.lab(mode="empty", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(trace.error, cleanup_error)
            self.assertEqual(str(trace.error), "owned primary cleanup error")
            capture.assert_called_once()
            self.assertFalse(trace.processes[0].thread.is_alive())
            self.assertEqual(trace.validations, [])
            self.assertEqual(trace.writes, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)


class _RecoveryCLI(owned.OwnedReselectionCLI):
    """One real owned PTY and SQLite file per synthetic zero-action startup."""

    def __init__(self, fixture, clock, stdin, cid, **options):
        self.cid = cid
        super().__init__(fixture, clock, stdin, fresh_panel=False, **options)

    def _create(self, path, cid):
        actual = self.cid if path == self.fixture.db_path else cid
        return super()._create(path, actual)

    def _publish(self, cid=None):
        actual = self.cid if cid is None else cid
        path = self.fixture.home / "conversations" / (actual + ".db")
        metadata = fixtures.literal_metadata(
            self.fixture.work, self.fixture.body, root_id=actual)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE trajectory_meta SET trajectory_type=4,source=17")
            db.execute("UPDATE trajectory_metadata_blob SET data=? WHERE id='main'", (metadata,))


class EmptyStartupRecoveryContracts(unittest.TestCase):
    SECOND = "32345678-1234-1234-1234-123456789abc"
    UNKNOWN = "42345678-1234-1234-1234-123456789abc"

    @contextmanager
    def lab(self, *, first=None, second=None, pin=True, drift_pin=False,
            after_first=None, after_second_spawn=None, cleanup_error=None,
            leave_alive=False, wal_first=False, timeout=20):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        clock = owned.Clock()
        processes, commands, descriptors, writes, setups, holders = [], [], [], [], [], []
        source_before, hooks_before = fixture.source.read_bytes(), fixture.hooks.read_bytes()
        saved = fixture.root / "untouched-preexisting.db"
        fixture.db_path.rename(saved)
        saved_bytes = saved.read_bytes()
        fixture.source.unlink(); fixture.source.parent.rmdir()
        real_openpty, real_write = profile.pty.openpty, profile.os.write
        identity = ((1, 2, 0o100700, os.geteuid(), os.getegid(), 1,
                     profile.REVIEWED_CLI_BYTES, 3, 4), profile.REVIEWED_CLI_SHA256)
        fingerprint = (identity, 240.0)
        pin_calls = []

        def cli_pin(cli, *, deadline=None):
            pin_calls.append(deadline)
            if not pin or (drift_pin and deadline is not None):
                return None
            if pin == "unproved":
                return None, 240.0
            return fingerprint

        def git(command, **kwargs):
            self.assertEqual(command, ["/usr/bin/git", "-C", str(fixture.work), "init",
                                     "-q", "--initial-branch=proposal-runtime-fixture"])
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 10)
            self.assertEqual(set(kwargs["env"]), {
                "PATH", "LANG", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                "GIT_TEMPLATE_DIR", "GIT_TERMINAL_PROMPT"})
            (fixture.work / ".git").mkdir(mode=0o700)
            setups.append(command)
            return mock.Mock(returncode=0)

        def openpty():
            pair = real_openpty()
            descriptors.extend((fd, owned.support._fd_identity(fd)) for fd in pair)
            return pair

        def write(fd, data):
            if any(fd == item[0] for item in descriptors[::2]):
                writes.append(data)
            return real_write(fd, data)

        def spawn(command, **kwargs):
            self.assertLess(len(processes), 2, "there is never a third initialization")
            expected = ["/owned/fake-cli", "--mode", "plan", "--sandbox", "--model",
                        fixtures.MODEL, "--agent", profile.NAME, "--add-dir", str(fixture.work),
                        "--effort", "high", "--input-format", "stream-json",
                        "--output-format", "stream-json"]
            self.assertEqual(command, expected)
            self.assertEqual(kwargs["cwd"], fixture.work)
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], kwargs["stdout"])
            self.assertEqual(kwargs["stdin"], kwargs["stderr"])
            number = len(processes)
            cid = fixtures.CID if number == 0 else self.SECOND
            options = dict(first or {"mode": "empty"}) if number == 0 else dict(
                second or {"mode": "recover", "synchronous_ready": True})
            local = SimpleNamespace(work=fixture.work, home=fixture.home,
                                    source=fixture.source, hooks=fixture.hooks, body=fixture.body,
                                    db_path=fixture.home / "conversations" / (cid + ".db"))
            cli = _RecoveryCLI(local, clock, kwargs["stdin"], cid, **options)
            cli.spawned_at = clock.peek()
            commands.append(command); processes.append(cli)
            if number == 0 and wal_first:
                connection = sqlite3.connect(fixture.db_path)
                self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
                connection.execute("SELECT * FROM trajectory_meta").fetchall()
                holders.append(connection)
                fixture.recovery_wal = connection
            if number == 1 and after_second_spawn:
                after_second_spawn(fixture, processes, clock)
            return cli

        def terminate(cli):
            if leave_alive and cli is processes[0]:
                return
            cli.terminate_owned()
            if cli is processes[0] and after_first:
                after_first(fixture, processes, clock)
            if cleanup_error is not None:
                raise cleanup_error

        trace = SimpleNamespace(result=None, error=None, fixture=fixture, clock=clock,
                                processes=processes, commands=commands, writes=writes,
                                setups=setups, pin_calls=pin_calls)
        try:
            with (
                mock.patch.object(profile, "_recovery_cli_fingerprint", side_effect=cli_pin),
                mock.patch.object(profile.subprocess, "run", side_effect=git),
                mock.patch.object(profile.subprocess, "Popen", side_effect=spawn),
                mock.patch.object(profile.pty, "openpty", side_effect=openpty),
                mock.patch.object(profile.os, "write", side_effect=write),
                mock.patch.object(profile.time, "monotonic", side_effect=clock),
            ):
                try:
                    trace.result = profile.prepare_profile(
                        Path("/owned/fake-cli"), fixture.work, fixtures.MODEL, "high",
                        terminate=terminate, cli_home=fixture.home, timeout=timeout)
                except BaseException as error:
                    trace.error = error
            self.assertEqual(len(setups), 1)
            self.assertLessEqual(len(processes), 2)
            self.assertLess(clock.calls, 1000)
            self.assertEqual(saved.read_bytes(), saved_bytes)
            self.assertTrue(all(data in (b"\x1b[1;1R", b"1\r") for data in writes))
            for cli in processes:
                self.assertIsNone(cli.error)
                if not leave_alive:
                    self.assertFalse(cli.thread.is_alive())
            for fd, original in descriptors:
                owned.support.InitializationReadinessTests.assert_owned_pty_closed(
                    self, fd, original)
            yield trace
        finally:
            for cli in processes:
                if cli.thread.is_alive():
                    cli.terminate_owned()
            for connection in holders:
                connection.close()
            fixture.source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fixture.source.write_bytes(source_before)
            fixture.hooks.write_bytes(hooks_before)
            fixture.doCleanups()

    def rejected(self, trace, attempts):
        self.assertIsNone(trace.result)
        self.assertIs(type(trace.error), profile.ProposalProfileError)
        self.assertEqual(len(trace.commands), attempts)

    def test_exact_empty_first_and_valid_second_accept_only_second_cid(self):
        with self.lab() as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, profile.PreparedProfile(
                self.SECOND, trace.fixture.work, fixtures.MODEL, "high"))
            self.assertEqual(len(trace.commands), 2)
            self.assertEqual(trace.pin_calls, [None, 240.0])
            self.assertEqual(trace.writes, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 20)
            self.assertLess(trace.processes[0].terminated_at, 21)
            self.assertTrue(trace.processes[1].published)
            with sqlite3.connect(trace.fixture.db_path) as db:
                self.assertEqual(db.execute("SELECT * FROM trajectory_meta").fetchall(),
                                 [(fixtures.CID, 0, 0)])
                self.assertEqual(db.execute("SELECT * FROM trajectory_metadata_blob").fetchall(),
                                 [("main", b"")])
                self.assertEqual(db.execute("SELECT * FROM steps").fetchall(), [])
                self.assertEqual(db.execute("SELECT * FROM executor_metadata").fetchall(), [])
            self.assertEqual(profile.validate_profile(
                trace.result, fresh=True, cli_home=trace.fixture.home)["conversation_id"],
                self.SECOND)

    def test_first_ready_returns_once_and_unproved_pin_never_retries(self):
        with self.lab(first={"synchronous_ready": True}) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result.conversation_id, fixtures.CID)
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(trace.pin_calls, [None])
        with self.lab(pin=False) as trace:
            self.rejected(trace, 1)
            self.assertEqual(trace.pin_calls, [None])
        with self.lab(pin="unproved") as trace:
            self.rejected(trace, 1)
            self.assertEqual(trace.pin_calls, [None])

    def test_local_optional_hash_failure_keeps_total_budget_and_overall_expiry_is_terminal(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        try:
            cli = fixture.root / "owned-executable"
            cli.write_bytes(b"#!/bin/sh\n")
            cli.chmod(0o700)
            real_lstat = Path.lstat
            info = real_lstat(cli)
            values = {key: getattr(info, key) for key in (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
                "st_size", "st_mtime_ns", "st_ctime_ns")}
            values["st_size"] = profile.REVIEWED_CLI_BYTES
            size_only_fixture = SimpleNamespace(**values)
            def lstat(path):
                return size_only_fixture if path == cli else real_lstat(path)
            local_error = profile.ProposalProfileError("proposal runtime preparation budget exhausted")
            with (
                mock.patch.object(Path, "lstat", new=lstat),
                mock.patch.object(profile, "_persisted_snapshot", side_effect=local_error),
                mock.patch.object(profile.time, "monotonic", side_effect=[0, 11]),
            ):
                self.assertEqual(profile._recovery_cli_fingerprint(cli), (None, 240))
            with (
                mock.patch.object(Path, "lstat", new=lstat),
                mock.patch.object(profile, "_persisted_snapshot", return_value=((1,), "wrong hash")),
                mock.patch.object(profile.time, "monotonic", side_effect=[0, 1]),
            ):
                self.assertEqual(profile._recovery_cli_fingerprint(cli), (None, 240))
            fixture.source.unlink(); fixture.source.parent.rmdir()
            with (
                mock.patch.object(Path, "lstat", new=lstat),
                mock.patch.object(profile, "_persisted_snapshot", side_effect=local_error),
                mock.patch.object(profile.time, "monotonic", side_effect=[0, 241]),
                mock.patch.object(profile.subprocess, "Popen") as spawn,
                mock.patch.object(profile.subprocess, "run") as git,
            ):
                with self.assertRaises(profile.ProposalProfileError) as caught:
                    profile.prepare_profile(cli, fixture.work, fixtures.MODEL, "high",
                        terminate=mock.Mock(), cli_home=fixture.home)
                self.assertEqual(str(caught.exception), "proposal runtime preparation budget exhausted")
                spawn.assert_not_called(); git.assert_not_called()
        finally:
            fixture.doCleanups()

    def test_second_empty_fails_after_exactly_two_initializations(self):
        with self.lab(second={"mode": "empty"}) as trace:
            self.rejected(trace, 2)
            self.assertIn("LAST_HINT_INITIAL_METADATA_EMPTY", str(trace.error))
            for cli in trace.processes:
                self.assertGreaterEqual(cli.terminated_at - cli.spawned_at, 20)
                self.assertLess(cli.terminated_at - cli.spawned_at, 21)
            self.assertLess(trace.clock.peek(), 240)

    def test_first_invalid_used_executor_multiple_mixed_and_busy_are_not_recoverable(self):
        for mode in ("invalid", "used", "executor", "multiple", "mixed", "busy"):
            with self.subTest(mode=mode), self.lab(first={"mode": mode}) as trace:
                self.rejected(trace, 1)

    def test_ui_and_unknown_diagnostics_or_observed_validity_never_authorize_recovery(self):
        with self.lab(first={"mode": "empty", "startup_ui": "ordinary_banner"}) as trace:
            self.rejected(trace, 1)
            self.assertGreater(trace.error.preparation_observation["ui_bytes"], 0)
        with mock.patch.object(profile, "_preparation_failure_observation",
                               return_value={"capture_state": "UNAVAILABLE"}), self.lab() as trace:
            self.rejected(trace, 1)
        with self.lab(first={"mode": "empty", "publish_on_terminate": True}) as trace:
            self.rejected(trace, 1)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)
            self.assertIs(trace.error.preparation_observation["ready_during_preparation"], False)
        with self.lab(first={"mode": "empty", "startup_ui": "foreign"}) as trace:
            self.rejected(trace, 1)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch; PREP_DIAG phase=FIRST reason=UI_NONEMPTY attempts=1 invalid=0 sqlite=0 multiple=0 ui=NONEMPTY")
            self.assertEqual(trace.writes, [])

    def test_cleanup_failure_or_still_alive_prevents_second_start(self):
        sentinel = OSError("owned cleanup sentinel")
        with self.lab(cleanup_error=sentinel) as trace:
            self.assertIs(trace.error, sentinel)
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 1)
        with self.lab(leave_alive=True) as trace:
            self.rejected(trace, 1)
            self.assertTrue(trace.processes[0].thread.is_alive())

    def test_inter_attempt_cli_hooks_definition_workspace_or_budget_drift_fails_closed(self):
        def hooks(fixture, _processes, _clock):
            fixture.hooks.write_bytes(b"changed\n")
        def definition(fixture, _processes, _clock):
            fixture.source.write_bytes(b"changed\n")
        def workspace(fixture, _processes, _clock):
            fixture.work.chmod(0o755)
        def budget(_fixture, _processes, clock):
            with clock.lock:
                clock.value = 239
        with self.lab(drift_pin=True) as trace:
            self.rejected(trace, 1)
            self.assertEqual(trace.pin_calls, [None, 240.0])
        for name, callback in (("hooks", hooks), ("definition", definition),
                               ("workspace", workspace), ("budget", budget)):
            with self.subTest(drift=name), self.lab(after_first=callback) as trace:
                self.rejected(trace, 1)

    def test_authoritative_post_cleanup_entire_metadata_and_no_actions_are_required(self):
        statements = (
            ("INSERT INTO trajectory_metadata_blob VALUES('extra',?)", (b"",)),
            ("UPDATE trajectory_meta SET cascade_id='different'", ()),
            ("INSERT INTO steps VALUES(3)", ()),
            ("INSERT INTO executor_metadata VALUES(?)", (b"executor",)),
        )
        for sql, values in statements:
            def mutate(fixture, _processes, _clock, sql=sql, values=values):
                with sqlite3.connect(fixture.db_path) as db:
                    db.execute(sql, values)
            with self.subTest(sql=sql), self.lab(after_first=mutate) as trace:
                self.rejected(trace, 1)

    def test_unknown_extra_session_before_or_during_second_is_never_ignored(self):
        def extra(fixture, _processes, _clock):
            owned.OwnedReselectionCLI._create(
                fixture.home / "conversations" / (self.UNKNOWN + ".db"), self.UNKNOWN)
        with self.lab(after_first=extra) as trace:
            self.rejected(trace, 1)
        with self.lab(after_second_spawn=extra) as trace:
            self.rejected(trace, 2)

    def test_late_first_only_and_late_first_plus_valid_second_never_win(self):
        def late(_fixture, processes, _clock):
            processes[0]._publish()
        for second in ({"mode": "empty"}, {"synchronous_ready": True}):
            with self.subTest(second=second), self.lab(
                    second=second, after_second_spawn=late) as trace:
                self.rejected(trace, 2)
                with sqlite3.connect(trace.fixture.db_path) as db:
                    self.assertEqual(db.execute("SELECT trajectory_type,source FROM trajectory_meta").fetchall(),
                                     [(4, 17)])

    def test_first_persisted_identity_or_wal_visible_data_change_during_second_is_rejected(self):
        def replace(fixture, _processes, _clock):
            saved = fixture.db_path.with_suffix(".old")
            fixture.db_path.rename(saved)
            fixture.db_path.write_bytes(saved.read_bytes())
            fixture.db_path.chmod(0o600)
        def wal(sql, values):
            def mutate(fixture, _processes, _clock):
                connection = fixture.recovery_wal
                before = fixture.db_path.read_bytes()
                identity = profile._identity9(fixture.db_path.lstat())
                connection.execute(sql, values)
                connection.commit()
                self.assertEqual(fixture.db_path.read_bytes(), before)
                self.assertEqual(profile._identity9(fixture.db_path.lstat()), identity)
                self.assertTrue(Path(str(fixture.db_path) + "-wal").is_file())
            return mutate
        for name, callback in (("identity", replace),
                ("wal-executor", wal("INSERT INTO executor_metadata VALUES(?)", (b"late WAL executor",))),
                ("wal-step", wal("INSERT INTO steps VALUES(3)", ()))):
            with self.subTest(drift=name), self.lab(
                    wal_first=name.startswith("wal-"), after_second_spawn=callback) as trace:
                self.rejected(trace, 2)

    def test_quiescent_pristine_wal_and_own_readonly_shm_activity_allow_second(self):
        with self.lab(wal_first=True) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result.conversation_id, self.SECOND)
            self.assertEqual(len(trace.commands), 2)
            connection = trace.fixture.recovery_wal
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            self.assertEqual(connection.execute("SELECT * FROM steps").fetchall(), [])
            self.assertEqual(connection.execute("SELECT * FROM executor_metadata").fetchall(), [])

    def test_second_metadata_created_only_during_cleanup_is_not_readiness(self):
        with self.lab(second={"mode": "empty", "publish_on_terminate": True}) as trace:
            self.rejected(trace, 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)
            self.assertIs(trace.error.preparation_observation["ready_during_preparation"], False)

    def test_pristine_snapshot_ignores_only_ephemeral_shm_content_and_mtime(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        try:
            fixture.db_path.unlink()
            owned.OwnedReselectionCLI._create(fixture.db_path, fixtures.CID)
            shm = Path(str(fixture.db_path) + "-shm")
            shm.write_bytes(b"ephemeral index before")
            shm.chmod(0o600)
            before = profile._pristine_initialization_snapshot(
                fixture.home / "conversations", fixture.db_path.name, profile.time.monotonic() + 10)
            shm.write_bytes(b"ephemeral index after!")
            after = profile._pristine_initialization_snapshot(
                fixture.home / "conversations", fixture.db_path.name, profile.time.monotonic() + 10)
            self.assertEqual(before, after)
        finally:
            fixture.doCleanups()

    def test_legacy_validation_retains_five_second_timeout_and_deadline_caps_it(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        connect = profile.sqlite3.connect
        seen = []
        def observe(*args, **kwargs):
            seen.append(kwargs["timeout"])
            return connect(*args, **kwargs)
        try:
            with mock.patch.object(profile.sqlite3, "connect", side_effect=observe):
                profile.validate_profile(fixture.candidate, fresh=True, cli_home=fixture.home)
                self.assertEqual(seen, [5])
                deadline = profile.time.monotonic() + 0.5
                profile.validate_profile(
                    fixture.candidate, fresh=True, cli_home=fixture.home, deadline=deadline)
                self.assertGreater(seen[-1], 0)
                self.assertLessEqual(seen[-1], 0.5)
                with self.assertRaises(profile.ProposalProfileError):
                    profile.validate_profile(
                        fixture.candidate, fresh=True, cli_home=fixture.home, deadline=0)
                self.assertEqual(len(seen), 2)
        finally:
            fixture.doCleanups()


class SafePreparationDiagnosticContracts(unittest.TestCase):
    SECOND = EmptyStartupRecoveryContracts.SECOND
    UNKNOWN = EmptyStartupRecoveryContracts.UNKNOWN

    def lab(self, **options):
        return EmptyStartupRecoveryContracts.lab(self, **options)

    def diagnostic(self, error):
        self.assertIs(type(error), profile.ProposalProfileError)
        prefix, suffix = str(error).split("; PREP_DIAG ")
        self.assertLessEqual(len(suffix), 512)
        self.assertTrue(suffix.isascii())
        self.assertRegex(suffix, r"^phase=(FIRST|PRE_SECOND|SECOND|FINAL) reason=[A-Z_]+ attempts=[12] invalid=(0|1|NA) sqlite=(0|1|NA) multiple=(0|1|NA) ui=(EMPTY|NONEMPTY|UNKNOWN)$")
        return prefix, suffix

    def test_first_declines_persist_ui_pin_and_separate_history_without_retry(self):
        cases = (({"startup_ui": "ordinary_banner"}, "UI_NONEMPTY", "invalid=0 sqlite=0 multiple=0"),
                 ({"mode": "invalid"}, "HISTORY_INVALID", "invalid=1 sqlite=0 multiple=0"),
                 ({"mode": "multiple"}, "HISTORY_MULTIPLE", "invalid=0 sqlite=0 multiple=1"),
                 ({"mode": "busy"}, "HISTORY_SQLITE_UNAVAILABLE", "invalid=0 sqlite=1 multiple=0"))
        for options, reason, history in cases:
            with self.subTest(reason=reason), self.lab(first={"mode": "empty", **options}) as trace:
                self.assertIsNone(trace.result)
                self.assertEqual(len(trace.commands), 1)
                prefix, suffix = self.diagnostic(trace.error)
                self.assertTrue(prefix.startswith("proposal runtime selected session is ambiguous or absent"))
                self.assertIn("phase=FIRST reason=" + reason + " attempts=1", suffix)
                self.assertIn(history, suffix)
        for pin in (False, "unproved"):
            with self.subTest(pin=pin), self.lab(pin=pin) as trace:
                self.assertIsNone(trace.result)
                self.assertEqual(len(trace.commands), 1)
                self.assertEqual(self.diagnostic(trace.error)[1],
                    "phase=FIRST reason=NO_PIN attempts=1 invalid=0 sqlite=0 multiple=0 ui=EMPTY")
        with mock.patch.object(profile, "_preparation_failure_observation",
                               return_value={"capture_state": "UNAVAILABLE"}), self.lab() as trace:
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(self.diagnostic(trace.error)[1],
                "phase=FIRST reason=DIAGNOSTICS_UNAVAILABLE attempts=1 invalid=0 sqlite=0 multiple=0 ui=UNKNOWN")

    def test_transient_sqlite_hint_stays_a_sticky_recovery_veto(self):
        actual = profile._initialization_readiness_hint
        calls = []
        def hint(*args, **kwargs):
            ready, facts = actual(*args, **kwargs)
            calls.append(facts)
            if len(calls) == 1:
                return False, dict(facts, reason="SQLITE_UNAVAILABLE", empty_profiles=0,
                                   sqlite_unavailable=1)
            return ready, facts
        with mock.patch.object(profile, "_initialization_readiness_hint", side_effect=hint), self.lab() as trace:
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 1)
            self.assertGreater(len(calls), 1)
            self.assertEqual(calls[-1]["empty_profiles"], 1)
            self.assertEqual(self.diagnostic(trace.error)[1],
                "phase=FIRST reason=HISTORY_SQLITE_UNAVAILABLE attempts=1 invalid=0 sqlite=1 multiple=0 ui=EMPTY")

    def test_presecond_guard_keeps_the_same_first_error_and_hides_underlying_prose(self):
        actual = profile._initialize_profile_once
        first_errors = []
        def initialize(*args, **kwargs):
            try:
                return actual(*args, **kwargs)
            except profile.ProposalProfileError as error:
                first_errors.append(error)
                raise
        secret = "PRIVATE_GUARD_FAILURE_PATH_CID_PAYLOAD_SENTINEL"
        with mock.patch.object(profile, "_initialize_profile_once", side_effect=initialize), \
             mock.patch.object(profile, "_pristine_initialization_snapshot", side_effect=OSError(secret)), \
             self.lab() as trace:
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(len(first_errors), 1)
            self.assertIs(trace.error, first_errors[0])
            prefix, suffix = self.diagnostic(trace.error)
            self.assertEqual(prefix, "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
            self.assertEqual(suffix, "phase=PRE_SECOND reason=PRISTINE attempts=1 invalid=0 sqlite=0 multiple=0 ui=EMPTY")
            self.assertNotIn(secret, str(trace.error))

    def test_presecond_pin_and_workspace_failure_reasons_do_not_create_second_start(self):
        with self.lab(drift_pin=True) as trace:
            self.assertEqual(len(trace.commands), 1)
            self.assertIn("phase=PRE_SECOND reason=CLI_PIN attempts=1", self.diagnostic(trace.error)[1])
        def hooks(fixture, _processes, _clock):
            fixture.hooks.write_bytes(b"changed\n")
        with self.lab(after_first=hooks) as trace:
            self.assertEqual(len(trace.commands), 1)
            self.assertIn("phase=PRE_SECOND reason=WORKSPACE attempts=1", self.diagnostic(trace.error)[1])

    def test_second_failure_is_distinguished_without_a_third_initializer(self):
        with self.lab(second={"mode": "empty"}) as trace:
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 2)
            prefix, suffix = self.diagnostic(trace.error)
            self.assertEqual(prefix, "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
            self.assertEqual(suffix, "phase=SECOND reason=INITIALIZER_FAILED attempts=2 invalid=0 sqlite=0 multiple=0 ui=EMPTY")

    def test_final_state_guard_preserves_late_first_rejection(self):
        def late(_fixture, processes, _clock):
            processes[0]._publish()
        with self.lab(after_second_spawn=late) as trace:
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 2)
            self.assertIn("phase=FINAL reason=FINAL_IDENTITY_STATE attempts=2", self.diagnostic(trace.error)[1])

    def test_final_validation_failure_keeps_its_original_identity_and_prefix(self):
        actual = profile.validate_profile
        second_calls = []
        sentinel = profile.ProposalProfileError("fixed final validation sentinel")
        def validate(candidate, **kwargs):
            if candidate.conversation_id == self.SECOND:
                second_calls.append(candidate)
                if len(second_calls) == 2:
                    raise sentinel
            return actual(candidate, **kwargs)
        with mock.patch.object(profile, "validate_profile", side_effect=validate), self.lab() as trace:
            self.assertIsNone(trace.result)
            self.assertIs(trace.error, sentinel)
            self.assertEqual(len(trace.commands), 2)
            self.assertEqual(len(second_calls), 2)
            self.assertEqual(self.diagnostic(trace.error), (
                "fixed final validation sentinel",
                "phase=FINAL reason=FINAL_VALIDATE attempts=2 invalid=0 sqlite=0 multiple=0 ui=UNKNOWN"))

    def test_cleanup_uncertainty_and_primary_cleanup_identity_are_preserved(self):
        with self.lab(leave_alive=True) as trace:
            self.assertEqual(len(trace.commands), 1)
            self.assertIn("phase=FIRST reason=CLEANUP_UNCERTAIN attempts=1", self.diagnostic(trace.error)[1])
        sentinel = profile.ProposalProfileError("fixed primary cleanup sentinel")
        with self.lab(cleanup_error=sentinel) as trace:
            self.assertIs(trace.error, sentinel)
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(self.diagnostic(trace.error)[0], "fixed primary cleanup sentinel")
            self.assertIn("reason=CLEANUP_UNCERTAIN", self.diagnostic(trace.error)[1])
        ordinary = OSError("owned original cleanup sentinel")
        with self.lab(cleanup_error=ordinary) as trace:
            self.assertIs(trace.error, ordinary)
            self.assertEqual(str(trace.error), "owned original cleanup sentinel")
            self.assertEqual(len(trace.commands), 1)

    def test_diagnostic_formatter_cannot_throw_replace_failure_or_format_payloads(self):
        prefix = "fixed original preparation failure"
        error = profile.ProposalProfileError(prefix)
        report = {"cleanup_normal": True, "descriptors_closed": True,
                  "process_dead": True, "empty_seen": True, "bad_seen": False,
                  "ready_seen": False, "final_valid_profiles": 0,
                  "invalid_seen": False, "sqlite_seen": False, "multiple_seen": False}
        secret = "PRIVATE_UI_ENV_PATH_CID_DB_SENTINEL"
        error.preparation_observation = {
            "capture_state": "CAPTURED", "ui_bytes": 0,
            "process_state_before_cleanup": "RUNNING", "payload_not_a_fact": secret}
        profile._append_preparation_diagnostic(error, phase="FIRST", reason=[],
            attempts=1, report=report, pinned=True)
        self.assertEqual(error.args, (prefix,))
        profile._append_preparation_diagnostic(error, phase="FIRST", reason="INELIGIBLE",
            attempts=1, report=report, pinned=True)
        self.assertEqual(self.diagnostic(error), (prefix,
            "phase=FIRST reason=INELIGIBLE_OTHER attempts=1 invalid=0 sqlite=0 multiple=0 ui=EMPTY"))
        self.assertNotIn(secret, str(error))
        once = error.args
        profile._append_preparation_diagnostic(error, phase="SECOND", reason="INITIALIZER_FAILED",
            attempts=2, report=report, pinned=True)
        self.assertEqual(error.args, once)
        invalid_args = profile.ProposalProfileError(prefix, object())
        original = invalid_args.args
        profile._append_preparation_diagnostic(invalid_args, phase="FIRST", reason="INELIGIBLE",
            attempts=1, report=report, pinned=True)
        self.assertIs(invalid_args.args, original)

    def test_valid_first_and_valid_second_never_invoke_failure_diagnostics(self):
        with mock.patch.object(profile, "_append_preparation_diagnostic",
                               side_effect=AssertionError("diagnostic used on success")):
            with self.lab(first={"synchronous_ready": True}) as trace:
                self.assertIsNone(trace.error)
                self.assertEqual(trace.result.conversation_id, fixtures.CID)
                self.assertEqual(len(trace.commands), 1)
            with self.lab() as trace:
                self.assertIsNone(trace.error)
                self.assertEqual(trace.result.conversation_id, self.SECOND)
                self.assertEqual(len(trace.commands), 2)


if __name__ == "__main__":
    unittest.main()
