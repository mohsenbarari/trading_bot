#!/usr/bin/env python3
"""Hermetic tests for durable quota wait supervisor due inspection."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

import pytest

from scripts.two_site_quota_supervisor_due_inspect import (
    SupervisorDueInspectError,
    inspect_durable_quota_wait_due_locked,
)
from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_due import evaluate_initial_wait_check
from scripts.two_site_quota_wait_recovery import WaitRecoveryError
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
import tests.test_two_site_provider_broker_terminal_record as terminal_record_mod
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    _setup_admission_env,
    fake_clock,
)


def _setup_env_for_kind(tmp_dir: str, kind: str) -> dict[str, Any]:
    if kind == "QUOTA_RESET_UNKNOWN":
        orig_make = terminal_record_mod._make_quota_payload

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return orig_make(*args, kind="QUOTA_RESET_UNKNOWN", **kwargs)

        with mock.patch.object(
            terminal_record_mod, "_make_quota_payload", side_effect=wrapper
        ):
            return _setup_admission_env(tmp_dir)
    return _setup_admission_env(tmp_dir)


@pytest.mark.parametrize(
    "kind,expected_due_state",
    [
        ("QUOTA_RESET_KNOWN", "KNOWN_RESET_REVALIDATION_DUE"),
        ("QUOTA_RESET_UNKNOWN", "UNKNOWN_BACKOFF_REVIEW_DUE"),
    ],
)
def test_supervisor_due_inspect_parametrized_kind(
    kind: str, expected_due_state: str
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_env_for_kind(tmp_dir, kind)
        root = env["root"]
        cycle_id = env["request"]["controller_run_id"]

        p_intent, p_sources = _patch_repo_boundary(env["facts"])
        with p_intent, p_sources:
            rec = admit_initial_quota_wait_locked(
                root, cycle_id, clock=fake_clock
            )
            assert rec["schedule"]["reset_kind"] == kind

            expected = collect_initial_wait_source_expected(
                root, cycle_id, env["epoch"], 1
            )

            created_at = rec["schedule"]["created_at_utc"]
            res_created = inspect_durable_quota_wait_due_locked(root, created_at)
            assert res_created is not None
            assert res_created["controller_run_id"] == cycle_id
            assert res_created["due"]["state"] == "NOT_DUE"
            assert res_created["due"] == evaluate_initial_wait_check(
                rec, expected, created_at
            )

            next_check_at = rec["schedule"]["next_check_at_utc"]
            res_due = inspect_durable_quota_wait_due_locked(root, next_check_at)
            assert res_due is not None
            assert res_due["controller_run_id"] == cycle_id
            assert res_due["due"]["state"] == expected_due_state
            assert res_due["due"] == evaluate_initial_wait_check(
                rec, expected, next_check_at
            )


def test_supervisor_due_inspect_before_wait_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_admission_env(tmp_dir)
        root = env["root"]
        p_intent, p_sources = _patch_repo_boundary(env["facts"])
        with p_intent, p_sources:
            assert inspect_durable_quota_wait_due_locked(root, "2026-09-25T06:00:00Z") is None


def test_supervisor_due_inspect_stop_sentinel_fails_before_evaluator() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_admission_env(tmp_dir)
        root = env["root"]
        cycle_id = env["request"]["controller_run_id"]
        stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"

        p_intent, p_sources = _patch_repo_boundary(env["facts"])
        with p_intent, p_sources:
            admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)
            stop_path.write_text("STOP", encoding="utf-8")
            with mock.patch(
                "scripts.two_site_quota_supervisor_due_inspect.evaluate_initial_wait_check"
            ) as m_eval:
                with pytest.raises(WaitRecoveryError, match="autopilot STOP sentinel exists"):
                    inspect_durable_quota_wait_due_locked(root, "2026-09-25T06:00:00Z")
                m_eval.assert_not_called()


@pytest.mark.parametrize(
    "patch_target,patch_return,expected_err",
    [
        (
            "scripts.two_site_quota_supervisor_due_inspect.read_pending_rescope_intent",
            lambda env: (env["epoch"], 1, {"drift": "intent"}),
            "wait record repository does not match pending rescope intent repository",
        ),
        (
            "scripts.two_site_quota_supervisor_due_inspect.collect_initial_wait_source_expected",
            lambda env: {"repository": {"drift": "expected"}},
            "expected repository does not match pending rescope intent repository",
        ),
    ],
)
def test_supervisor_due_inspect_repo_drift_fails_before_evaluator(
    patch_target: str, patch_return: Any, expected_err: str
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        env = _setup_admission_env(tmp_dir)
        root = env["root"]
        cycle_id = env["request"]["controller_run_id"]

        p_intent, p_sources = _patch_repo_boundary(env["facts"])
        with p_intent, p_sources:
            admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)
            with mock.patch(patch_target, return_value=patch_return(env)):
                with mock.patch(
                    "scripts.two_site_quota_supervisor_due_inspect.evaluate_initial_wait_check"
                ) as m_eval:
                    with pytest.raises(SupervisorDueInspectError, match=expected_err):
                        inspect_durable_quota_wait_due_locked(root, "2026-09-25T06:00:00Z")
                    m_eval.assert_not_called()
