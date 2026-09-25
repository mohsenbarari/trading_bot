"""Hermetic tests for autopilot supervisor quota wait hook wiring."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.two_site_codex_autopilot import AutopilotError, run_loop, unique_json
from scripts.two_site_quota_supervisor_bridge import QuotaSupervisorBridgeError
from tests.test_two_site_ae03_runtime_wire import HEAD, _repo_facts, _rescope_result

FAKE_WAIT_RECORD = {
    "state": "WAITING_QUOTA",
    "request_id": "req-1",
    "schedule": {"next_check_at_utc": "2026-09-25T07:00:00Z"},
}


class TestTwoSiteQuotaSupervisorHook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        os.chmod(self.root, 0o700)
        self.codex = self.root / "fake_codex"
        self.state_root = self.root / ".local" / "two-site-refactor" / "autopilot"
        self.state_root.mkdir(parents=True, mode=0o700)
        os.chmod(self.state_root, 0o700)
        self.repo = _repo_facts()

    def tearDown(self):
        self.temp_dir.cleanup()

    @contextmanager
    def _apply_patches(self, bridge_return=None, bridge_side_effect=None):
        def fake_run_cycle(
            root, codex, state_root, cycle, timeout, before_model=None, **kwargs
        ):
            if before_model is not None:
                before_model(self.repo)
            run_dir = state_root / "runs" / f"cycle-{cycle:04d}"
            run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(run_dir, 0o700)
            return run_dir, _rescope_result(attempt=cycle)

        with patch(
            "scripts.two_site_codex_autopilot.validate_repository",
            side_effect=lambda r, require_clean=True: self.repo,
        ), patch(
            "scripts.two_site_codex_autopilot.git",
            side_effect=lambda r, *a: HEAD if "rev-parse" in a else ("" if "status" in a else "2" * 40),
        ), patch(
            "scripts.two_site_codex_autopilot.ref_snapshot",
            return_value={"refs/heads/main": HEAD},
        ), patch(
            "scripts.two_site_codex_autopilot.prune_runs",
            return_value=None,
        ) as mock_prune, patch(
            "scripts.two_site_codex_autopilot.run_cycle",
            side_effect=fake_run_cycle,
        ) as mock_run_cycle, patch(
            "scripts.two_site_quota_supervisor_bridge.accept_cycle_quota_disposition_locked",
            return_value=bridge_return,
            side_effect=bridge_side_effect,
        ) as mock_bridge:
            yield mock_prune, mock_run_cycle, mock_bridge

    def _read_status(self) -> dict:
        return unique_json((self.state_root / "status.json").read_text(encoding="utf-8"))

    def test_scenario1_default_disabled_does_not_call_bridge(self):
        with self._apply_patches() as (mock_prune, mock_run_cycle, mock_bridge):
            ret = run_loop(self.root, self.codex, max_cycles=1, timeout=300, once=True)
        self.assertEqual(ret, 2)
        mock_bridge.assert_not_called()
        self.assertEqual(mock_run_cycle.call_count, 1)
        mock_prune.assert_called_once_with(self.state_root)
        status = self._read_status()
        self.assertEqual(status["state"], "RESCOPE")
        self.assertEqual(status["cycle"], 1)

    def test_scenario2_opt_in_admitted_quota_wait_exits_without_prune(self):
        with self._apply_patches(bridge_return=dict(FAKE_WAIT_RECORD)) as (
            mock_prune,
            mock_run_cycle,
            mock_bridge,
        ):
            ret = run_loop(
                self.root, self.codex, max_cycles=5, timeout=300, once=True, quota_wait_enabled=True
            )
        self.assertEqual(ret, 2)
        mock_bridge.assert_called_once_with(
            self.root,
            self.state_root / "runs" / "cycle-0001",
            _rescope_result(attempt=1),
            enabled=True,
        )
        self.assertEqual(mock_run_cycle.call_count, 1)
        mock_prune.assert_not_called()
        self.assertEqual(list(self.state_root.rglob("*receipt*.json")), [])
        status = self._read_status()
        self.assertEqual(status["state"], "WAITING_QUOTA")
        self.assertEqual(status["cycle"], 1)
        self.assertEqual(status["request_id"], "req-1")
        self.assertEqual(status["schedule"], {"next_check_at_utc": "2026-09-25T07:00:00Z"})
        self.assertEqual(status["head"], HEAD)
        self.assertEqual(status["summary"], "Validated terminal quota; awaiting explicit resume gate.")
        self.assertEqual(status["blockers"], [])

    def test_scenario3_opt_in_bridge_none_proceeds_to_rescope(self):
        with self._apply_patches(bridge_return=None) as (
            mock_prune,
            mock_run_cycle,
            mock_bridge,
        ):
            ret = run_loop(
                self.root, self.codex, max_cycles=1, timeout=300, once=True, quota_wait_enabled=True
            )
        self.assertEqual(ret, 2)
        mock_bridge.assert_called_once()
        self.assertEqual(mock_run_cycle.call_count, 1)
        mock_prune.assert_called_once_with(self.state_root)
        status = self._read_status()
        self.assertEqual(status["state"], "RESCOPE")
        self.assertEqual(status["cycle"], 1)

    def _assert_scenario4_blocked(self, ret_val=None, side_eff=None, expected_blocker=""):
        with self._apply_patches(bridge_return=ret_val, bridge_side_effect=side_eff) as (
            mock_prune,
            mock_run_cycle,
            mock_bridge,
        ):
            ret = run_loop(
                self.root, self.codex, max_cycles=3, timeout=300, once=True, quota_wait_enabled=True
            )
        self.assertEqual(ret, 2)
        mock_bridge.assert_called_once()
        self.assertEqual(mock_run_cycle.call_count, 1)
        mock_prune.assert_not_called()
        self.assertEqual(list(self.state_root.rglob("*receipt*.json")), [])
        status = self._read_status()
        self.assertEqual(status["state"], "BLOCKED")
        self.assertEqual(status["cycle"], 1)
        self.assertTrue(any(expected_blocker in b for b in status.get("blockers", [])))

    def test_scenario4a_unexpected_dict_state(self):
        self._assert_scenario4_blocked(
            ret_val={"state": "INVALID_STATE", "request_id": "req-1"},
            expected_blocker="invalid quota wait admission record",
        )

    def test_scenario4b_non_dict_outcome(self):
        self._assert_scenario4_blocked(
            ret_val="not_a_dict",
            expected_blocker="invalid quota wait admission record",
        )

    def test_scenario4c_bridge_exception(self):
        self._assert_scenario4_blocked(
            side_eff=QuotaSupervisorBridgeError("bridge exploded"),
            expected_blocker="bridge exploded",
        )

    def test_scenario5_non_bool_quota_wait_enabled_raises_before_io(self):
        for val in [None, 1, 0, "True", "False", [], {}]:
            with self.subTest(val=val):
                with self.assertRaises(AutopilotError) as ctx:
                    run_loop(
                        Path("/nonexistent/unopened/path"),
                        self.codex,
                        max_cycles=1,
                        timeout=300,
                        once=True,
                        quota_wait_enabled=val,
                    )
                self.assertIn("quota_wait_enabled must be an exact bool", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
