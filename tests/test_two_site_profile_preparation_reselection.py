"""Owned PTY/SQLite pre-generation recovery; no vendor CLI/auth/provider.

CODEX_AUTHORED by /root/main_empty_sqlite_codex_author. This is test source,
not a claim that the vendor cause is known or a live recovery was observed.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import select
import sqlite3
import threading
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_proposal_runtime_profile as profile
from tests import test_two_site_profile_initialization_readiness as support
from tests import test_two_site_runtime_proposal_profile as fixtures

PANEL = b"Available Agents\n two-site-proposal-only\n"
AGENTS = b"/agents\r"
CHOOSE = b"\x1b[B\r\x1b"
SECOND = "22345678-1234-1234-1234-123456789abc"


class Clock(support.Clock):
    def __init__(self):
        super().__init__()
        self.value = -0.1

    def __call__(self):
        with self.lock:
            self.calls += 1
            if self.calls > 1000:
                raise AssertionError("reselection exceeded bounded clock")
            self.value += 0.1
            return self.value


class OwnedReselectionCLI:
    """One synthetic CLI on a real PTY; only local selection publishes metadata."""

    def __init__(self, fixture, clock, stdin, *, mode="recover", first_ready=None,
                 fresh_panel=True, fragmented=False, drift=None, padding=0):
        self.fixture, self.clock = fixture, clock
        self.mode, self.first_ready, self.fresh_panel = mode, first_ready, fresh_panel
        self.fragmented, self.drift, self.padding = fragmented, drift, padding
        self.fd = os.dup(stdin)
        os.set_blocking(self.fd, False)
        self.stop = threading.Event()
        self.error = None
        self.received = bytearray()
        self.panels = self.choices = 0
        self.published = self.drifted = False
        self.terminated_at = None
        self.busy = None
        self._create(fixture.db_path, fixtures.CID)
        if mode in ("invalid", "used", "executor", "multiple"):
            with sqlite3.connect(fixture.db_path) as db:
                if mode == "invalid":
                    db.execute("UPDATE trajectory_meta SET source=99")
                elif mode == "used":
                    db.execute("INSERT INTO steps VALUES(3)")
                elif mode == "executor":
                    db.execute("INSERT INTO executor_metadata VALUES(?)", (b"execution evidence",))
            if mode == "multiple":
                self._create(fixture.home / "conversations" / (SECOND + ".db"), SECOND)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def _create(path, cid):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE trajectory_meta(cascade_id TEXT,trajectory_type INTEGER,source INTEGER)")
            db.execute("CREATE TABLE trajectory_metadata_blob(id TEXT,data BLOB)")
            db.execute("CREATE TABLE steps(status INTEGER)")
            db.execute("CREATE TABLE executor_metadata(data BLOB)")
            db.execute("INSERT INTO trajectory_meta VALUES(?,?,?)", (cid, 0, 0))
            db.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (b"",))
        path.chmod(0o600)

    def _publish(self, cid=fixtures.CID):
        path = self.fixture.home / "conversations" / (cid + ".db")
        metadata = fixtures.literal_metadata(self.fixture.work, self.fixture.body, root_id=cid)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE trajectory_meta SET trajectory_type=4,source=17")
            db.execute("UPDATE trajectory_metadata_blob SET data=? WHERE id='main'", (metadata,))

    def _send(self, data):
        offset = 0
        while offset < len(data) and not self.stop.is_set():
            try:
                offset += os.write(self.fd, data[offset:])
            except BlockingIOError:
                select.select([], [self.fd], [], 0.005)

    def run(self):
        try:
            if self.mode == "busy":
                self.busy = sqlite3.connect(self.fixture.db_path)
                self.busy.execute("BEGIN EXCLUSIVE")
            while not self.stop.is_set():
                ready, _, _ = select.select([self.fd], [], [], 0.005)
                if ready:
                    try:
                        self.received.extend(os.read(self.fd, 4096))
                    except BlockingIOError:
                        pass
                requested = self.received.count(b"/agents")
                if requested > self.panels:
                    self.panels = requested
                    if requested == 1 or self.fresh_panel:
                        if requested > 1 and self.fragmented:
                            full_header = self.fragmented in ("header_first", "header_only", "foreign_name")
                            self._send(b"Available Agents\n" if full_header else b"Available ")
                            complete_at = self.clock.peek() + 0.5
                            while self.clock.peek() < complete_at and not self.stop.is_set():
                                self.stop.wait(0.005)
                            if self.fragmented == "header_first":
                                self._send(b" two-site-proposal-only\n")
                            elif self.fragmented == "foreign_name":
                                self._send(b" foreign-agent\n")
                            elif self.fragmented != "header_only":
                                self._send(b"Agents\n two-site-proposal-only\n")
                        else:
                            self._send(PANEL)
                        self._send(b"x" * self.padding)
                self.choices = self.received.count(b"\x1b[B")
                now = self.clock.peek()
                if self.drift and self.choices and now >= 23 and not self.drifted:
                    path = self.fixture.hooks if self.drift == "hooks" else self.fixture.source
                    path.write_bytes(b"changed owned fixture\n")
                    self.drifted = True
                if self.mode == "exit" and self.choices and now >= 22:
                    self.stop.set()
                first_complete = self.first_ready is not None and self.choices >= 1 and now >= self.first_ready
                recovered = self.choices >= 2 and self.mode in ("recover", "ambiguous")
                if not self.published and (first_complete or recovered):
                    self._publish()
                    if self.mode == "ambiguous":
                        second = self.fixture.home / "conversations" / (SECOND + ".db")
                        self._create(second, SECOND)
                        self._publish(SECOND)
                    self.published = True
        except BaseException as error:
            self.error = error
        finally:
            if self.busy is not None:
                self.busy.close()
            os.close(self.fd)

    def poll(self):
        return 0 if self.stop.is_set() else None

    def terminate_owned(self):
        self.terminated_at = self.clock.peek()
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise AssertionError("owned fake CLI cleanup did not finish")


class ProfilePreparationReselectionTests(unittest.TestCase):
    @contextmanager
    def lab(self, *, cleanup_error=False, timeout=30, queued_stale=False, **options):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        clock = Clock()
        processes, descriptors, commands, master_writes, validations = [], [], [], [], []
        real_openpty, real_write = profile.pty.openpty, profile.os.write
        real_validate = profile.validate_profile
        real_hooks = profile.validate_hooks
        injected_stale = []
        source_before, hooks_before = fixture.source.read_bytes(), fixture.hooks.read_bytes()
        preexisting = fixture.root / "saved-ready.db"
        fixture.db_path.rename(preexisting)
        preexisting_bytes = preexisting.read_bytes()
        fixture.source.unlink()
        fixture.source.parent.rmdir()

        def openpty():
            pair = real_openpty()
            descriptors.extend((fd, support._fd_identity(fd)) for fd in pair)
            return pair

        def write(fd, data):
            if descriptors and fd == descriptors[0][0]:
                master_writes.append(data)
            return real_write(fd, data)

        def spawn(command, **kwargs):
            self.assertEqual(command, ["/owned/fake-cli", "--mode", "plan", "--sandbox",
                                      "--model", fixtures.MODEL, "--effort", "high"])
            self.assertEqual(kwargs["cwd"], fixture.work)
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stdin"], kwargs["stdout"])
            self.assertEqual(kwargs["stdin"], kwargs["stderr"])
            commands.append(command)
            cli = OwnedReselectionCLI(fixture, clock, kwargs["stdin"], **options)
            processes.append(cli)
            return cli

        def terminate(cli):
            cli.terminate_owned()
            if cleanup_error:
                raise OSError("owned cleanup failed")

        def hooks(workspace):
            result = real_hooks(workspace)
            if (queued_stale and processes and not injected_stale
                    and master_writes.count(AGENTS) == 1 and clock.peek() >= 24):
                # Called in the subject before its recovery request: inject an
                # OLD rendering into the actual kernel PTY queue, not buffer.
                processes[0]._send(PANEL)
                self.assertTrue(select.select([descriptors[0][0]], [], [], 0)[0])
                injected_stale.append(PANEL)
            return result

        def validate(*args, **kwargs):
            validations.append(processes[0].poll() is None)
            return real_validate(*args, **kwargs)

        trace = type("Trace", (), {})()
        trace.result = trace.error = None
        trace.fixture, trace.clock = fixture, clock
        trace.processes, trace.writes, trace.validations = processes, master_writes, validations
        trace.injected_stale = injected_stale
        try:
            with mock.patch.object(profile.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                 mock.patch.object(profile.subprocess, "Popen", side_effect=spawn), \
                 mock.patch.object(profile.pty, "openpty", side_effect=openpty), \
                 mock.patch.object(profile.os, "write", side_effect=write), \
                 mock.patch.object(profile.time, "monotonic", side_effect=clock), \
                 mock.patch.object(profile, "validate_profile", side_effect=validate), \
                 mock.patch.object(profile, "validate_hooks", side_effect=hooks):
                try:
                    trace.result = profile.prepare_profile(
                        Path("/owned/fake-cli"), fixture.work, fixtures.MODEL, "high",
                        terminate=terminate, cli_home=fixture.home, timeout=timeout,
                    )
                except BaseException as error:
                    trace.error = error
            self.assertEqual(len(commands), 1)
            self.assertEqual(len(processes), 1)
            self.assertIsNone(processes[0].error)
            self.assertFalse(processes[0].thread.is_alive())
            self.assertLess(clock.calls, 1000)
            self.assertTrue(all(data in (AGENTS, CHOOSE, b"\x1b[1;1R", b"1\r") for data in master_writes))
            self.assertLessEqual(master_writes.count(AGENTS), 2)
            self.assertLessEqual(master_writes.count(CHOOSE), 2)
            self.assertTrue(all(alive is False for alive in validations))
            for fd, identity in descriptors:
                support.InitializationReadinessTests.assert_owned_pty_closed(self, fd, identity)
            self.assertEqual(preexisting.read_bytes(), preexisting_bytes)
            yield trace
        finally:
            for cli in processes:
                if cli.thread.is_alive():
                    cli.terminate_owned()
            fixture.source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fixture.source.write_bytes(source_before)
            fixture.hooks.write_bytes(hooks_before)
            fixture.doCleanups()

    def assert_rejected(self, trace):
        self.assertIsNone(trace.result)
        self.assertIs(type(trace.error), profile.ProposalProfileError)

    def test_one_live_reselection_recovers_real_metadata_before_generation(self):
        for fragmented in (False, True, "header_first"):
            with self.subTest(fragmented=fragmented), self.lab(fragmented=fragmented) as trace:
                self.assertIsNone(trace.error)
                self.assertEqual(trace.result, trace.fixture.candidate)
                self.assertEqual(trace.writes.count(AGENTS), 2)
                self.assertEqual(trace.writes.count(CHOOSE), 2)
                self.assertEqual(trace.validations, [False])
                self.assertTrue(trace.processes[0].published)
                self.assertLess(trace.processes[0].terminated_at, 30)
                self.assertEqual(profile.validate_profile(trace.result, fresh=True, cli_home=trace.fixture.home), trace.fixture.expected)

    def test_recovery_header_without_fixed_agent_name_drains_to_original_deadline(self):
        for fragmented in ("header_only", "foreign_name"):
            with self.subTest(fragmented=fragmented), self.lab(mode="empty", fragmented=fragmented) as trace:
                self.assert_rejected(trace)
                self.assertEqual(trace.writes.count(AGENTS), 2)
                self.assertEqual(trace.writes.count(CHOOSE), 1)
                self.assertFalse(trace.processes[0].published)
                self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
                self.assertLess(trace.processes[0].terminated_at, 31)

    def test_slow_original_profile_finishes_while_fresh_panel_is_absent(self):
        with self.lab(first_ready=24, fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertEqual(trace.writes.count(CHOOSE), 1)
            self.assertLess(trace.processes[0].terminated_at, 30)

    def test_no_stale_panel_reuse_and_permanent_empty_keeps_failure_classification(self):
        for fresh_panel in (False, True):
            with self.subTest(fresh_panel=fresh_panel), self.lab(mode="empty", fresh_panel=fresh_panel) as trace:
                self.assert_rejected(trace)
                self.assertEqual(trace.writes.count(AGENTS), 2)
                self.assertEqual(trace.writes.count(CHOOSE), 2 if fresh_panel else 1)
                self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
                self.assertEqual(trace.error.observation["last_initialization_readiness_hint"], {
                    "reason": "INITIAL_METADATA_EMPTY", "new_sessions": 1,
                    "valid_profiles": 0, "empty_profiles": 1,
                    "invalid_profiles": 0, "sqlite_unavailable": 0,
                })
                self.assertEqual(trace.error.observation["final_new_sessions"], 1)
                self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
                self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
                self.assertLess(trace.processes[0].terminated_at, 31)

    def test_queued_old_panel_is_drained_before_new_request_not_reused(self):
        with self.lab(mode="empty", fresh_panel=False, queued_stale=True) as trace:
            self.assert_rejected(trace)
            self.assertEqual(trace.injected_stale, [PANEL])
            self.assertEqual(trace.writes.count(AGENTS), 2)
            self.assertEqual(trace.writes.count(CHOOSE), 1)
            self.assertFalse(trace.processes[0].published)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)

    def test_nonpristine_or_unreadable_placeholders_never_reselect(self):
        for mode in ("invalid", "used", "executor", "multiple", "busy"):
            with self.subTest(mode=mode), self.lab(mode=mode) as trace:
                self.assert_rejected(trace)
                self.assertEqual(trace.writes.count(AGENTS), 1)
                self.assertEqual(trace.writes.count(CHOOSE), 1)
                self.assertFalse(trace.processes[0].published)

    def test_two_valid_profiles_after_reselection_are_not_arbitrarily_chosen(self):
        with self.lab(mode="ambiguous") as trace:
            self.assert_rejected(trace)
            self.assertEqual(trace.writes.count(CHOOSE), 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 2)

    def test_hook_and_source_drift_block_before_second_local_control_input(self):
        for drift in ("hooks", "source"):
            with self.subTest(drift=drift), self.lab(drift=drift) as trace:
                self.assert_rejected(trace)
                self.assertTrue(trace.processes[0].drifted)
                self.assertEqual(trace.writes.count(AGENTS), 1)
                self.assertEqual(trace.validations, [])

    def test_original_deadline_not_extended_for_second_panel(self):
        for timeout in (20, 30, 45):
            with self.subTest(timeout=timeout), self.lab(mode="empty", fresh_panel=False, timeout=timeout) as trace:
                self.assert_rejected(trace)
                self.assertGreaterEqual(trace.processes[0].terminated_at, timeout)
                self.assertLess(trace.processes[0].terminated_at, timeout + 1)
                self.assertLessEqual(trace.writes.count(AGENTS), 2)

    def test_cumulative_ui_limit_not_reset_when_history_is_watermarked(self):
        with self.lab(mode="empty", padding=33000) as trace:
            self.assert_rejected(trace)
            self.assertEqual(str(trace.error), "proposal runtime UI exceeds limit")

    def test_cleanup_failure_or_early_exit_never_returns_recovered_profile(self):
        with self.lab(cleanup_error=True) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), OSError)
            self.assertEqual(str(trace.error), "owned cleanup failed")
            self.assertEqual(trace.validations, [])
        with self.lab(mode="exit") as trace:
            self.assert_rejected(trace)
            self.assertEqual(trace.writes.count(AGENTS), 1)


if __name__ == "__main__":
    unittest.main()
