"""Fail-closed pre-review pytest gate for zero genuinely passed tests."""
from __future__ import annotations

import pytest

_passed_calls = 0


def reset_gate() -> None:
    global _passed_calls
    _passed_calls = 0


def pytest_sessionstart(session: pytest.Session) -> None:
    reset_gate()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _passed_calls
    if (
        getattr(report, "when", None) == "call"
        and getattr(report, "passed", False)
        and not hasattr(report, "wasxfail")
    ):
        _passed_calls += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _passed_calls
    if _passed_calls == 0 and getattr(session, "exitstatus", exitstatus) == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
