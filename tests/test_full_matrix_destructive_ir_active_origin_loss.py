from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _ir_active_origin_loss_state,
    _run_ir_active_origin_loss,
    recover_active_faults,
)


class _Args:
    campaign_id = "12345678-1234-4234-9234-123456789abc"
    gate_group_id = "32345678-1234-4234-9234-123456789abc"
    release_sha = "a" * 40
    operation_id = "22345678-1234-4234-9234-123456789abc"
    iteration = 1


class FullMatrixDestructiveIrActiveOriginLossTests(unittest.TestCase):
    def _state(self, phase: str = "ir_powered_off") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "destructive_ir_active_origin_loss",
            "operation_id": _Args.operation_id,
            "scenario_id": "ir_only_active_origin_loss_is_safe_unavailable",
            "phase": phase,
            "writer_epoch": 10,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    @staticmethod
    def _ir_pair() -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {
                "active_site": "webapp_ir", "writer_epoch": 10,
                "control_state": "active", "local_active_with_witness_lease": False,
            },
            "webapp_ir": {
                "active_site": "webapp_ir", "writer_epoch": 10,
                "control_state": "active", "local_active_with_witness_lease": True,
            },
        }

    @staticmethod
    def _lifecycle() -> dict[str, object]:
        return {"iteration": 1, "writer_epoch_after": 10}

    def test_state_is_closed_and_recovery_uses_only_ir_power_recovery(self):
        self.assertEqual(_ir_active_origin_loss_state(self._state())["writer_epoch"], 10)
        with self.assertRaisesRegex(Exception, "active-origin-loss state"):
            _ir_active_origin_loss_state(self._state("unsafe"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "active-faults.json"
            path.write_text(json.dumps(self._state()) + "\n", encoding="utf-8")
            path.chmod(0o600)
            plan = {
                "_state_root": root,
                "campaign_id": _Args.campaign_id,
                "gate_group_id": _Args.gate_group_id,
                "release_sha": _Args.release_sha,
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_ir_active_origin_loss"
            ) as cleanup:
                result = recover_active_faults(plan)
        self.assertEqual(result["recovered_kinds"], ["destructive_ir_active_origin_loss"])
        cleanup.assert_called_once()

    def test_ir_loss_observes_only_fi_and_public_safe_unavailable_before_ir_recovery(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root}
            fi_safe = {
                "active_site": "webapp_ir", "writer_epoch": 10,
                "control_state": "active", "local_active_with_witness_lease": False,
                "local_active_reasons": ["writer_active_site_mismatch"],
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_lifecycle_state",
                return_value=self._lifecycle(),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._writer_pair_observation",
                side_effect=[self._ir_pair(), self._ir_pair()],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._writer_lease_observation",
                return_value=fi_safe,
            ) as fi_observation, patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=[{"origin": "ir"}, {"origin": "ir"}],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_safe_unavailable",
                return_value={"status": "safe_unavailable"},
            ) as unavailable, patch(
                "scripts.full_matrix_live.scenario_handlers._destructive_power",
                side_effect=[
                    {"action": "power-off", "role": "webapp_ir"},
                    {"action": "power-on", "role": "webapp_ir"},
                ],
            ) as destructive:
                outcome, observations = _run_ir_active_origin_loss(_Args(), plan)
        self.assertTrue(outcome["ir_active_origin_loss_never_promoted_fi_without_failback"])
        self.assertEqual(fi_observation.call_args.args[1], "webapp_fi")
        self.assertEqual([item.kwargs["action"] for item in destructive.call_args_list], ["power-off", "power-on"])
        unavailable.assert_called_once_with(plan)
        self.assertEqual(observations["fi_safe_unavailable"]["active_site"], "webapp_ir")
        self.assertFalse((root / "active-faults.json").exists())


if __name__ == "__main__":
    unittest.main()
