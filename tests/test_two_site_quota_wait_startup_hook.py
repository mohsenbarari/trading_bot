"""Hermetic tests for supervisor startup quota wait hook."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_codex_autopilot import run_loop, unique_json
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    _setup_admission_env,
    admit_initial_quota_wait_locked,
    fake_clock,
)


def _read_status(autopilot_dir: Path) -> dict:
    return unique_json((autopilot_dir / "status.json").read_text(encoding="utf-8"))


class TestTwoSiteQuotaWaitStartupHook(unittest.TestCase):
    def test_real_admitted_wait_recovers_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root, cid = env["root"], env["request"]["controller_run_id"]
            autopilot_dir = env["state_root"] / "autopilot"
            os.chmod(autopilot_dir, 0o700)
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(root, cid, clock=fake_clock)
            self.assertTrue(wait_path.is_file())
            durable_wait_bytes = wait_path.read_bytes()
            record_on_disk = json.loads(durable_wait_bytes.decode("utf-8"))

            mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
            with p_intent, p_sources, mock.patch(
                "scripts.two_site_codex_autopilot.validate_repository",
                side_effect=lambda *a, **k: copy.deepcopy(env["facts"]),
            ), mock.patch("scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle):
                ret = run_loop(
                    root, root / "fake_codex", max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
                )

            self.assertEqual(ret, 2)
            mock_run_cycle.assert_not_called()
            self.assertEqual(wait_path.read_bytes(), durable_wait_bytes)
            self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

            status = _read_status(autopilot_dir)
            self.assertEqual(status["state"], "WAITING_QUOTA")
            self.assertEqual(status["cycle"], 1)
            self.assertEqual(status["evidence"], str(env["runs_dir"]))
            self.assertEqual(status["head"], env["facts"]["head"])
            self.assertEqual(status["request_id"], env["request"]["request_id"])
            self.assertEqual(status["schedule"], record_on_disk["schedule"])
            self.assertEqual(status["summary"], "Validated terminal quota; awaiting explicit resume gate.")
            self.assertEqual(status["blockers"], [])

    def test_default_off_and_flag_false_remain_blocked(self) -> None:
        for flag in (None, False):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp_dir:
                env = _setup_admission_env(tmp_dir)
                root, cid = env["root"], env["request"]["controller_run_id"]
                autopilot_dir = env["state_root"] / "autopilot"
                os.chmod(autopilot_dir, 0o700)
                wait_path = env["runs_dir"] / "quota-wait.json"

                p_intent, p_sources = _patch_repo_boundary(env["facts"])
                with p_intent, p_sources:
                    admit_initial_quota_wait_locked(root, cid, clock=fake_clock)
                durable_bytes = wait_path.read_bytes()

                mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
                mock_recovery = mock.Mock(side_effect=AssertionError("recovery must not be called"))
                kwargs = {} if flag is None else {"quota_wait_enabled": flag}
                with p_intent, p_sources, mock.patch(
                    "scripts.two_site_codex_autopilot.validate_repository",
                    side_effect=lambda *a, **k: copy.deepcopy(env["facts"]),
                ), mock.patch(
                    "scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle
                ), mock.patch(
                    "scripts.two_site_quota_wait_recovery.recover_durable_initial_wait_locked", mock_recovery
                ):
                    ret = run_loop(root, root / "fake_codex", max_cycles=1, timeout=300, once=True, **kwargs)

                self.assertEqual(ret, 2)
                mock_run_cycle.assert_not_called()
                mock_recovery.assert_not_called()
                self.assertEqual(wait_path.read_bytes(), durable_bytes)
                self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

                status = _read_status(autopilot_dir)
                self.assertEqual(status["state"], "BLOCKED")
                self.assertEqual(status["cycle"], 1)
                self.assertTrue(any("ambiguous: rescope intent already exists" in b for b in status["blockers"]))

    def test_corrupt_wait_file_mode_remains_blocked_without_run_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root, cid = env["root"], env["request"]["controller_run_id"]
            autopilot_dir = env["state_root"] / "autopilot"
            os.chmod(autopilot_dir, 0o700)
            wait_path = env["runs_dir"] / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                admit_initial_quota_wait_locked(root, cid, clock=fake_clock)
            os.chmod(wait_path, 0o644)

            mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
            with p_intent, p_sources, mock.patch(
                "scripts.two_site_codex_autopilot.validate_repository",
                side_effect=lambda *a, **k: copy.deepcopy(env["facts"]),
            ), mock.patch("scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle):
                ret = run_loop(
                    root, root / "fake_codex", max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
                )

            self.assertEqual(ret, 2)
            mock_run_cycle.assert_not_called()
            self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

            status = _read_status(autopilot_dir)
            self.assertEqual(status["state"], "BLOCKED")
            self.assertEqual(status["cycle"], 1)
            self.assertTrue(any("mode must be 0600" in b for b in status["blockers"]))

    def test_malformed_recovery_outcomes_remain_blocked(self) -> None:
        malformed_cases = [
            (
                "request_id_none",
                lambda c: (c, {"state": "WAITING_QUOTA", "controller_run_id": c, "request_id": None, "schedule": {}}),
                "request_id must be a nonempty str",
            ),
            (
                "schedule_none",
                lambda c: (c, {"state": "WAITING_QUOTA", "controller_run_id": c, "request_id": "req-1", "schedule": None}),
                "schedule must be an exact dict",
            ),
            (
                "cid_mismatch",
                lambda c: (c, {"state": "WAITING_QUOTA", "controller_run_id": "diff-cid", "request_id": "req-1", "schedule": {}}),
                "controller_run_id mismatch",
            ),
            (
                "state_mismatch",
                lambda c: (c, {"state": "OTHER", "controller_run_id": c, "request_id": "req-1", "schedule": {}}),
                "record state must be WAITING_QUOTA",
            ),
            ("recovered_none", lambda c: None, "no durable quota wait record recovered"),
        ]
        for name, outcome_builder, expected_fragment in malformed_cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp_dir:
                env = _setup_admission_env(tmp_dir)
                root, cid = env["root"], env["request"]["controller_run_id"]
                autopilot_dir = env["state_root"] / "autopilot"
                os.chmod(autopilot_dir, 0o700)

                mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
                p_intent, p_sources = _patch_repo_boundary(env["facts"])
                with p_intent, p_sources, mock.patch(
                    "scripts.two_site_codex_autopilot.validate_repository",
                    side_effect=lambda *a, **k: copy.deepcopy(env["facts"]),
                ), mock.patch(
                    "scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle
                ), mock.patch(
                    "scripts.two_site_quota_wait_recovery.recover_durable_initial_wait_locked",
                    return_value=outcome_builder(cid),
                ):
                    ret = run_loop(
                        root, root / "fake_codex", max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
                    )

                self.assertEqual(ret, 2)
                mock_run_cycle.assert_not_called()
                self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

                status = _read_status(autopilot_dir)
                self.assertEqual(status["state"], "BLOCKED")
                self.assertEqual(status["cycle"], 1)
                self.assertNotEqual(status["state"], "WAITING_QUOTA")
                self.assertTrue(any(expected_fragment in b for b in status["blockers"]))

    def test_stop_before_startup_wins_stopped_without_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            autopilot_dir = env["state_root"] / "autopilot"
            os.chmod(autopilot_dir, 0o700)
            stop_path = autopilot_dir / "STOP"
            stop_path.write_text("STOP", encoding="utf-8")

            mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
            mock_recovery = mock.Mock(side_effect=AssertionError("recovery must not be called"))
            with mock.patch(
                "scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle
            ), mock.patch(
                "scripts.two_site_quota_wait_recovery.recover_durable_initial_wait_locked", mock_recovery
            ):
                ret = run_loop(
                    root, root / "fake_codex", max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
                )

            self.assertEqual(ret, 0)
            mock_run_cycle.assert_not_called()
            mock_recovery.assert_not_called()
            self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

            status = _read_status(autopilot_dir)
            self.assertEqual(status["state"], "STOPPED")
            self.assertEqual(status["cycle"], 1)

    def test_stop_after_recovery_wins_before_waiting_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root, cid = env["root"], env["request"]["controller_run_id"]
            autopilot_dir = env["state_root"] / "autopilot"
            os.chmod(autopilot_dir, 0o700)
            stop_path = autopilot_dir / "STOP"

            valid_record = {
                "state": "WAITING_QUOTA",
                "controller_run_id": cid,
                "request_id": env["request"]["request_id"],
                "schedule": {"next_check_at_utc": "2026-09-25T07:00:00Z"},
            }

            def repo_with_stop(*args, **kwargs):
                if stop_path.parent.exists() and not stop_path.exists():
                    if getattr(repo_with_stop, "called", False):
                        stop_path.write_text("STOP", encoding="utf-8")
                    repo_with_stop.called = True
                return copy.deepcopy(env["facts"])

            repo_with_stop.called = False

            mock_run_cycle = mock.Mock(side_effect=AssertionError("run_cycle must not be called"))
            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources, mock.patch(
                "scripts.two_site_codex_autopilot.validate_repository",
                side_effect=repo_with_stop,
            ), mock.patch(
                "scripts.two_site_codex_autopilot.run_cycle", mock_run_cycle
            ), mock.patch(
                "scripts.two_site_quota_wait_recovery.recover_durable_initial_wait_locked",
                return_value=(cid, valid_record),
            ):
                ret = run_loop(
                    root, root / "fake_codex", max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
                )

            self.assertEqual(ret, 0)
            mock_run_cycle.assert_not_called()
            self.assertFalse(any("rescope-receipt" in p.name for p in autopilot_dir.rglob("*.json")))

            status = _read_status(autopilot_dir)
            self.assertEqual(status["state"], "STOPPED")
            self.assertEqual(status["cycle"], 1)


if __name__ == "__main__":
    unittest.main()
