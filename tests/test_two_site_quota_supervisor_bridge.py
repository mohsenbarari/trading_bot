#!/usr/bin/env python3
"""Hermetic unit tests for two-site quota supervisor bridge."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from scripts.two_site_quota_supervisor_bridge import (
    QuotaSupervisorBridgeError,
    accept_cycle_quota_disposition_locked,
)
from tests.test_two_site_quota_wait_admission import (
    _patch_repo_boundary,
    _setup_admission_env,
)


def _valid_result(head: str, **overrides: Any) -> dict[str, Any]:
    res: dict[str, Any] = {
        "state": "BLOCKED",
        "head_changed": False,
        "commit": None,
        "head_before": head,
        "head_after": head,
        "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None,
        "proposal_sha256": None,
    }
    res.update(overrides)
    return res


class TestTwoSiteQuotaSupervisorBridge(unittest.TestCase):
    def test_default_off_zero_io_and_non_bool_enabled(self) -> None:
        self.assertIsNone(accept_cycle_quota_disposition_locked(None, None, None))

        with mock.patch(
            "scripts.two_site_quota_supervisor_bridge.canonical_root"
        ) as p_root, mock.patch(
            "scripts.two_site_quota_supervisor_bridge.probe_cycle_quota_receipt"
        ) as p_probe, mock.patch(
            "scripts.two_site_quota_supervisor_bridge.admit_initial_quota_wait_locked"
        ) as p_admit:
            res = accept_cycle_quota_disposition_locked(
                None, None, None, enabled=False
            )
            self.assertIsNone(res)
            p_root.assert_not_called()
            p_probe.assert_not_called()
            p_admit.assert_not_called()

        for invalid_enabled in (1, "true", None, 0, 1.0, [], {}):
            with self.assertRaises(QuotaSupervisorBridgeError):
                accept_cycle_quota_disposition_locked(
                    None, None, None, enabled=invalid_enabled
                )

    def test_real_positive_and_idempotent_wait_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            run_dir = env["runs_dir"]
            result = _valid_result(env["facts"]["head"])
            wait_path = run_dir / "quota-wait.json"

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                rec1 = accept_cycle_quota_disposition_locked(
                    root, run_dir, result, enabled=True
                )
                self.assertIsInstance(rec1, dict)
                self.assertEqual(rec1.get("state"), "WAITING_QUOTA")
                self.assertTrue(wait_path.is_file())
                raw1 = wait_path.read_bytes()

                rec2 = accept_cycle_quota_disposition_locked(
                    root, run_dir, result, enabled=True
                )
                self.assertEqual(rec2, rec1)
                self.assertEqual(wait_path.read_bytes(), raw1)

    def test_valid_accepted_continue_never_probes_quota_state(self) -> None:
        result = {
            "state": "CONTINUE",
            "head_changed": True,
            "head_before": "1" * 40,
            "head_after": "2" * 40,
            "commit": "2" * 40,
        }
        with mock.patch(
            "scripts.two_site_quota_supervisor_bridge.canonical_root"
        ) as p_root, mock.patch(
            "scripts.two_site_quota_supervisor_bridge.probe_cycle_quota_receipt"
        ) as p_probe:
            self.assertIsNone(accept_cycle_quota_disposition_locked(
                None, None, result, enabled=True
            ))
            p_root.assert_not_called()
            p_probe.assert_not_called()

        for invalid in (
            dict(result, head_changed=False),
            dict(result, head_after=result["head_before"]),
            dict(result, commit="3" * 40),
        ):
            with self.assertRaises(QuotaSupervisorBridgeError):
                accept_cycle_quota_disposition_locked(None, None, invalid, enabled=True)

    def test_exact_single_target_deletions_no_reservation_and_no_terminal_quota(self) -> None:
        # Scenario 1: NO_RESERVATION deletes only broker-reservation.json
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            run_dir = env["runs_dir"]
            result = _valid_result(env["facts"]["head"])
            res_file = env["runs_dir"] / "broker-reservation.json"
            tq_file = (
                root
                / ".local"
                / "two-site-refactor"
                / "provider-broker"
                / "terminal-quota"
                / f"{env['request']['request_id']}.json"
            )
            self.assertTrue(res_file.is_file())
            self.assertTrue(tq_file.is_file())

            res_file.unlink()
            self.assertFalse(res_file.exists())
            self.assertTrue(tq_file.is_file())

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                res = accept_cycle_quota_disposition_locked(
                    root, run_dir, result, enabled=True
                )
            self.assertIsNone(res)
            self.assertFalse((run_dir / "quota-wait.json").exists())

        # Scenario 2: NO_TERMINAL_QUOTA deletes only terminal quota receipt file
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            run_dir = env["runs_dir"]
            result = _valid_result(env["facts"]["head"])
            res_file = env["runs_dir"] / "broker-reservation.json"
            tq_file = (
                root
                / ".local"
                / "two-site-refactor"
                / "provider-broker"
                / "terminal-quota"
                / f"{env['request']['request_id']}.json"
            )
            self.assertTrue(res_file.is_file())
            self.assertTrue(tq_file.is_file())

            tq_file.unlink()
            self.assertFalse(tq_file.exists())
            self.assertTrue(res_file.is_file())

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                res = accept_cycle_quota_disposition_locked(
                    root, run_dir, result, enabled=True
                )
            self.assertIsNone(res)
            self.assertFalse((run_dir / "quota-wait.json").exists())

    def test_contradictory_results_fail_closed_before_wait_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            run_dir = env["runs_dir"]
            head = env["facts"]["head"]
            wait_path = run_dir / "quota-wait.json"

            contradictory_cases = [
                {"state": "COMPLETE"},
                {"head_changed": True},
                {"commit": "0123456789abcdef0123456789abcdef01234567"},
                {"head_after": "0" * 40},
                {"gemini_worker_run_id": "worker-1"},
                {"gemini_reviewer_run_id": "rev-1"},
                {"proposal_sha256": "f" * 64},
            ]

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                for patch in contradictory_cases:
                    with self.assertRaises(QuotaSupervisorBridgeError):
                        accept_cycle_quota_disposition_locked(
                            root, run_dir, _valid_result(head, **patch), enabled=True
                        )
                    self.assertFalse(wait_path.exists())

    def test_invalid_inputs_and_path_safety_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env = _setup_admission_env(tmp_dir)
            root = env["root"]
            run_dir = env["runs_dir"]
            result = _valid_result(env["facts"]["head"])

            p_intent, p_sources = _patch_repo_boundary(env["facts"])
            with p_intent, p_sources:
                for bad_root, bad_run, bad_res in [
                    ("not-a-path", run_dir, result),
                    (root, "not-a-path", result),
                    (root, run_dir, "not-a-dict"),
                ]:
                    with self.assertRaises(QuotaSupervisorBridgeError):
                        accept_cycle_quota_disposition_locked(
                            bad_root, bad_run, bad_res, enabled=True
                        )

                symlink_root = Path(tmp_dir) / "symlink_root"
                symlink_root.symlink_to(root)
                with self.assertRaises(QuotaSupervisorBridgeError):
                    accept_cycle_quota_disposition_locked(
                        symlink_root, run_dir, result, enabled=True
                    )

                sibling = run_dir.parent / "invalid-sibling"
                with self.assertRaises(QuotaSupervisorBridgeError):
                    accept_cycle_quota_disposition_locked(
                        root, sibling, result, enabled=True
                    )

                symlink_run = run_dir.parent / "symlink-run"
                symlink_run.symlink_to(run_dir)
                try:
                    with self.assertRaises(QuotaSupervisorBridgeError):
                        accept_cycle_quota_disposition_locked(
                            root, symlink_run, result, enabled=True
                        )
                finally:
                    symlink_run.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
