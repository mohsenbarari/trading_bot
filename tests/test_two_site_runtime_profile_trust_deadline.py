"""Offline preparation traces; no real CLI, authentication or network access."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from scripts import two_site_proposal_runtime_profile as profile
import tests.test_two_site_runtime_proposal_profile as fixtures


PANEL = b"Available Agents\n two-site-proposal-only\n"
SELECT = b"\x1b[B\r\x1b"
AGENTS = b"/agents\r"
TRUST = b"1\r"


class TraceClock:
    def __init__(self):
        self.observed = -0.25
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > 1000:
            raise AssertionError("offline preparation exceeded its clock budget")
        self.observed += 0.25
        return self.observed


class PreparationTrace:
    def __init__(self, fixture, events, *, exit_at=None, cleanup_error=None):
        self.fixture = fixture
        self.events = sorted(events)
        self.clock = TraceClock()
        self.drains = []
        self.reads = []
        self.writes = []
        self.closed = []
        self.pending = b""
        self.process = mock.Mock()
        self.process.poll.side_effect = lambda: (
            1 if exit_at is not None and self.clock.observed >= exit_at else None
        )
        self.selector = mock.Mock()
        self.selector.select.side_effect = self.select
        self.terminate = mock.Mock(side_effect=cleanup_error)
        self.original_source = fixture.source.read_bytes()
        self.original_hooks = fixture.hooks.read_bytes()
        self.original_db = fixture.db_path.read_bytes()
        self.original_oracle = fixture.oracle
        self.fault_db = self.original_db
        self.staged = fixture.root / "prepared-fixture.db"
        self.result = self.error = None

    def select(self, timeout):
        self.drains.append((self.clock.observed, timeout))
        if not self.pending and self.events and self.events[0][0] <= self.clock.observed:
            _, self.pending = self.events.pop(0)
        return [(None, 1)] if self.pending else []

    def read(self, fd, size):
        assert fd == 31 and 1 <= size <= 4096
        self.reads.append((self.clock.observed, size))
        data, self.pending = self.pending[:size], self.pending[size:]
        return data

    def write(self, fd, data):
        assert fd == 31
        self.writes.append((self.clock.observed, data))
        if data == SELECT:
            self.staged.rename(self.fixture.db_path)
        return len(data)

    def invoke(self, *, timeout=30):
        f = self.fixture
        self.fault_db = f.db_path.read_bytes()
        f.db_path.rename(self.staged)
        f.source.unlink()
        f.source.parent.rmdir()
        with ExitStack() as patches:
            for owner, name, kwargs in (
                (profile.subprocess, "run", {"return_value": mock.Mock(returncode=0)}),
                (profile.subprocess, "Popen", {"return_value": self.process}),
                (profile.time, "monotonic", {"side_effect": self.clock}),
                (profile.pty, "openpty", {"return_value": (31, 32)}),
                (profile.termios, "tcsetwinsize", {}),
                (profile.os, "set_blocking", {}),
                (profile.os, "close", {"side_effect": self.closed.append}),
                (profile.os, "read", {"side_effect": self.read}),
                (profile.os, "write", {"side_effect": self.write}),
                (profile.selectors, "DefaultSelector", {"return_value": self.selector}),
            ):
                patches.enter_context(mock.patch.object(owner, name, **kwargs))
            try:
                self.result = profile.prepare_profile(
                    Path("/owned/fake-cli"), f.work, fixtures.MODEL, "high",
                    terminate=self.terminate, cli_home=f.home, timeout=timeout,
                )
            except BaseException as error:
                # Retain the actual Subject object; assertions inspect it separately.
                self.error = error
        return self

    def restore(self):
        f = self.fixture
        if self.staged.exists():
            self.staged.rename(f.db_path)
        f.db_path.write_bytes(self.original_db)
        f.source.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        f.source.write_bytes(self.original_source)
        f.source.chmod(0o600)
        f.hooks.write_bytes(self.original_hooks)
        f.oracle = self.original_oracle


class PreparationTrustDeadlineTests(unittest.TestCase):
    @contextmanager
    def owned(self, events, **options):
        f = fixtures.RuntimeProfileContracts("runTest")
        f.setUp()
        trace = None
        try:
            trace = PreparationTrace(f, events, **options)
            yield trace
        finally:
            try:
                if trace is not None:
                    trace.restore()
                    self.assertEqual(f.db_path.read_bytes(), trace.original_db)
                    self.assertIs(f.oracle, trace.original_oracle)
                    f.check_positive()
            finally:
                f.doCleanups()

    def clean(self, trace):
        trace.terminate.assert_called_once_with(trace.process)
        trace.selector.register.assert_called_once_with(31, 1)
        trace.selector.close.assert_called_once()
        self.assertEqual(trace.closed, [32, 31])
        self.assertLess(trace.clock.calls, 1000)
        self.assertTrue(all(delay == 0.1 for _, delay in trace.drains))
        f = trace.fixture
        current_db = trace.staged if trace.staged.exists() else f.db_path
        self.assertEqual(current_db.read_bytes(), trace.fault_db)
        self.assertEqual(f.source.read_bytes(), trace.original_source)
        self.assertEqual(f.hooks.read_bytes(), trace.original_hooks)
        self.assertIs(f.oracle, trace.original_oracle)
        allowed = {AGENTS, SELECT, TRUST, b"\x1b[1;1R"}
        self.assertTrue(all(data in allowed for _, data in trace.writes))

    def positive(self, trace):
        self.clean(trace)
        self.assertIsNone(trace.error)
        self.assertEqual(
            trace.result,
            profile.PreparedProfile(fixtures.CID, trace.fixture.work, fixtures.MODEL, "high"),
        )
        self.assertEqual([data for _, data in trace.writes].count(AGENTS), 1)
        self.assertEqual([data for _, data in trace.writes].count(SELECT), 1)

    def negative(self, trace, message):
        self.clean(trace)
        self.assertIsNone(trace.result)
        self.assertIs(type(trace.error), profile.ProposalProfileError)
        self.assertEqual(str(trace.error), message)
        self.assertNotIn(SELECT, [data for _, data in trace.writes])

    def test_timely_and_late_real_fresh_session(self):
        for arrival in (9, 14, 16, 22):
            with self.subTest(arrival=arrival), self.owned([(arrival, PANEL)]) as trace:
                trace.invoke()
                self.positive(trace)
                selected_at = next(t for t, data in trace.writes if data == SELECT)
                self.assertGreaterEqual(selected_at, max(12, arrival))
                self.assertLess(selected_at, 30)
                if arrival > 12:
                    self.assertTrue(any(t > 12 for t, _ in trace.drains))

    def test_fragmented_title_and_terminal_reply(self):
        events = [(2, b"\x1b[6n"), (10, b"two-site-proposal-only\n"),
                  (12, b"Available "), (14, b"Agents\n")]
        with self.owned(events) as trace:
            trace.invoke()
            self.positive(trace)
            self.assertIn(b"\x1b[1;1R", [data for _, data in trace.writes])
            self.assertGreaterEqual(next(t for t, data in trace.writes if data == SELECT), 14)

    def test_absence_drains_to_each_original_deadline(self):
        for deadline in (20, 30, 45):
            events = [(t, b"neutral\n") for t in range(2, deadline, 3)]
            with self.subTest(deadline=deadline), self.owned(events) as trace:
                trace.invoke(timeout=deadline)
                self.negative(trace, "proposal runtime agent panel label unavailable")
                self.assertGreaterEqual(trace.clock.observed, deadline)
                self.assertLess(trace.clock.observed, deadline + 0.5)
                self.assertTrue(any(t > 12 for t, _ in trace.reads))
                self.assertEqual([data for _, data in trace.writes], [AGENTS])
                self.assertEqual(trace.error.observation["elapsed_seconds"], deadline)

    def test_wrong_name_and_every_legacy_guard(self):
        cases = [(b"Available Agents\nwrong-name", "proposal runtime local profile not discovered")]
        cases += [(PANEL + name.encode(), "proposal runtime inherited legacy agents")
                  for name in ("two-site-coordinator", "two-site-stage-worker", "two-site-stage-verifier")]
        for data, message in cases:
            with self.subTest(message=message, data=data), self.owned([(13, data)]) as trace:
                trace.invoke()
                self.negative(trace, message)
                self.assertLess(trace.clock.observed, 15)

    def test_late_ui_cannot_accept_used_session_metadata(self):
        with self.owned([(22, PANEL)]) as trace:
            f = trace.fixture
            with sqlite3.connect(f.db_path) as db:
                db.execute("INSERT INTO steps VALUES(3)")
                db.execute("INSERT INTO executor_metadata VALUES(?)", (f.executor(),))
            trace.invoke()
            self.clean(trace)
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent")
            self.assertIn(SELECT, [data for _, data in trace.writes])

    def test_owned_incremental_trust_resets_panel_age_not_absolute_deadline(self):
        with self.owned([]) as trace:
            path = str(trace.fixture.work).encode()
            trace.events = [(1, b"Do you trust this workspace? " + path),
                            (3, b" 1. Yes, I trust this folder No Enter to confirm"),
                            (16, PANEL)]
            trace.invoke()
            self.positive(trace)
            answers = [(t, data) for t, data in trace.writes if data == TRUST]
            self.assertEqual(len(answers), 1)
            self.assertGreaterEqual(answers[0][0], 3)
            self.assertGreaterEqual(next(t for t, data in trace.writes if data == AGENTS), answers[0][0] + 7)

    def test_slow_trust_cannot_extend_absolute_deadline(self):
        for deadline in (20, 30, 45):
            with self.subTest(deadline=deadline), self.owned([]) as trace:
                path = str(trace.fixture.work).encode()
                accepted = deadline - 8
                dialog = b"Do you trust this workspace? " + path + b" Yes, I trust this folder No Enter"
                trace.events = [(accepted, dialog), (deadline + 2, PANEL)]
                trace.invoke(timeout=deadline)
                self.clean(trace)
                self.assertIsNone(trace.result)
                self.assertIs(type(trace.error), profile.ProposalProfileError)
                self.assertEqual([data for _, data in trace.writes].count(TRUST), 1)
                self.assertNotIn(SELECT, [data for _, data in trace.writes])
                self.assertGreaterEqual(trace.clock.observed, deadline)
                self.assertLess(trace.clock.observed, deadline + 0.5)

    def test_foreign_and_suffix_trust_reject_before_answer(self):
        cases = (("-other", b"Yes, I trust this folder"),
                 ("/foreign", b"Yes, I trust this folder"),
                 ("", b"Trust everything"))
        for suffix, choice in cases:
            with self.subTest(suffix=suffix, choice=choice), self.owned([]) as trace:
                path = (str(trace.fixture.work) + suffix).encode()
                trace.events = [(2, b"Do you trust this workspace? " + path + b" " + choice + b" No Enter")]
                trace.invoke()
                self.negative(trace, "proposal runtime trust scope mismatch")
                self.assertEqual(trace.writes, [])

    def test_process_exit_and_termination_error_close_all_resources(self):
        with self.owned([], exit_at=5) as trace:
            trace.invoke()
            self.negative(trace, "proposal runtime profile selection failed")
        error = OSError("owned termination failed")
        with self.owned([], cleanup_error=error) as trace:
            trace.invoke()
            self.clean(trace)
            self.assertIs(trace.error, error)
            self.assertIsNone(trace.result)

    def test_ui_bound_remains_enforced(self):
        with self.owned([(1, b"x" * 65537)]) as trace:
            trace.invoke()
            self.negative(trace, "proposal runtime UI exceeds limit")

    def test_selection_is_not_allowed_at_absolute_deadline(self):
        with self.owned([(29.5, PANEL)]) as trace:
            trace.invoke()
            self.clean(trace)
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertNotIn(SELECT, [data for _, data in trace.writes])
