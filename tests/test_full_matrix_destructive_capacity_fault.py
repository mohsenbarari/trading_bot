from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _capacity_fault_state,
    _run_capacity_fault,
    recover_active_faults,
)


class _Args:
    campaign_id = "12345678-1234-4234-9234-123456789abc"
    gate_group_id = "32345678-1234-4234-9234-123456789abc"
    release_sha = "a" * 40
    operation_id = "22345678-1234-4234-9234-123456789abc"
    iteration = 1


class FullMatrixDestructiveCapacityFaultTests(unittest.TestCase):
    @staticmethod
    def _pair() -> dict[str, dict[str, object]]:
        return {
            "webapp_fi": {
                "active_site": "webapp_fi", "writer_epoch": 7,
                "control_state": "active", "local_active_with_witness_lease": True,
            },
            "webapp_ir": {
                "active_site": "webapp_fi", "writer_epoch": 7,
                "control_state": "active", "local_active_with_witness_lease": False,
            },
        }

    def _state(self, phase: str = "armed") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "destructive_webapp_fi_capacity_fault",
            "operation_id": _Args.operation_id,
            "scenario_id": "wal_event_redis_blob_capacity_exhaustion_safe",
            "phase": phase,
            "writer_epoch": 7,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    def test_state_is_closed_and_recovery_disarms_exact_owned_reserve(self):
        self.assertEqual(_capacity_fault_state(self._state())["writer_epoch"], 7)
        with self.assertRaisesRegex(Exception, "capacity-fault state"):
            _capacity_fault_state(self._state("unsafe"))
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
            with patch("scripts.full_matrix_live.scenario_handlers._cleanup_capacity_fault") as cleanup:
                result = recover_active_faults(plan)
        self.assertEqual(result["recovered_kinds"], ["destructive_webapp_fi_capacity_fault"])
        cleanup.assert_called_once()

    def test_actual_capacity_doer_arms_probes_controlled_http_fence_then_releases(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root}
            armed = {
                "status": "armed", "operation_id": _Args.operation_id,
                "storage_total_bytes": 10_000, "available_bytes": 1_000,
                "hard_limit_bytes": 1_024 - 24, "marker_sha256": "b" * 64,
            }
            released = {
                "status": "cleared", "operation_id": _Args.operation_id,
                "storage_total_bytes": 10_000, "available_bytes": 9_000,
                "hard_limit_bytes": 1_000,
            }
            with patch(
                "scripts.full_matrix_live.scenario_handlers._writer_pair_observation",
                return_value=self._pair(),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._public_ingress_probe",
                side_effect=[{"origin": "fi"}, {"origin": "fi"}],
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._capacity_agent_operation",
                side_effect=[armed, released],
            ) as actuator, patch(
                "scripts.full_matrix_live.scenario_handlers._capacity_writer_fence_probe",
                return_value={"http_status": 503, "reason": "full_matrix_capacity_hard_limit"},
            ) as probe, patch(
                "scripts.full_matrix_live.scenario_handlers._wait_for_witness_recovery",
                return_value=self._pair(),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._wait_for_business_convergence",
                return_value=({"converged": True}, {"webapp_fi": {}}),
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.collect_all_host_snapshots",
                return_value={"webapp_fi": {"mount": {}}, "webapp_ir": {}, "bot_fi": {}, "witness": {}},
            ):
                outcome, observations = _run_capacity_fault(_Args(), plan)
        self.assertTrue(outcome["webapp_writer_rejected_unsafe_http_mutation_before_data_plane_write"])
        self.assertEqual([call.kwargs["action"] for call in actuator.call_args_list], ["arm", "disarm"])
        probe.assert_called_once()
        self.assertEqual(observations["writer_fence_probe"]["http_status"], 503)
        self.assertFalse((root / "active-faults.json").exists())
