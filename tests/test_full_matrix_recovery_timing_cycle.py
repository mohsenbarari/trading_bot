from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _recovery_timing_state,
    _run_recovery_timing_cycle,
    recover_active_faults,
)


class _Args:
    operation_id = "12345678-1234-4234-9234-123456789abc"


class FullMatrixRecoveryTimingCycleTests(unittest.TestCase):
    def _state(self, *, phase: str = "prepared") -> dict[str, str]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "recovery_timing_probe",
            "operation_id": _Args.operation_id,
            "scenario_id": "reconnect_flap_and_bounded_catchup",
            "fault_id": "FMX_1234567890AB_RCV",
            "fixture_first": "FMX_1234567890AB_RD1",
            "fixture_second": "FMX_1234567890AB_RD2",
            "fixture_live": "FMX_1234567890AB_RDL",
            "correlation_prefix": "fmxtiming:1234567890abd",
            "phase": phase,
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    def test_state_rejects_unknown_phase_and_recovery_is_exactly_dispatched(self):
        invalid = self._state(phase="unsafe")
        with self.assertRaisesRegex(Exception, "recovery timing active state"):
            _recovery_timing_state(invalid)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root}
            path = root / "active-faults.json"
            path.write_text(__import__("json").dumps(self._state()) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_recovery_timing_probe"
            ) as cleanup:
                result = recover_active_faults(plan)
            self.assertEqual(result["recovered_kinds"], ["recovery_timing_probe"])
            cleanup.assert_called_once()

    def test_cycle_has_two_closed_pauses_and_one_atomic_resume_emit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root, "_roles": {"webapp_ir": {}}}
            now = datetime.now(timezone.utc)
            pending = [
                {
                    "captured_at": now,
                    "pending_events": 2,
                    "oldest_age_seconds": 1.0,
                    "snapshot_sha256": "a" * 64,
                },
                {
                    "captured_at": now + timedelta(seconds=1),
                    "pending_events": 3,
                    "oldest_age_seconds": 2.0,
                    "snapshot_sha256": "b" * 64,
                },
            ]
            emitter = {
                "schema": "three-site-full-matrix-timing-emitter-v1",
                "status": "passed",
                "role": "webapp_ir",
                "samples": [],
                "started_epoch": 1.0,
                "finished_epoch": 2.0,
            }
            calls: list[str] = []

            def fault(_plan, *, action, fault_id):  # noqa: ANN001
                calls.append(action)
                return {"status": "passed", "action": action, "fault_id": fault_id}

            def cleanup(cleanup_plan, state):  # noqa: ANN001
                (cleanup_plan["_state_root"] / "active-faults.json").unlink()
                return {"cleanup": True}

            with patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_writer_precondition",
                return_value={"writer": "ir"},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_delivery_fault",
                side_effect=fault,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._run_timing_emitter",
                return_value=emitter,
            ) as emit, patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_snapshot_set",
                return_value={},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._pending_backlog_snapshot",
                side_effect=pending,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_delivery_resume_emit",
                return_value=emitter,
            ) as resume_emit, patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_backlog_manifest",
                return_value={},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._timing_manifest_with_journal_durations",
                return_value={},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.build_timing_evidence",
                return_value={"artifact": "accepted"},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_timing_outcome",
                return_value={"passed": True},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_recovery_timing_probe",
                side_effect=cleanup,
            ):
                outcome, observations = _run_recovery_timing_cycle(
                    _Args(), plan, label="doer"
                )
            self.assertEqual(outcome, {"passed": True})
            self.assertEqual(calls, ["pause", "resume", "pause"])
            self.assertEqual(emit.call_count, 2)
            self.assertEqual(resume_emit.call_count, 1)
            emitted_prefixes = [
                item.kwargs["correlation_prefix"] for item in emit.call_args_list
            ] + [resume_emit.call_args.kwargs["correlation_prefix"]]
            self.assertTrue(all(16 <= len(item) <= 24 for item in emitted_prefixes))
            self.assertEqual(len(set(emitted_prefixes)), 3)
            self.assertFalse((root / "active-faults.json").exists())
            self.assertEqual(observations["cleanup"], {"cleanup": True})


if __name__ == "__main__":
    unittest.main()
