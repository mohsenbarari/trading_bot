"""Owned PTY/SQLite initialization witnesses; no vendor CLI, auth or Provider.

The PTY is real, but subprocess creation is replaced by a bounded local thread
that accepts only the existing fixed initialization keystrokes. SQLite payloads
are synthetic independent fixtures. Final profile validation is not mocked.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import select
import sqlite3
import stat
import threading
import time
import unittest
from unittest import mock

from scripts import two_site_proposal_runtime_profile as profile
import tests.test_two_site_runtime_proposal_profile as fixtures


PANEL = b"Available Agents\n two-site-proposal-only\n"
SECOND_ID = "22345678-1234-1234-1234-123456789abc"


def _fd_identity(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), info.st_rdev)


class Clock:
    """Only the subject advances virtual time; the owned fake CLI observes it."""

    def __init__(self):
        self.value = -0.25
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.calls += 1
            if self.calls > 1000:
                raise AssertionError("initialization exceeded its bounded clock")
            self.value += 0.25
            return self.value

    def peek(self):
        with self.lock:
            return self.value


class OwnedPTYCLI:
    """An in-process fake CLI on the subject's real owned PTY, never an executor."""

    def __init__(self, fixture, clock, *, ready_at, stdin):
        self.fixture, self.clock, self.ready_at = fixture, clock, ready_at
        self.fd = os.dup(stdin)
        os.set_blocking(self.fd, False)
        self.stop = threading.Event()
        self.selected = threading.Event()
        self.published = threading.Event()
        self.writes = bytearray()
        self.thread_error = None
        self.terminated_at = None
        self.published_while_alive = False
        with sqlite3.connect(fixture.db_path) as db:
            db.execute("CREATE TABLE trajectory_meta(cascade_id TEXT,trajectory_type INTEGER,source INTEGER)")
            db.execute("CREATE TABLE trajectory_metadata_blob(id TEXT,data BLOB)")
            db.execute("CREATE TABLE steps(status INTEGER)")
            db.execute("CREATE TABLE executor_metadata(data BLOB)")
            db.execute("INSERT INTO trajectory_meta VALUES(?,?,?)", (fixtures.CID, 0, 0))
            db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (b"",))
        fixture.db_path.chmod(0o600)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def poll(self):
        return 0 if self.stop.is_set() else None

    def run(self):
        panel_sent = False
        try:
            while not self.stop.is_set():
                readable, _, _ = select.select([self.fd], [], [], 0.005)
                if readable:
                    try:
                        chunk = os.read(self.fd, 4096)
                    except BlockingIOError:
                        chunk = b""
                    self.writes.extend(chunk)
                    if b"/agents" in self.writes and not panel_sent:
                        os.write(self.fd, PANEL)
                        panel_sent = True
                    if b"\x1b[B" in self.writes:
                        self.selected.set()
                if (self.selected.is_set() and self.ready_at is not None
                        and self.clock.peek() >= self.ready_at and not self.published.is_set()):
                    with sqlite3.connect(self.fixture.db_path, timeout=1) as db:
                        db.execute("UPDATE trajectory_meta SET trajectory_type=4,source=17")
                        db.execute("UPDATE trajectory_metadata_blob SET data=? WHERE id='main'", (self.fixture.oracle,))
                    self.published_while_alive = not self.stop.is_set()
                    self.published.set()
        except BaseException as error:
            self.thread_error = error
        finally:
            os.close(self.fd)

    def terminate_owned(self):
        self.terminated_at = self.clock.peek()
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise AssertionError("owned fake CLI did not terminate")


class InitializationReadinessTests(unittest.TestCase):
    def assert_owned_pty_closed(self, fd, original_identity):
        # This controlled fixture distinguishes its owned PTY from regular-file
        # reuse, not arbitrary open-file descriptions that share stat identity.
        try:
            current_identity = _fd_identity(fd)
        except OSError as error:
            self.assertEqual(error.errno, errno.EBADF)
        else:
            self.assertNotEqual(current_identity, original_identity,
                                "owned PTY descriptor identity remains open")

    @contextmanager
    def fixture(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        try:
            yield fixture
        finally:
            fixture.doCleanups()

    def hint(self, fixture, *, before=None, deadline=None):
        return profile._initialization_readiness_hint(
            fixture.home / "conversations", set() if before is None else before,
            fixture.work, fixtures.MODEL, "high",
            deadline=time.monotonic() + 5 if deadline is None else deadline,
        )

    @contextmanager
    def preparation(self, *, ready_at, after_terminate=None):
        with self.fixture() as fixture:
            original = (fixture.db_path.read_bytes(), fixture.source.read_bytes(), fixture.hooks.read_bytes())
            saved = fixture.root / "independent-ready.db"
            fixture.db_path.rename(saved)
            fixture.source.unlink()
            fixture.source.parent.rmdir()
            clock, processes, descriptors, closed_descriptors = Clock(), [], [], []
            real_openpty = profile.pty.openpty
            real_close = profile.os.close
            real_validator = profile.validate_profile
            validation_liveness = []

            def openpty():
                pair = real_openpty()
                descriptors.extend((fd, _fd_identity(fd)) for fd in pair)
                return pair

            def close(fd):
                owned = any(fd == descriptor for descriptor, _ in descriptors)
                identity = _fd_identity(fd) if owned else None
                result = real_close(fd)
                if owned:
                    closed_descriptors.append((fd, identity))
                return result

            def spawn(command, **kwargs):
                self.assertEqual(command, ["/owned/fake-cli", "--mode", "plan", "--sandbox", "--model", fixtures.MODEL, "--effort", "high"])
                self.assertEqual(kwargs["cwd"], fixture.work)
                self.assertEqual(kwargs["stdin"], kwargs["stdout"])
                self.assertEqual(kwargs["stdin"], kwargs["stderr"])
                self.assertTrue(kwargs["start_new_session"])
                cli = OwnedPTYCLI(fixture, clock, ready_at=ready_at, stdin=kwargs["stdin"])
                processes.append(cli)
                return cli

            def terminate(cli):
                cli.terminate_owned()
                if after_terminate is not None:
                    after_terminate(fixture)

            def validate(*args, **kwargs):
                validation_liveness.append(processes[0].poll() is None)
                return real_validator(*args, **kwargs)

            trace = type("Trace", (), {})()
            trace.fixture, trace.clock, trace.processes = fixture, clock, processes
            trace.result = trace.error = None
            try:
                with mock.patch.object(profile.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                     mock.patch.object(profile.subprocess, "Popen", side_effect=spawn) as popen, \
                     mock.patch.object(profile.pty, "openpty", side_effect=openpty) as pty, \
                     mock.patch.object(profile.os, "close", side_effect=close), \
                     mock.patch.object(profile.time, "monotonic", side_effect=clock), \
                     mock.patch.object(profile, "validate_profile", side_effect=validate):
                    try:
                        trace.result = profile.prepare_profile(
                            Path("/owned/fake-cli"), fixture.work, fixtures.MODEL, "high",
                            terminate=terminate, cli_home=fixture.home,
                        )
                    except BaseException as error:
                        trace.error = error
                self.assertEqual(popen.call_count, 1)
                self.assertEqual(pty.call_count, 1)
                self.assertEqual(len(processes), 1)
                self.assertIsNone(processes[0].thread_error)
                self.assertTrue(processes[0].selected.is_set())
                self.assertEqual(validation_liveness, [False])
                self.assertLess(clock.calls, 1000)
                for fd, identity in descriptors:
                    self.assertEqual(closed_descriptors.count((fd, identity)), 1)
                    self.assert_owned_pty_closed(fd, identity)
                yield trace
            finally:
                for cli in processes:
                    if not cli.stop.is_set():
                        cli.terminate_owned()
                if fixture.db_path.exists():
                    fixture.db_path.unlink()
                saved.rename(fixture.db_path)
                fixture.source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fixture.source.write_bytes(original[1]); fixture.source.chmod(0o600)
                fixture.hooks.write_bytes(original[2])
                self.assertEqual(fixture.db_path.read_bytes(), original[0])
                fixture.check_positive()

    def test_real_pty_placeholder_becomes_valid_after_age20_before_cleanup(self):
        with self.preparation(ready_at=24) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            cli = trace.processes[0]
            self.assertTrue(cli.published.is_set())
            self.assertTrue(cli.published_while_alive)
            self.assertGreaterEqual(cli.terminated_at, 24)
            self.assertLess(cli.terminated_at, 30)
            self.assertEqual(profile.validate_profile(trace.result, fresh=True, cli_home=trace.fixture.home), trace.fixture.expected)
            self.assertNotIn(b"--input-format", cli.writes)
            self.assertNotIn(b"CONTEXT_JSON", cli.writes)

    def test_real_pty_permanently_empty_reaches_original_default_deadline(self):
        with self.preparation(ready_at=None) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
            self.assertFalse(trace.processes[0].published.is_set())
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)
            observation = trace.error.observation
            self.assertEqual(observation["final_new_sessions"], 1)
            self.assertEqual(observation["final_valid_profiles"], 0)
            self.assertEqual(observation["last_initialization_readiness_hint"]["empty_profiles"], 1)
            self.assertTrue(set(observation["last_initialization_readiness_hint"]) == {"reason", "new_sessions", "valid_profiles", "empty_profiles", "invalid_profiles", "sqlite_unavailable"})

    def test_final_validator_rejects_hook_drift_after_ready_hint(self):
        def drift(fixture):
            fixture.hooks.write_bytes(b"{}\n")
        with self.preparation(ready_at=24, after_terminate=drift) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent")
            self.assertTrue(trace.processes[0].published_while_alive)
            self.assertEqual(trace.error.observation["last_initialization_readiness_hint"]["reason"], "READY")
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)

    def test_closed_owned_pty_descriptor_number_can_be_reused_by_regular_file(self):
        with self.fixture() as fixture:
            with (fixture.root / "known-regular-replacement").open("w+b") as replacement:
                self.assertTrue(stat.S_ISREG(os.fstat(replacement.fileno()).st_mode))
                master, slave = os.openpty()
                slave_open = True
                try:
                    original = _fd_identity(slave)
                    with self.assertRaisesRegex(AssertionError, "owned PTY descriptor identity remains open"):
                        self.assert_owned_pty_closed(slave, original)
                    os.close(slave)
                    slave_open = False
                    self.assert_owned_pty_closed(slave, original)
                    self.assertNotEqual(replacement.fileno(), slave)
                    os.dup2(replacement.fileno(), slave)
                    slave_open = True
                    self.assertEqual(_fd_identity(slave), _fd_identity(replacement.fileno()))
                    self.assert_owned_pty_closed(slave, original)
                finally:
                    if slave_open:
                        os.close(slave)
                    os.close(master)
            fixture.check_positive()

    def test_real_sql_hint_is_readonly_timeout_zero_and_not_acceptance(self):
        with self.fixture() as fixture:
            original = (fixture.db_path.read_bytes(), fixture.source.read_bytes(), fixture.hooks.read_bytes())
            connect = sqlite3.connect
            with mock.patch.object(profile.sqlite3, "connect", wraps=connect) as observed:
                ready, facts = self.hint(fixture)
            self.assertTrue(ready)
            self.assertEqual(facts, {"reason": "READY", "new_sessions": 1, "valid_profiles": 1, "empty_profiles": 0, "invalid_profiles": 0, "sqlite_unavailable": 0})
            self.assertEqual(observed.call_count, 1)
            self.assertEqual(observed.call_args.kwargs, {"uri": True, "timeout": 0})
            self.assertTrue(observed.call_args.args[0].endswith("?mode=ro"))
            self.assertEqual((fixture.db_path.read_bytes(), fixture.source.read_bytes(), fixture.hooks.read_bytes()), original)
            fixture.check_positive()

    def test_initial_placeholder_has_only_fixed_count_reason_observation(self):
        with self.fixture() as fixture:
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("UPDATE trajectory_meta SET trajectory_type=0,source=0")
                db.execute("UPDATE trajectory_metadata_blob SET data=?", (b"",))
            before = fixture.db_path.read_bytes()
            ready, facts = self.hint(fixture)
            self.assertFalse(ready)
            self.assertEqual(facts, {"reason": "INITIAL_METADATA_EMPTY", "new_sessions": 1, "valid_profiles": 0, "empty_profiles": 1, "invalid_profiles": 0, "sqlite_unavailable": 0})
            self.assertEqual(fixture.db_path.read_bytes(), before)
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("UPDATE trajectory_meta SET trajectory_type=4,source=17")
                db.execute("UPDATE trajectory_metadata_blob SET data=?", (fixture.oracle,))
            fixture.check_positive()

    def test_ambiguous_invalid_used_and_preexisting_profiles_never_ready(self):
        with self.fixture() as fixture:
            second = fixture.home / "conversations" / (SECOND_ID + ".db")
            second.write_bytes(fixture.db_path.read_bytes()); second.chmod(0o600)
            with sqlite3.connect(second) as db:
                db.execute("UPDATE trajectory_meta SET cascade_id=?", (SECOND_ID,))
                db.execute("UPDATE trajectory_metadata_blob SET data=?", (fixtures.literal_metadata(fixture.work, fixture.body, root_id=SECOND_ID),))
            try:
                ready, facts = self.hint(fixture)
                self.assertFalse(ready)
                self.assertEqual((facts["reason"], facts["new_sessions"], facts["valid_profiles"]), ("AMBIGUOUS", 2, 2))
            finally:
                second.unlink()
            bad = fixtures.literal_metadata(fixture.work, fixture.body, name="wrong-agent")
            fixture.replace_meta(bad)
            ready, facts = self.hint(fixture)
            self.assertFalse(ready)
            self.assertEqual((facts["reason"], facts["invalid_profiles"]), ("INVALID_METADATA", 1))
            fixture.replace_meta(fixture.oracle)
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("INSERT INTO steps VALUES(3)")
            ready, facts = self.hint(fixture)
            self.assertFalse(ready)
            self.assertEqual((facts["reason"], facts["invalid_profiles"]), ("INVALID_METADATA", 1))
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("DELETE FROM steps")
            with mock.patch.object(profile.sqlite3, "connect", side_effect=AssertionError("preexisting session queried")):
                ready, facts = self.hint(fixture, before={fixtures.CID + ".db"})
            self.assertFalse(ready)
            self.assertEqual((facts["reason"], facts["new_sessions"]), ("NEW_SESSION_COUNT", 0))
            fixture.check_positive()

    def test_busy_read_is_nonblocking_and_expired_hint_does_not_query(self):
        with self.fixture() as fixture:
            with sqlite3.connect(fixture.db_path) as owner:
                owner.execute("BEGIN EXCLUSIVE")
                ready, facts = self.hint(fixture)
                self.assertFalse(ready)
                self.assertEqual((facts["reason"], facts["sqlite_unavailable"]), ("SQLITE_UNAVAILABLE", 1))
            with mock.patch.object(profile.sqlite3, "connect", side_effect=AssertionError("expired hint queried")):
                ready, facts = self.hint(fixture, deadline=time.monotonic() - 1)
            self.assertFalse(ready)
            self.assertEqual(facts["reason"], "DEADLINE")
            fixture.check_positive()

    def test_link_duplicate_and_nine_new_session_bound_preserve_custody(self):
        with self.fixture() as fixture:
            link = fixture.root / "linked.db"
            os.link(fixture.db_path, link)
            try:
                ready, facts = self.hint(fixture)
                self.assertFalse(ready)
                self.assertEqual(facts["invalid_profiles"], 1)
            finally:
                link.unlink()
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (fixture.oracle,))
            ready, facts = self.hint(fixture)
            self.assertFalse(ready)
            self.assertEqual(facts["invalid_profiles"], 1)
            with sqlite3.connect(fixture.db_path) as db:
                db.execute("DELETE FROM trajectory_metadata_blob WHERE rowid=(SELECT MAX(rowid) FROM trajectory_metadata_blob)")
            extras = []
            try:
                for index in range(8):
                    path = fixture.home / "conversations" / (f"32345678-1234-1234-1234-{index:012x}.db")
                    path.write_bytes(b"bounded invalid fixture"); extras.append(path)
                with mock.patch.object(profile.sqlite3, "connect", side_effect=AssertionError("over-limit session queried")):
                    ready, facts = self.hint(fixture)
                self.assertFalse(ready)
                self.assertEqual((facts["reason"], facts["new_sessions"]), ("NEW_SESSION_COUNT", 9))
                self.assertEqual(profile.MAX_NEW_SESSIONS, 8)
            finally:
                for path in extras:
                    path.unlink()
            fixture.check_positive()


if __name__ == "__main__":
    unittest.main()
