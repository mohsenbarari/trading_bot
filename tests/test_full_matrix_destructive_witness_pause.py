from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _run_witness_pause,
    _witness_pause_state,
    recover_active_faults,
)


class _Args:
    campaign_id = "12345678-1234-4234-9234-123456789abc"
    gate_group_id = "32345678-1234-4234-9234-123456789abc"
    release_sha = "a" * 40
    operation_id = "22345678-1234-4234-9234-123456789abc"


class FullMatrixDestructiveWitnessPauseTests(unittest.TestCase):
    def _state(self, phase: str = "witness_powered_off") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "destructive_witness_vm_pause",
            "operation_id": _Args.operation_id,
            "scenario_id": "witness_partition_and_vm_pause",
            "phase": phase,
            "writer_epoch": 9,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    @staticmethod
    def _pair(*, active: bool) -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {
                "active_site": "webapp_fi",
                "writer_epoch": 9,
                "control_state": "active",
                "local_active_with_witness_lease": active,
            },
            "webapp_ir": {
                "active_site": "webapp_fi",
                "writer_epoch": 9,
                "control_state": "active",
                "local_active_with_witness_lease": False,
            },
        }

    def test_state_is_closed_and_recovery_uses_only_its_exact_cleanup(self):
        self.assertEqual(_witness_pause_state(self._state())["writer_epoch"], 9)
        invalid = self._state("unsafe")
        with self.assertRaisesRegex(Exception, "destructive witness pause state"):
            _witness_pause_state(invalid)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "active-faults.json"
            path.write_text(__import__("json").dumps(self._state()) + "\n", encoding="utf-8")
            path.chmod(0o600)
            plan = {
                "_state_root": root,
                "campaign_id": _Args.campaign_id,
                "gate_group_id": _Args.gate_group_id,
                "release_sha": _Args.release_sha,
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_witness_pause"
            ) as cleanup:
                result = recover_active_faults(plan)
        self.assertEqual(result["recovered_kinds"], ["destructive_witness_vm_pause"])
        cleanup.assert_called_once()

    def test_cycle_powers_off_fences_then_powers_on_before_state_removal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root}
            power = [
                {
                    "schema": "three-site-full-matrix-arvan-destructive-power-v1",
                    "status": "passed",
                    "intent_sha256": "a" * 64,
                    "role": "witness",
                    "action": "power-off",
                    "before_status": "ACTIVE",
                    "after_status": "SHUTOFF",
                    "audit_event_hash": "b" * 64,
                },
                {
                    "schema": "three-site-full-matrix-arvan-destructive-power-v1",
                    "status": "passed",
                    "intent_sha256": "c" * 64,
                    "role": "witness",
                    "action": "power-on",
                    "before_status": "SHUTOFF",
                    "after_status": "ACTIVE",
                    "audit_event_hash": "d" * 64,
                },
            ]
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_pair_observation",
                side_effect=[self._pair(active=True), self._pair(active=False), self._pair(active=True)],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=[{"origin": "fi"}, {"origin": "fi"}],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._destructive_power",
                side_effect=power,
            ) as destructive:
                outcome, observations = _run_witness_pause(_Args(), plan)
        self.assertTrue(outcome["webapp_ir_never_promoted_without_national_cutoff"])
        self.assertEqual([item.kwargs["action"] for item in destructive.call_args_list], ["power-off", "power-on"])
        self.assertEqual(observations["both_sites_fenced"]["webapp_fi"]["local_active_with_witness_lease"], False)
        self.assertFalse((root / "active-faults.json").exists())


if __name__ == "__main__":
    unittest.main()
