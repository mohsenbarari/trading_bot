from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _power_loss_cutpoint_state,
    _run_power_loss_cutpoint,
    recover_active_faults,
)


class _Args:
    campaign_id = "12345678-1234-4234-9234-123456789abc"
    gate_group_id = "32345678-1234-4234-9234-123456789abc"
    release_sha = "a" * 40
    operation_id = "22345678-1234-4234-9234-123456789abc"
    iteration = 1


class FullMatrixDestructivePowerLossCutpointTests(unittest.TestCase):
    @staticmethod
    def _fi_pair() -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {"active_site": "webapp_fi", "writer_epoch": 8, "control_state": "active", "local_active_with_witness_lease": True},
            "webapp_ir": {"active_site": "webapp_fi", "writer_epoch": 8, "control_state": "active", "local_active_with_witness_lease": False},
        }

    @staticmethod
    def _ir_pair() -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {"active_site": "webapp_ir", "writer_epoch": 9, "control_state": "active", "local_active_with_witness_lease": False},
            "webapp_ir": {"active_site": "webapp_ir", "writer_epoch": 9, "control_state": "active", "local_active_with_witness_lease": True},
        }

    def _state(self, phase: str = "fi_powered_off") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "destructive_power_loss_cutpoint",
            "operation_id": _Args.operation_id,
            "scenario_id": "power_loss_between_fence_and_enable",
            "phase": phase,
            "writer_epoch_before": 8,
            "writer_epoch_after": 9,
            "transition_operation_id": "32345678-1234-4234-9234-123456789abc" if phase != "prepared" else None,
            "plan_hash": "b" * 64 if phase != "prepared" else None,
            "iteration": 1,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    @staticmethod
    def _transition(status: str) -> dict[str, object]:
        return {
            "status": status,
            "operation_id": "32345678-1234-4234-9234-123456789abc",
            "plan_hash": "b" * 64,
            "source_site": "webapp_fi",
            "target_site": "webapp_ir",
            "writer_epoch_before": 8,
            "writer_epoch_after": 9,
            "connectivity_mode": "isolated",
            "connectivity_consecutive_rounds": 3,
            **({"paused_after_step": "source_connections_drained"} if status == "paused" else {}),
        }

    def test_state_is_closed_and_recovery_dispatches_exact_cutpoint_cleanup(self):
        self.assertEqual(_power_loss_cutpoint_state(self._state())["writer_epoch_after"], 9)
        with self.assertRaisesRegex(Exception, "power-loss cutpoint state"):
            _power_loss_cutpoint_state(self._state("bogus"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "active-faults.json").write_text(json.dumps(self._state()) + "\n", encoding="utf-8")
            (root / "active-faults.json").chmod(0o600)
            plan = {"_state_root": root, "campaign_id": _Args.campaign_id, "gate_group_id": _Args.gate_group_id, "release_sha": _Args.release_sha}
            with patch("scripts.full_matrix_live.scenario_handlers._cleanup_power_loss_cutpoint") as cleanup:
                result = recover_active_faults(plan)
        self.assertEqual(result["recovered_kinds"], ["destructive_power_loss_cutpoint"])
        self.assertEqual(cleanup.call_args.args[0].iteration, 1)

    def test_fi_powers_off_only_after_durable_cutpoint_then_same_jit_plan_resumes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {
                "_state_root": root,
                "campaign_id": _Args.campaign_id,
                "release_sha": _Args.release_sha,
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_pair_observation",
                return_value=self._fi_pair(),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=[{"origin": "fi"}, {"origin": "ir"}, {"origin": "ir"}],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.execute_transition",
                side_effect=[self._transition("paused"), self._transition("completed")],
            ) as transition, patch(
                "scripts.full_matrix_live.scenario_handlers._destructive_power",
                side_effect=[{"action": "power-off"}, {"action": "power-on"}],
            ) as power, patch(
                "scripts.full_matrix_live.scenario_handlers._wait_for_ir_writer_recovery",
                return_value=self._ir_pair(),
            ):
                outcome, observations = _run_power_loss_cutpoint(_Args(), plan)
                self.assertTrue((root / "writer-lifecycle.json").is_file())
                self.assertFalse((root / "active-faults.json").exists())
        self.assertTrue(outcome["same_jit_plan_resumed_through_ir_pull_only_target_enable"])
        self.assertTrue(transition.call_args_list[0].kwargs["pause_after_source_drain_for_power_loss"])
        self.assertFalse(transition.call_args_list[1].kwargs.get("pause_after_source_drain_for_power_loss", False))
        self.assertEqual([item.kwargs["action"] for item in power.call_args_list], ["power-off", "power-on"])
        self.assertEqual(observations["paused_transition"]["paused_after_step"], "source_connections_drained")
