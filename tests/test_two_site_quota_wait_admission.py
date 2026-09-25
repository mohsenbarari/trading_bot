#!/usr/bin/env python3
"""Hermetic unit tests for initial quota wait admission."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_codex_autopilot import (
    ensure_rescope_epoch_dir,
    publish_rescope_intent,
    validate_accepted_repository,
)
from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)
from scripts.two_site_quota_wait_sources import collect_initial_wait_source_expected
from scripts.two_site_quota_wait_storage import (
    WaitStorageError,
    publish_initial_wait_record_bytes,
)
from tests.test_two_site_quota_wait_sources import _setup_test_env

FIXED_CLOCK_UTC = datetime(2026, 9, 25, 6, 0, 0, tzinfo=timezone.utc)

EXPECTED_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "state",
        "controller_run_id",
        "request_id",
        "reservation",
        "request_sha256",
        "terminal_receipt_sha256",
        "response_sha256",
        "semantic_task_fingerprint",
        "rescope_epoch",
        "rescope_attempt",
        "quota_attempt",
        "quota_evidence",
        "schedule",
        "repository",
    }
)

EXPECTED_SCHEDULE_KEYS = frozenset(
    {
        "reset_kind",
        "reset_at_utc",
        "created_at_utc",
        "next_check_at_utc",
        "remaining_check_budget",
        "remaining_resume_budget",
    }
)


def fake_clock() -> datetime:
    return FIXED_CLOCK_UTC


def exploding_clock() -> datetime:
    raise AssertionError("clock must not be invoked on cached wait record")


def _patch_repo_boundary(facts: dict[str, Any]):
    return (
        mock.patch(
            "scripts.two_site_quota_wait_intent.validate_repository",
            side_effect=lambda *a, **k: copy.deepcopy(facts),
        ),
        mock.patch(
            "scripts.two_site_quota_wait_sources.validate_repository",
            side_effect=lambda *a, **k: copy.deepcopy(facts),
        ),
    )


def _setup_admission_env(tmp_dir: str) -> dict[str, Any]:
    (
        root,
        request,
        response,
        _task_path,
        _status_path,
        epoch,
        facts,
        published_receipt,
    ) = _setup_test_env(tmp_dir)
    state_root = root / ".local" / "two-site-refactor"
    autopilot_dir = state_root / "autopilot"
    runs_dir = autopilot_dir / "runs" / request["controller_run_id"]
    epoch_dir = ensure_rescope_epoch_dir(
        autopilot_dir, validate_accepted_repository(facts)
    )
    publish_rescope_intent(epoch_dir, 1)
    return {
        "root": root,
        "state_root": state_root,
        "runs_dir": runs_dir,
        "request": request,
        "response": response,
        "facts": facts,
        "epoch": epoch,
        "epoch_dir": epoch_dir,
        "published_receipt": published_receipt,
    }


class TestTwoSiteQuotaWaitAdmission(unittest.TestCase):
    def test_real_first_publication_and_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                expected_source = collect_initial_wait_source_expected(
                    root, cycle_id, env["epoch"], 1
                )
                record = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )

            self.assertEqual(len(record), 15)
            self.assertEqual(set(record.keys()), EXPECTED_RECORD_KEYS)
            self.assertEqual(record["schema_version"], "1.0")
            self.assertEqual(record["state"], "WAITING_QUOTA")
            self.assertEqual(record["controller_run_id"], cycle_id)
            self.assertEqual(record["request_id"], env["request"]["request_id"])
            self.assertEqual(record["rescope_epoch"], env["epoch"])
            self.assertEqual(record["rescope_attempt"], 1)
            self.assertEqual(record["request_sha256"], expected_source["request_sha256"])
            self.assertEqual(record["terminal_receipt_sha256"], expected_source["terminal_receipt_sha256"])
            self.assertEqual(record["response_sha256"], expected_source["response_sha256"])
            self.assertEqual(record["semantic_task_fingerprint"], expected_source["semantic_task_fingerprint"])
            self.assertEqual(record["reservation"], expected_source["reservation"])
            self.assertEqual(record["repository"], expected_source["repository"])
            self.assertEqual(record["quota_evidence"], expected_source["quota_evidence"])

            quota_attempt = record["quota_attempt"]
            self.assertIsInstance(quota_attempt, dict)
            self.assertEqual(len(quota_attempt), 3)
            self.assertEqual(quota_attempt, expected_source["quota_attempt"])

            schedule = record["schedule"]
            self.assertEqual(len(schedule), 6)
            self.assertEqual(set(schedule.keys()), EXPECTED_SCHEDULE_KEYS)
            self.assertEqual(schedule["created_at_utc"], "2026-09-25T06:00:00Z")
            self.assertEqual(schedule["remaining_resume_budget"], 1)

            evidence = record["quota_evidence"]
            if evidence["kind"] == "QUOTA_RESET_UNKNOWN":
                self.assertEqual(schedule["reset_kind"], "QUOTA_RESET_UNKNOWN")
                self.assertIsNone(schedule["reset_at_utc"])
                self.assertEqual(schedule["next_check_at_utc"], "2026-09-25T06:15:00Z")
                self.assertEqual(schedule["remaining_check_budget"], 5)
            else:
                self.assertEqual(schedule["reset_kind"], "QUOTA_RESET_KNOWN")
                self.assertEqual(schedule["reset_at_utc"], evidence["reset_at_utc"])
                self.assertEqual(schedule["remaining_check_budget"], 0)
                reset_dt = datetime.fromisoformat(
                    evidence["reset_at_utc"][:-1] + "+00:00"
                )
                expected_next_dt = max(
                    FIXED_CLOCK_UTC + timedelta(seconds=30),
                    reset_dt + timedelta(seconds=30),
                )
                timespec = "microseconds" if expected_next_dt.microsecond else "seconds"
                expected_next_str = expected_next_dt.isoformat(timespec=timespec).replace(
                    "+00:00", "Z"
                )
                self.assertEqual(schedule["next_check_at_utc"], expected_next_str)

            self.assertTrue(wait_path.is_file())
            disk_record = json.loads(wait_path.read_text(encoding="utf-8"))
            self.assertEqual(disk_record, record)

    def test_idempotent_second_call_without_clock_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                first_record = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )

                self.assertTrue(wait_path.is_file())
                first_bytes = wait_path.read_bytes()

                clock_mock = mock.Mock(side_effect=exploding_clock)
                second_record = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=clock_mock
                )

                clock_mock.assert_not_called()
                self.assertEqual(second_record, first_record)
                self.assertEqual(wait_path.read_bytes(), first_bytes)

    def test_stop_priority_before_admission_and_after_publication_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"
            stop_path = env["root"] / ".local" / "two-site-refactor" / "autopilot" / "STOP"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                stop_path.write_text("STOP", encoding="utf-8")
                with self.assertRaises(WaitAdmissionError) as ctx1:
                    admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)
                self.assertIn("autopilot STOP sentinel exists", str(ctx1.exception))
                self.assertFalse(wait_path.exists())

                stop_path.unlink()
                record = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                self.assertTrue(wait_path.is_file())

                stop_target = env["root"] / "nonexistent-stop-target"
                stop_path.symlink_to(stop_target)
                self.assertTrue(stop_path.is_symlink())
                self.assertFalse(stop_path.exists())

                clock_mock = mock.Mock(side_effect=exploding_clock)
                with self.assertRaises(WaitAdmissionError) as ctx2:
                    admit_initial_quota_wait_locked(
                        root, cycle_id, clock=clock_mock
                    )
                self.assertIn("autopilot STOP sentinel exists", str(ctx2.exception))
                clock_mock.assert_not_called()

                disk_record = json.loads(wait_path.read_text(encoding="utf-8"))
                self.assertEqual(disk_record, record)

    def test_simulated_post_link_error_preserves_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            real_publisher = publish_initial_wait_record_bytes

            def failing_publisher(
                c_root: Path, run_id: str, record_bytes: bytes
            ) -> None:
                real_publisher(c_root, run_id, record_bytes)
                raise WaitStorageError("simulated post-link publisher failure")

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            p_publish = mock.patch(
                "scripts.two_site_quota_wait_admission.publish_initial_wait_record_bytes",
                side_effect=failing_publisher,
            )

            with p_intent, p_sources:
                with p_publish:
                    with self.assertRaises(WaitAdmissionError) as ctx:
                        admit_initial_quota_wait_locked(
                            root, cycle_id, clock=fake_clock
                        )
                    self.assertIn(
                        "simulated post-link publisher failure", str(ctx.exception)
                    )

                self.assertTrue(wait_path.is_file())
                persisted_bytes = wait_path.read_bytes()
                persisted_record = json.loads(persisted_bytes.decode("utf-8"))

                clock_mock = mock.Mock(side_effect=exploding_clock)
                recovered_record = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=clock_mock
                )
                clock_mock.assert_not_called()
                self.assertEqual(recovered_record, persisted_record)
                self.assertEqual(wait_path.read_bytes(), persisted_bytes)


if __name__ == "__main__":
    unittest.main()
