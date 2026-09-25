#!/usr/bin/env python3
"""Hermetic unit tests for durable initial quota wait recovery."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_quota_wait_admission import admit_initial_quota_wait_locked
from scripts.two_site_quota_wait_recovery import (
    WaitRecoveryError,
    recover_durable_initial_wait_locked,
)
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    _setup_admission_env,
    fake_clock,
)


class TestTwoSiteQuotaWaitRecovery(unittest.TestCase):
    def test_positive_admission_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admitted = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                recovered = recover_durable_initial_wait_locked(root)

            self.assertIsNotNone(recovered)
            recovered_cid, recovered_record = recovered
            self.assertEqual(recovered_cid, cycle_id)
            self.assertEqual(recovered_record, admitted)

    def test_legacy_0755_no_wait_run_directory_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"

            legacy_run_id = "cycle-20260901-20260901T000000Z-1"
            legacy_dir = runs_parent / legacy_run_id
            legacy_dir.mkdir(mode=0o755)
            legacy_dir.chmod(0o755)

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admitted = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                recovered = recover_durable_initial_wait_locked(root)

            self.assertIsNotNone(recovered)
            recovered_cid, recovered_record = recovered
            self.assertEqual(recovered_cid, cycle_id)
            self.assertEqual(recovered_record, admitted)

    def test_0755_run_with_wait_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)
                env["runs_dir"].chmod(0o755)
                with self.assertRaises(WaitRecoveryError):
                    recover_durable_initial_wait_locked(root)

    def test_before_wait_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                result = recover_durable_initial_wait_locked(root)

            self.assertIsNone(result)

    def test_stop_sentinel_fails_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                stop_path.write_text("STOP", encoding="utf-8")
                with self.assertRaises(WaitRecoveryError) as ctx1:
                    recover_durable_initial_wait_locked(root)
                self.assertIn("autopilot STOP sentinel exists", str(ctx1.exception))

                stop_path.unlink()
                stop_target = root / "nonexistent-stop-target"
                stop_path.symlink_to(stop_target)
                self.assertTrue(stop_path.is_symlink())

                with self.assertRaises(WaitRecoveryError) as ctx2:
                    recover_durable_initial_wait_locked(root)
                self.assertIn("autopilot STOP sentinel exists", str(ctx2.exception))

    def test_corrupt_0644_wait_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)
                wait_path.chmod(0o644)
                with self.assertRaises(WaitRecoveryError):
                    recover_durable_initial_wait_locked(root)

    def test_valid_numeric_second_cycle_with_wait_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]
            wait_path = env["runs_dir"] / "quota-wait.json"
            runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(root, cycle_id, clock=fake_clock)

                second_cycle_id = "cycle-20260925-20260925T080000Z-2"
                second_dir = runs_parent / second_cycle_id
                second_dir.mkdir(mode=0o700)
                second_dir.chmod(0o700)
                second_wait = second_dir / "quota-wait.json"
                second_wait.write_bytes(wait_path.read_bytes())
                second_wait.chmod(0o600)

                with self.assertRaises(WaitRecoveryError) as ctx:
                    recover_durable_initial_wait_locked(root)
                self.assertIn("multiple wait candidates", str(ctx.exception).lower())

    def test_symlink_alias_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            alias_root = Path(tmp_dir) / "alias_root"
            alias_root.symlink_to(root)

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                with self.assertRaises(WaitRecoveryError):
                    recover_durable_initial_wait_locked(alias_root)

    def test_missing_runs_dir_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
            shutil.rmtree(runs_parent)

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                with self.assertRaises(WaitRecoveryError):
                    recover_durable_initial_wait_locked(root)

    def test_os_listdir_patched_to_raise_while_positive_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            cycle_id = env["request"]["controller_run_id"]

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admitted = admit_initial_quota_wait_locked(
                    root, cycle_id, clock=fake_clock
                )
                with mock.patch("os.listdir", side_effect=AssertionError("os.listdir must not be called")):
                    recovered = recover_durable_initial_wait_locked(root)

            self.assertIsNotNone(recovered)
            recovered_cid, recovered_record = recovered
            self.assertEqual(recovered_cid, cycle_id)
            self.assertEqual(recovered_record, admitted)

    def test_unexpected_entry_name_in_runs_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            runs_parent = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
            unexpected_file = runs_parent / "unexpected_file.txt"
            unexpected_file.write_text("hello", encoding="utf-8")

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                with self.assertRaises(WaitRecoveryError) as ctx:
                    recover_durable_initial_wait_locked(root)
                self.assertIn("unexpected entry in runs directory", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
