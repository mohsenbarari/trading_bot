"""787M strict owned-init recovery; synthetic owned PTYs/SQLite, never vendor CLI.

Actual zero-input 791Q observes event/conversation_id/init and the closed
agent/cwd/model/permission_mode/tools payload, with json_schema absent. Agent
is a string; its expected NAME here is the conservative requested-agent guard,
not an observed vendor value. Advertised tools are discovery, not execution;
only unchanged DB/agent validators accept profiles.
Historical C003 output is UNKNOWN and is neither reclassified nor replayed here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import select
import signal
import stat
import tempfile
import time
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_proposal_runtime_profile as profile
from tests import test_two_site_profile_direct_agent_preparation as direct
from tests import test_two_site_profile_preparation_reselection as owned
from tests import test_two_site_runtime_proposal_profile as fixtures


def init_frame(workspace, cid=fixtures.CID, model=fixtures.MODEL):
    return {"event": "init", "conversation_id": cid,
            "init": {"agent": profile.NAME, "model": model, "cwd": str(workspace),
                     "tools": ["read_file", "run_command"],
                     "permission_mode": "request-review"}}


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode()


class HeadlessInitSchemaContracts(unittest.TestCase):
    WORK = Path("/owned/proposal-runtime-workspace")

    def state(self, raw, names=None):
        return profile._headless_initialization_state(
            bytearray(raw), self.WORK, fixtures.MODEL,
            {fixtures.CID + ".db"} if names is None else names)

    def rejected(self, raw, names=None):
        self.assertNotEqual(self.state(raw, names), "OWNED_INIT_ONLY")

    def test_exact_owned_init_accepts_lf_and_only_pty_terminal_crlf(self):
        raw = encoded(init_frame(self.WORK))
        self.assertEqual(self.state(raw), "OWNED_INIT_ONLY")
        self.assertEqual(self.state(raw[:-1] + b"\r\n"), "OWNED_INIT_ONLY")
        self.assertEqual(self.state(b""), "EMPTY")

    def test_observed_agent_key_requires_exact_requested_string_not_default_or_missing(self):
        raw = encoded(init_frame(self.WORK))
        self.assertEqual(self.state(raw), "OWNED_INIT_ONLY")
        for agent in (None, True, False, 0, 1, [], {}, "", "default",
                      profile.NAME + "-other", profile.NAME.upper(), profile.NAME + "\n",
                      "\ud800"):
            with self.subTest(agent_type=type(agent).__name__):
                value = init_frame(self.WORK); value["init"]["agent"] = agent
                self.assertEqual(self.state(encoded(value)), "UNOWNED_IDENTITY")
        missing = init_frame(self.WORK); del missing["init"]["agent"]
        self.assertEqual(self.state(encoded(missing)), "UNEXPECTED_PAYLOAD")
        misplaced = init_frame(self.WORK)
        misplaced["agent"] = misplaced["init"].pop("agent")
        self.assertEqual(self.state(encoded(misplaced)), "UNEXPECTED_ENVELOPE")
        duplicate = raw.replace(b'"agent":', b'"agent":"wrong","agent":', 1)
        self.assertEqual(self.state(duplicate), "MALFORMED")

    def test_advertised_tool_names_are_bounded_discovery_not_tool_attempts(self):
        for tools in ([], ["read_file"], ["read_file", "run_command"]):
            with self.subTest(tools=tools):
                value = init_frame(self.WORK); value["init"]["tools"] = tools
                self.assertEqual(self.state(encoded(value)), "OWNED_INIT_ONLY")
        for tools in (["duplicate", "duplicate"], ["x" * 129], ["has space"],
                      ["line\nfeed"], ["\ud800"], [{"tool_name": "read_file"}],
                      [str(index) for index in range(129)], None, "read_file"):
            with self.subTest(shape=type(tools).__name__):
                value = init_frame(self.WORK); value["init"]["tools"] = tools
                self.rejected(encoded(value))

    def test_closed_payload_envelope_and_no_schema_or_execution_fields(self):
        for key, extra in (("step_update", {}), ("result", {}), ("error", "auth"),
                           ("tool_name", "read_file"), ("tool_info", {}),
                           ("subagent_info", {}), ("denied_actions", []),
                           ("step_type", "tool"), ("unknown", None)):
            with self.subTest(top=key):
                value = init_frame(self.WORK); value[key] = extra
                self.rejected(encoded(value))
            with self.subTest(payload=key):
                value = init_frame(self.WORK); value["init"][key] = extra
                self.rejected(encoded(value))
        for schema in (None, {}, {"type": "object"}):
            value = init_frame(self.WORK); value["init"]["json_schema"] = schema
            self.rejected(encoded(value))
        for event in ("user", "step_update", "result", "error", "control", "auth", "trust"):
            value = init_frame(self.WORK); value["event"] = event
            self.rejected(encoded(value))

    def test_exact_fresh_cid_model_workspace_permission_and_types_required(self):
        for field, value in (("model", "wrong-model"), ("model", None),
                             ("model", {"model": fixtures.MODEL}),
                             ("cwd", str(self.WORK) + "-other"),
                             ("permission_mode", "bypassPermissions"),
                             ("permission_mode", "plan")):
            with self.subTest(field=field):
                event = init_frame(self.WORK); event["init"][field] = value
                self.rejected(encoded(event))
        for cid in (direct.EmptyStartupRecoveryContracts.SECOND, "invalid", None, 1):
            with self.subTest(cid_type=type(cid).__name__):
                value = init_frame(self.WORK); value["conversation_id"] = cid
                self.rejected(encoded(value))
        raw = encoded(init_frame(self.WORK))
        for names in (set(), {direct.EmptyStartupRecoveryContracts.SECOND + ".db"},
                      {fixtures.CID + ".db", direct.EmptyStartupRecoveryContracts.SECOND + ".db"}):
            with self.subTest(names_count=len(names)):
                self.rejected(raw, names)

    def test_duplicate_keys_constants_surrogates_depth_and_invalid_utf8_rejected(self):
        raw = encoded(init_frame(self.WORK))
        duplicates = (
            raw.replace(b'"event":"init"', b'"event":"init","event":"init"'),
            raw.replace(b'"tools":', b'"model":"duplicate","tools":'),
            raw.replace(b'"tools":', b'"tools":[],"tools":'),
        )
        for value in duplicates:
            self.rejected(value)
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            self.rejected(raw.replace(b'"request-review"', constant))
        self.rejected(raw.replace(b'"read_file"', b'"\\ud800"'))
        self.rejected(raw.replace(b'"read_file"', b'"\xff"'))
        self.rejected(b'{"nested":' * 1200 + b'0' + b'}' * 1200 + b'\n')
        self.rejected(b'{"unknown":' + b'"x"' * 22000 + b'}\n')

    def test_no_truncation_extra_frames_control_prefix_suffix_or_trailing_garbage(self):
        raw = encoded(init_frame(self.WORK))
        for value in (raw[:-1], raw[:-2] + b"\n", raw + raw, raw + b"\n",
                      b"\n" + raw, b"\x1b[0m" + raw, raw[:-1] + b"\rX\n",
                      raw[:-1] + b"\t\n", raw[:-1] + b" garbage\n",
                      raw + b'{"event":"step_update"}\n',
                      raw + b'{"event":"result"}\n',
                      raw + b'{"event":"error"}\n'):
            with self.subTest(byte_count=len(value)):
                self.rejected(value)


class OwnedHeadlessRecoveryContracts(unittest.TestCase):
    SECOND = direct.EmptyStartupRecoveryContracts.SECOND
    UNKNOWN = direct.EmptyStartupRecoveryContracts.UNKNOWN

    def test_owned_agent_diagnostic_boolean_matrix_and_escaped_identity(self):
        """Real parser/diagnostics must compose without relaxing any other veto."""
        workspace = Path("/owned/proposal-runtime-workspace")
        names = {fixtures.CID + ".db"}
        process = mock.Mock()
        process.poll.return_value = None

        def eligible(raw, *, fact_changes=None, missing=None, report_changes=None):
            facts = profile._preparation_failure_observation(
                bytearray(raw), process, workspace, False, False, False)
            if fact_changes:
                facts.update(fact_changes)
            if missing is not None:
                del facts[missing]
            error = profile.ProposalProfileError("fixed eligibility sentinel")
            error.preparation_observation = facts
            report = {
                "cleanup_normal": True, "descriptors_closed": True, "process_dead": True,
                "empty_seen": True, "ready_seen": False, "final_valid_profiles": 0,
                "names": names, "headless_init_names": frozenset(names),
                "headless_init_state": profile._headless_initialization_state(
                    bytearray(raw), workspace, fixtures.MODEL, names),
                "invalid_seen": False, "multiple_seen": False,
                "sqlite_seen": False, "bad_seen": False,
            }
            if report_changes:
                report.update(report_changes)
            return profile._empty_recovery_eligible(error, report), facts, report

        raw = encoded(init_frame(workspace))
        accepted, facts, report = eligible(raw)
        self.assertEqual(report["headless_init_state"], "OWNED_INIT_ONLY")
        self.assertIs(facts["owned_workspace_present"], True)
        self.assertIs(facts["fixed_agent_name_present"], True)
        self.assertIs(accepted, True)
        escaped_agent = (b'"agent":"\\u0074' + profile.NAME[1:].encode("ascii") + b'"')
        escaped = raw.replace(b'"agent":' + json.dumps(profile.NAME).encode("ascii"),
                              escaped_agent, 1)
        self.assertNotEqual(escaped, raw)
        accepted, escaped_facts, escaped_report = eligible(escaped)
        self.assertEqual(escaped_report["headless_init_state"], "OWNED_INIT_ONLY")
        self.assertIs(escaped_facts["fixed_agent_name_present"], False)
        self.assertIs(escaped_facts["owned_workspace_present"], True)
        self.assertIs(accepted, True)

        fixed = {"capture_state", "ui_bytes", "process_state_before_cleanup"}
        expected = {"owned_workspace_present", "fixed_agent_name_present"}
        for key in set(facts) - fixed - expected:
            with self.subTest(veto=key):
                self.assertIs(facts[key], False)
                self.assertIs(eligible(raw, fact_changes={key: True})[0], False)
        for value in (False, None, 0, 1, "true"):
            with self.subTest(workspace_flag_type=type(value).__name__):
                self.assertIs(eligible(raw, fact_changes={"owned_workspace_present": value})[0], False)
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(name_flag_type=type(value).__name__):
                self.assertIs(eligible(raw, fact_changes={"fixed_agent_name_present": value})[0], False)
        for key in facts:
            with self.subTest(missing=key):
                self.assertIs(eligible(raw, missing=key)[0], False)
        for key, value in (("cleanup_normal", False), ("descriptors_closed", False),
                           ("process_dead", False), ("ready_seen", True),
                           ("invalid_seen", True), ("multiple_seen", True),
                           ("final_valid_profiles", 1), ("empty_seen", False),
                           ("headless_init_names", frozenset()),
                           ("bad_seen", True), ("sqlite_seen", None)):
            with self.subTest(report_veto=key):
                self.assertIs(eligible(raw, report_changes={key: value})[0], False)
        self.assertIs(eligible(raw, report_changes={"bad_seen": True, "sqlite_seen": True})[0], True)
        wrong_agent = init_frame(workspace)
        wrong_agent["init"]["agent"] = "foreign-agent"
        wrong_agent["init"]["tools"] = [profile.NAME]
        for other in (encoded(wrong_agent),
                      (profile.NAME + " " + str(workspace) + "\n").encode()):
            with self.subTest(unowned_byte_count=len(other)):
                accepted, other_facts, other_report = eligible(other)
                self.assertIs(other_facts["fixed_agent_name_present"], True)
                self.assertIs(other_facts["owned_workspace_present"], True)
                self.assertNotEqual(other_report["headless_init_state"], "OWNED_INIT_ONLY")
                self.assertIs(accepted, False)
        self.assertIs(eligible(raw)[0], True)

    @contextmanager
    def lab(self, *, frame=None, transient_sqlite=False, history=None, **options):
        """Reuse the real owned two-PTY/SQLite lab without replacing its validators."""
        original_init = direct._RecoveryCLI.__init__
        actual_hint = profile._initialization_readiness_hint
        hints = []

        def initialize(cli, fixture, clock, stdin, cid, **cli_options):
            original_init(cli, fixture, clock, stdin, cid, **cli_options)
            if cid == fixtures.CID:
                value = init_frame(fixture.work, cid)
                data = encoded(value) if frame is None else frame(value)
                cli._send(data)

        def hint(*args, **kwargs):
            ready, facts = actual_hint(*args, **kwargs)
            hints.append(facts)
            if len(hints) == 1 and (transient_sqlite or history):
                changed = dict(facts)
                if transient_sqlite:
                    changed.update(reason="SQLITE_UNAVAILABLE", empty_profiles=0,
                                   sqlite_unavailable=1)
                if history:
                    changed.update(history)
                return False, changed
            return ready, facts

        with mock.patch.object(direct._RecoveryCLI, "__init__", new=initialize), \
             mock.patch.object(profile, "_initialization_readiness_hint", side_effect=hint), \
             direct.EmptyStartupRecoveryContracts.lab(self, **options) as trace:
            trace.hints = hints
            yield trace

    def rejected(self, trace, attempts):
        self.assertIsNone(trace.result)
        self.assertIs(type(trace.error), profile.ProposalProfileError)
        self.assertEqual(len(trace.commands), attempts)

    def test_owned_complete_init_allows_one_fresh_initializer_after_pristine_failure(self):
        with self.lab() as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result.conversation_id, self.SECOND)
            self.assertEqual(len(trace.commands), 2)
            self.assertEqual(trace.pin_calls, [None, 240.0])
            self.assertEqual(trace.writes, [])
            self.assertLess(trace.clock.peek(), 240)
            self.assertFalse(trace.processes[0].published)
            self.assertTrue(trace.processes[1].published)
            self.assertEqual(profile.validate_profile(
                trace.result, fresh=True, cli_home=trace.fixture.home)["conversation_id"], self.SECOND)
            with sqlite3.connect(trace.fixture.db_path) as db:
                self.assertEqual(db.execute("SELECT * FROM trajectory_meta").fetchall(), [(fixtures.CID, 0, 0)])
                self.assertEqual(db.execute("SELECT * FROM trajectory_metadata_blob").fetchall(), [("main", b"")])
                self.assertEqual(db.execute("SELECT * FROM steps").fetchall(), [])
                self.assertEqual(db.execute("SELECT * FROM executor_metadata").fetchall(), [])

    def test_transient_sqlite_hint_only_owned_init_can_recover_after_real_pristine_proof(self):
        with self.lab(transient_sqlite=True, wal_first=True) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(len(trace.commands), 2)
            self.assertEqual(trace.result.conversation_id, self.SECOND)
            self.assertGreater(len(trace.hints), 1)
            self.assertEqual(trace.hints[-1]["valid_profiles"], 1)
            self.assertEqual(trace.writes, [])

    def test_second_empty_never_creates_third_initializer_and_keeps_fixed_init_diagnostic(self):
        with self.lab(second={"mode": "empty"}) as trace:
            self.rejected(trace, 2)
            self.assertIn("phase=SECOND reason=INITIALIZER_FAILED attempts=2", str(trace.error))

    def test_wrong_model_cid_workspace_extra_event_or_unknown_payload_cannot_retry(self):
        def wrong(field, value):
            def frame(event):
                event["init"][field] = value
                return encoded(event)
            return frame
        def wrong_cid(event):
            event["conversation_id"] = self.UNKNOWN
            return encoded(event)
        def extra(event):
            return encoded(event) + b'{"event":"error"}\n'
        for name, frame in (("agent", wrong("agent", "wrong-agent")),
                            ("agent_type", wrong("agent", None)),
                            ("model", wrong("model", "wrong")),
                            ("workspace", wrong("cwd", "/foreign")),
                            ("unknown", wrong("unknown", "value")),
                            ("cid", wrong_cid), ("extra", extra)):
            with self.subTest(case=name), self.lab(frame=frame, transient_sqlite=True) as trace:
                self.rejected(trace, 1)
                self.assertNotIn(" init=OWNED_INIT_ONLY", str(trace.error))

    def test_invalid_multiple_or_ready_history_stays_sticky_despite_init_and_sqlite(self):
        histories = ({"invalid_profiles": 1}, {"new_sessions": 2}, {"valid_profiles": 1})
        for history in histories:
            with self.subTest(history=history), self.lab(
                    transient_sqlite=True, history=history) as trace:
                self.rejected(trace, 1)
                self.assertIn(" init=OWNED_INIT_ONLY", str(trace.error))

    def test_early_exit_unproved_pin_or_trust_remains_ineligible(self):
        for name, options in (("exit", {"first": {"mode": "exit"}, "timeout": 30}),
                              ("pin", {"pin": False}),
                              ("trust", {"first": {"mode": "empty", "startup_ui": "foreign"}})):
            with self.subTest(case=name), self.lab(**options) as trace:
                self.rejected(trace, 1)

    def test_cleanup_error_and_alive_first_process_never_authorize_second(self):
        sentinel = OSError("owned cleanup sentinel")
        with self.lab(cleanup_error=sentinel) as trace:
            self.assertIs(trace.error, sentinel)
            self.assertIsNone(trace.result)
            self.assertEqual(len(trace.commands), 1)
        with self.lab(leave_alive=True) as trace:
            self.rejected(trace, 1)

    def test_dirty_first_metadata_steps_executor_or_unknown_roster_rejects_before_retry(self):
        statements = (("INSERT INTO steps VALUES(3)", ()),
                      ("INSERT INTO executor_metadata VALUES(?)", (b"executor",)),
                      ("INSERT INTO trajectory_metadata_blob VALUES('extra',?)", (b"",)))
        for sql, values in statements:
            def mutate(fixture, _processes, _clock, sql=sql, values=values):
                with sqlite3.connect(fixture.db_path) as db:
                    db.execute(sql, values)
            with self.subTest(sql=sql), self.lab(after_first=mutate, transient_sqlite=True) as trace:
                self.rejected(trace, 1)
                self.assertIn("phase=PRE_SECOND reason=PRISTINE", str(trace.error))
        def extra(fixture, _processes, _clock):
            owned.OwnedReselectionCLI._create(
                fixture.home / "conversations" / (self.UNKNOWN + ".db"), self.UNKNOWN)
        with self.lab(after_first=extra) as trace:
            self.rejected(trace, 1)
            # after_first runs inside terminate, before the final session census.
            # Two final names must fail the FIRST singleton guard, before retry.
            self.assertIn("phase=FIRST reason=UI_NONEMPTY attempts=1", str(trace.error))
            self.assertEqual(trace.error.observation["final_new_sessions"], 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(
                trace.error.observation["last_initialization_readiness_hint"]["new_sessions"], 1)
            self.assertEqual(trace.writes, [])

    def test_late_first_metadata_during_cleanup_or_second_is_never_accepted(self):
        with self.lab(first={"mode": "empty", "publish_on_terminate": True}) as trace:
            self.rejected(trace, 1)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)
        def late(_fixture, processes, _clock):
            processes[0]._publish()
        with self.lab(after_second_spawn=late) as trace:
            self.rejected(trace, 2)
            self.assertIn("phase=FINAL reason=FINAL_IDENTITY_STATE", str(trace.error))
        with self.lab(second={"mode": "empty", "publish_on_terminate": True}) as trace:
            self.rejected(trace, 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)

    def test_owned_init_diagnostics_are_fixed_and_contain_no_frame_identity_or_paths(self):
        with self.lab(second={"mode": "empty"}) as trace:
            self.rejected(trace, 2)
            self.assertNotIn(str(trace.fixture.work), str(trace.error))
            self.assertNotIn(fixtures.CID, str(trace.error))
            self.assertNotIn(self.SECOND, str(trace.error))
            self.assertNotIn('"tools"', str(trace.error))
        with self.lab(pin=False) as trace:
            self.rejected(trace, 1)
            self.assertIn(" init=OWNED_INIT_ONLY", str(trace.error))
            self.assertNotIn(str(trace.fixture.work), str(trace.error))
            self.assertNotIn(fixtures.CID, str(trace.error))


class RealSQLiteWALCustodyContracts(unittest.TestCase):
    """Only owned synthetic DBs; no CLI, provider, network or historical data."""

    def produce(self, path, nonempty, wal_mode=True):
        db = sqlite3.connect(path)
        try:
            if wal_mode:
                self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            db.executescript(
                "CREATE TABLE trajectory_meta(cascade_id TEXT,trajectory_type INTEGER,source INTEGER);"
                "CREATE TABLE trajectory_metadata_blob(id TEXT,data BLOB);"
                "CREATE TABLE steps(id INTEGER);CREATE TABLE executor_metadata(id INTEGER);")
            db.execute("INSERT INTO trajectory_meta VALUES(?,0,0)", (fixtures.CID,))
            db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',x'')")
            db.commit()
            if wal_mode:
                self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(), (0, 0, 0))
            if nonempty:
                db.execute("CREATE TABLE synthetic_marker(id INTEGER)")
                db.execute("INSERT INTO synthetic_marker VALUES(1)")
                db.commit()
            return db
        except BaseException:
            db.close()
            raise

    def reap(self, child, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            found, status_code = os.waitpid(child, os.WNOHANG)
            if found == child:
                return status_code
            time.sleep(0.01)
        return None

    @contextmanager
    def database(self, *, writer_live, nonempty, wal_mode=True):
        with tempfile.TemporaryDirectory(prefix="wa794-real-sqlite-wal-", dir="/tmp") as folder:
            directory = Path(folder)
            path = directory / (fixtures.CID + ".db")
            if writer_live:
                writer = self.produce(path, nonempty, wal_mode)
                try:
                    yield directory, path, writer
                finally:
                    writer.close()
            else:
                read_fd, write_fd = os.pipe()
                descriptors = {read_fd, write_fd}
                child = None
                try:
                    child = os.fork()
                    if child == 0:
                        try:
                            os.close(read_fd)
                            producer = self.produce(path, nonempty, wal_mode)
                            os.write(write_fd, b"R")
                            os._exit(0)  # Deliberately retain a genuine orphan WAL.
                        except BaseException:
                            os._exit(2)
                    os.close(write_fd); descriptors.remove(write_fd)
                    self.assertTrue(select.select([read_fd], [], [], 5)[0], "owned producer readiness deadline")
                    self.assertEqual(os.read(read_fd, 1), b"R")
                    os.close(read_fd); descriptors.remove(read_fd)
                    status_code = self.reap(child, 5)
                    self.assertIsNotNone(status_code, "owned producer exit deadline")
                    child = None
                    self.assertEqual(os.waitstatus_to_exitcode(status_code), 0)
                    yield directory, path, None
                finally:
                    failures = []
                    if child is not None:
                        try:
                            try:
                                os.kill(child, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            self.assertIsNotNone(self.reap(child, 2), "owned producer cleanup deadline")
                        except BaseException as error:
                            failures.append(error)
                    for descriptor in descriptors:
                        try:
                            os.close(descriptor)
                        except BaseException as error:
                            failures.append(error)
                    if failures:
                        raise failures[0]

    def raw_snapshot(self, path):
        return profile._persisted_snapshot(path, time.monotonic() + 5, profile.MAX_DATABASE)

    def pristine(self, directory, path):
        return profile._pristine_initialization_snapshot(directory, path.name, time.monotonic() + 10)

    def test_real_orphan_and_live_zero_and_nonempty_wals_ignore_only_cross_read_ctime(self):
        logical = ([(fixtures.CID, "text", 0, "integer", 0, "integer")],
                   [("main", "text", "blob", 0)], [], [])
        for writer_live in (False, True):
            for nonempty in (False, True):
                with self.subTest(writer_live=writer_live, nonempty=nonempty), self.database(
                        writer_live=writer_live, nonempty=nonempty) as (directory, path, writer):
                    wal = Path(str(path) + "-wal")
                    before_db, before_wal = self.raw_snapshot(path), self.raw_snapshot(wal)
                    self.assertEqual(before_wal[0][6] > 0, nonempty)
                    time.sleep(0.02)  # Test-only separation from filesystem ctime resolution.
                    first = self.pristine(directory, path)
                    after_db, after_wal = self.raw_snapshot(path), self.raw_snapshot(wal)
                    self.assertEqual(before_db, after_db)
                    self.assertEqual(before_wal[0][:8], after_wal[0][:8])
                    self.assertEqual(before_wal[1], after_wal[1])
                    # SQLite/VFS may leave ctime unchanged here (for example in
                    # the non-root private/tmp sandbox); it is not a SQL contract.
                    self.assertEqual(first, ((before_db, (before_wal[0][:8], before_wal[1])), logical))
                    self.assertEqual(len(first[0][0][0]), 9)
                    self.assertEqual(len(first[0][1][0]), 8)
                    comparison = profile._pristine_wal_comparison(before_wal)
                    self.assertEqual(comparison, (before_wal[0][:8], before_wal[1]))
                    for position in range(8):
                        with self.subTest(retained_identity_position=position):
                            changed = list(before_wal[0]); changed[position] += 1
                            self.assertNotEqual(profile._pristine_wal_comparison(
                                (tuple(changed), before_wal[1])), comparison)
                    changed_ctime = before_wal[0][:8] + (before_wal[0][8] + 1,)
                    self.assertEqual(profile._pristine_wal_comparison(
                        (changed_ctime, before_wal[1])), comparison)
                    changed_digest = ("0" if before_wal[1][0] != "0" else "1") + before_wal[1][1:]
                    self.assertNotEqual(profile._pristine_wal_comparison(
                        (before_wal[0], changed_digest)), comparison)
                    self.assertIsNone(profile._pristine_wal_comparison(None))
                    self.assertNotEqual(comparison, None)
                    actual_inspect = profile._inspect_shm
                    observed = []

                    def inspect(shm, deadline):
                        actual_inspect(shm, deadline)
                        observed.append(shm)
                        if len(observed) == 2:
                            # Deterministic test-owned change AFTER real SQL and
                            # close, BEFORE the real final persisted-file capture.
                            # This is a chmod fixture, NOT a change attributed to SQL.
                            initial = self.raw_snapshot(wal)
                            time.sleep(0.02)
                            wal.chmod(stat.S_IMODE(wal.stat().st_mode))
                            changed = self.raw_snapshot(wal)
                            self.assertEqual(initial[0][:8], changed[0][:8])
                            self.assertEqual(initial[1], changed[1])
                            self.assertNotEqual(initial[0][8], changed[0][8])

                    with mock.patch.object(profile, "_inspect_shm", side_effect=inspect):
                        controlled = self.pristine(directory, path)
                    self.assertEqual(len(observed), 2, "forced ctime phase must execute after real SQL")
                    self.assertEqual(controlled, first)
                    self.assertEqual(self.pristine(directory, path), first)
                    self.assertEqual(self.raw_snapshot(path), before_db)
                    if writer is not None:
                        self.assertEqual(writer.execute("SELECT * FROM steps").fetchall(), [])
                        self.assertEqual(writer.execute("SELECT * FROM executor_metadata").fetchall(), [])

    def reject_cross_sql_mutation(self, directory, path, mutate):
        actual_inspect = profile._inspect_shm
        calls = []

        def inspect(shm, deadline):
            actual_inspect(shm, deadline)
            calls.append(shm)
            if len(calls) == 2:
                mutate(path, Path(str(path) + "-wal"))

        with mock.patch.object(profile, "_inspect_shm", side_effect=inspect):
            with self.assertRaises(profile.ProposalProfileError):
                self.pristine(directory, path)
        self.assertEqual(len(calls), 2, "mutation must run only after real SQL/close")

    def change_bytes_preserving_eight_identity_fields(self, path):
        before = path.stat()
        data = bytearray(path.read_bytes())
        self.assertGreater(len(data), 0)
        data[-1] ^= 1
        path.write_bytes(data)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(profile._identity9(path.stat())[:8], profile._identity9(before)[:8])

    def replace_inode_preserving_bytes_mode_and_mtime(self, path):
        before = path.stat()
        data = path.read_bytes()
        previous = Path(str(path) + ".old-owned-inode")
        path.rename(previous)
        path.write_bytes(data)
        path.chmod(stat.S_IMODE(before.st_mode))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertNotEqual(path.stat().st_ino, before.st_ino)
        self.assertEqual(path.read_bytes(), data)

    def test_cross_sql_wal_bytes_inode_mode_nlink_mtime_and_presence_drift_still_reject(self):
        def mode(path):
            before = path.stat()
            path.chmod(stat.S_IMODE(before.st_mode) ^ stat.S_IXUSR)
            self.assertNotEqual(path.stat().st_mode, before.st_mode)
        def nlink(path):
            os.link(path, Path(str(path) + ".owned-hardlink"))
            self.assertEqual(path.stat().st_nlink, 2)
        def mtime(path):
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
            self.assertNotEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        def size(path):
            before = path.stat().st_size
            with path.open("ab") as stream:
                self.assertEqual(stream.write(b"\x00"), 1)
            self.assertEqual(path.stat().st_size, before + 1)
        for name, mutate in (("bytes-only", self.change_bytes_preserving_eight_identity_fields),
                             ("inode", self.replace_inode_preserving_bytes_mode_and_mtime),
                             ("mode", mode), ("nlink", nlink), ("mtime", mtime),
                             ("size", size),
                             ("disappears", lambda path: path.unlink())):
            with self.subTest(field=name), self.database(
                    writer_live=False, nonempty=True) as (directory, path, _writer):
                self.reject_cross_sql_mutation(directory, path, lambda _db, wal: mutate(wal))

    def test_wal_absence_to_presence_is_not_normalized_away(self):
        with self.database(writer_live=False, nonempty=False, wal_mode=False) as (directory, path, _writer):
            wal = Path(str(path) + "-wal")
            self.assertFalse(wal.exists())
            def create(_path, owned_wal):
                self.assertFalse(owned_wal.exists())
                with owned_wal.open("xb"):
                    pass
            self.reject_cross_sql_mutation(directory, path, create)
            self.assertTrue(wal.is_file())

    def test_cross_sql_database_full_nine_ctime_bytes_inode_mode_and_mtime_stay_strict(self):
        def ctime(path):
            before = path.stat()
            time.sleep(0.02)  # Test-only; production clocks and budgets are unchanged.
            path.chmod(stat.S_IMODE(before.st_mode))
            after = path.stat()
            self.assertEqual(profile._identity9(before)[:8], profile._identity9(after)[:8])
            self.assertNotEqual(before.st_ctime_ns, after.st_ctime_ns)
        def mode(path):
            path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
        def mtime(path):
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        for name, mutate in (("ctime-only", ctime),
                             ("bytes-only", self.change_bytes_preserving_eight_identity_fields),
                             ("inode", self.replace_inode_preserving_bytes_mode_and_mtime),
                             ("mode", mode), ("mtime", mtime)):
            with self.subTest(field=name), self.database(
                    writer_live=False, nonempty=True) as (directory, path, _writer):
                self.reject_cross_sql_mutation(directory, path, lambda db, _wal: mutate(db))

    def test_single_persisted_wal_read_still_rejects_ctime_only_identity_change(self):
        with self.database(writer_live=False, nonempty=True) as (_directory, path, _writer):
            wal = Path(str(path) + "-wal")
            before = wal.stat()
            actual_read = profile.os.read
            changed = []

            def read(descriptor, count):
                data = actual_read(descriptor, count)
                info = os.fstat(descriptor)
                if not changed and (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino):
                    time.sleep(0.02)  # Exercise real ctime drift within one read, without a clock mock.
                    wal.chmod(stat.S_IMODE(before.st_mode))
                    after = wal.stat()
                    self.assertEqual(profile._identity9(before)[:8], profile._identity9(after)[:8])
                    self.assertNotEqual(before.st_ctime_ns, after.st_ctime_ns)
                    changed.append(True)
                return data

            with mock.patch.object(profile.os, "read", side_effect=read):
                with self.assertRaises(profile.ProposalProfileError):
                    self.raw_snapshot(wal)
            self.assertEqual(changed, [True])

    def test_real_wal_logical_rows_storage_classes_steps_and_executors_remain_pristine(self):
        mutations = (
            "INSERT INTO steps VALUES(1)",
            "INSERT INTO executor_metadata VALUES(1)",
            "INSERT INTO trajectory_metadata_blob VALUES('extra',x'')",
            "UPDATE trajectory_metadata_blob SET data='not-a-blob'",
            "UPDATE trajectory_metadata_blob SET id=x'6d61696e'",
            "UPDATE trajectory_meta SET trajectory_type=9",
            "UPDATE trajectory_meta SET source=x'00'",
            "UPDATE trajectory_meta SET cascade_id='foreign'",
            "INSERT INTO trajectory_meta SELECT * FROM trajectory_meta",
        )
        for number, statement in enumerate(mutations):
            with self.subTest(logical_case=number), self.database(
                    writer_live=True, nonempty=False) as (directory, path, writer):
                writer.execute(statement)
                writer.commit()
                with self.assertRaises(profile.ProposalProfileError):
                    self.pristine(directory, path)


if __name__ == "__main__":
    unittest.main()
