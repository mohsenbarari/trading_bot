from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _fi_host_loss_state,
    _run_fi_host_loss,
    recover_active_faults,
)


class _Args:
    campaign_id = "12345678-1234-4234-9234-123456789abc"
    gate_group_id = "32345678-1234-4234-9234-123456789abc"
    release_sha = "a" * 40
    operation_id = "22345678-1234-4234-9234-123456789abc"


class FullMatrixDestructiveFiHostLossTests(unittest.TestCase):
    def _state(self, phase: str = "fi_powered_off") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "destructive_fi_host_loss",
            "operation_id": _Args.operation_id,
            "scenario_id": "fi_host_loss_without_national_cutoff",
            "phase": phase,
            "writer_epoch": 9,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    @staticmethod
    def _pair() -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {
                "active_site": "webapp_fi", "writer_epoch": 9,
                "control_state": "active", "local_active_with_witness_lease": True,
            },
            "webapp_ir": {
                "active_site": "webapp_fi", "writer_epoch": 9,
                "control_state": "active", "local_active_with_witness_lease": False,
            },
        }

    def test_state_and_recovery_are_exactly_scoped(self):
        self.assertEqual(_fi_host_loss_state(self._state())["writer_epoch"], 9)
        invalid = self._state("unsafe")
        with self.assertRaisesRegex(Exception, "destructive FI host-loss state"):
            _fi_host_loss_state(invalid)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_path = root / "active-faults.json"
            state_path.write_text(__import__("json").dumps(self._state()) + "\n", encoding="utf-8")
            state_path.chmod(0o600)
            plan = {
                "_state_root": root,
                "campaign_id": _Args.campaign_id,
                "gate_group_id": _Args.gate_group_id,
                "release_sha": _Args.release_sha,
            }
            with patch("scripts.full_matrix_live.scenario_handlers._cleanup_fi_host_loss") as cleanup:
                result = recover_active_faults(plan)
        self.assertEqual(result["recovered_kinds"], ["destructive_fi_host_loss"])
        cleanup.assert_called_once()

    def test_outage_reads_only_ir_standby_before_fi_recovery(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root}
            power = [
                {"action": "power-off", "role": "webapp_fi"},
                {"action": "power-on", "role": "webapp_fi"},
            ]
            ir = {
                "active_site": "webapp_fi", "writer_epoch": 9,
                "control_state": "active", "local_active_with_witness_lease": False,
                "local_active_reasons": ["writer_active_site_mismatch"],
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_pair_observation",
                side_effect=[self._pair(), self._pair()],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._writer_lease_observation",
                return_value=ir,
            ) as ir_observation, patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=[{"origin": "fi"}, {"origin": "fi"}],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._destructive_power",
                side_effect=power,
            ) as destructive:
                outcome, observations = _run_fi_host_loss(_Args(), plan)
        self.assertTrue(outcome["fi_vm_loss_did_not_promote_ir_without_national_cutoff"])
        self.assertEqual(ir_observation.call_args.args[1], "webapp_ir")
        self.assertEqual([call.kwargs["action"] for call in destructive.call_args_list], ["power-off", "power-on"])
        self.assertEqual(observations["ir_safe_unavailable"]["active_site"], "webapp_fi")
        self.assertFalse((root / "active-faults.json").exists())


if __name__ == "__main__":
    unittest.main()
