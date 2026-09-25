#!/usr/bin/env python3
"""Hermetic fault-injection tests for initial quota wait admission."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)
import scripts.two_site_quota_wait_sources as wait_sources
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    _setup_admission_env,
    exploding_clock,
    fake_clock,
)


class TestTwoSiteQuotaWaitAdmissionFaults(unittest.TestCase):
    def test_chmod_wait_record_mode_fails_admission_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                self.assertTrue(wait_path.is_file())
                initial_bytes = wait_path.read_bytes()

                os.chmod(wait_path, 0o644)
                self.assertEqual(stat.S_IMODE(wait_path.stat().st_mode), 0o644)

                clock_mock = mock.Mock(side_effect=exploding_clock)
                with self.assertRaises(WaitAdmissionError):
                    admit_initial_quota_wait_locked(
                        root, cycle_id, clock=clock_mock
                    )
                clock_mock.assert_not_called()

                self.assertEqual(wait_path.read_bytes(), initial_bytes)
                self.assertEqual(stat.S_IMODE(wait_path.stat().st_mode), 0o644)

    def test_corrupt_existing_state_bytes_fails_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                self.assertTrue(wait_path.is_file())
                original_bytes = wait_path.read_bytes()

                target_sub = b'"state":"WAITING_QUOTA"'
                corrupt_sub = b'"state":"CORRUPT_STATE"'
                self.assertEqual(len(target_sub), len(corrupt_sub))
                self.assertIn(target_sub, original_bytes)

                corrupted_bytes = original_bytes.replace(target_sub, corrupt_sub, 1)
                self.assertEqual(len(corrupted_bytes), len(original_bytes))
                wait_path.write_bytes(corrupted_bytes)

                clock_mock = mock.Mock(side_effect=exploding_clock)
                with self.assertRaises(WaitAdmissionError):
                    admit_initial_quota_wait_locked(
                        root, cycle_id, clock=clock_mock
                    )
                clock_mock.assert_not_called()

                self.assertEqual(wait_path.read_bytes(), corrupted_bytes)

    def test_witness_drift_between_collections_blocks_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            if "status_path" not in env:
                status_matches = sorted(
                    (root / ".local" / "two-site-refactor" / "proposals").glob(
                        "*-worker-0/status.json"
                    )
                )
                self.assertTrue(status_matches, "status witness file must exist")
                env["status_path"] = status_matches[0]

            real_collector = wait_sources.collect_initial_wait_source_expected
            wrapper_count = 0

            def collector_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
                nonlocal wrapper_count
                wrapper_count += 1
                result = real_collector(*args, **kwargs)
                if wrapper_count == 1:
                    env["status_path"].write_text(
                        '{"corrupted": true}', encoding="utf-8"
                    )
                return result

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            p_wrap_admission = mock.patch(
                "scripts.two_site_quota_wait_admission.collect_initial_wait_source_expected",
                side_effect=collector_wrapper,
            )
            p_wrap_sources = mock.patch(
                "scripts.two_site_quota_wait_sources.collect_initial_wait_source_expected",
                side_effect=collector_wrapper,
            )

            with p_intent, p_sources, p_wrap_admission, p_wrap_sources:
                with self.assertRaises(WaitAdmissionError):
                    admit_initial_quota_wait_locked(
                        root, cycle_id, clock=fake_clock
                    )

            self.assertEqual(wrapper_count, 2)
            self.assertFalse(wait_path.exists())

    def test_stop_injected_after_proofs_before_publication_blocks_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"
            stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"

            def clock_writing_stop() -> datetime:
                stop_path.write_text("STOP", encoding="utf-8")
                return fake_clock()

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                with self.assertRaises(WaitAdmissionError) as ctx:
                    admit_initial_quota_wait_locked(
                        root, cycle_id, clock=clock_writing_stop
                    )
                self.assertIn("autopilot STOP sentinel exists", str(ctx.exception))

            self.assertTrue(stop_path.is_file())
            self.assertFalse(wait_path.exists())


if __name__ == "__main__":
    unittest.main()
