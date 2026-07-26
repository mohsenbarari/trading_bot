from __future__ import annotations

import unittest

from core.three_site_full_matrix_campaign import PHASE_SCENARIOS
from scripts.full_matrix_live.scenario_handlers import _one_hour_backlog_schedule
from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _one_hour_backlog_state,
    _run_one_hour_backlog_cycle,
    recover_active_faults,
)
from pathlib import Path
import json
import tempfile
from unittest.mock import patch


class FullMatrixOneHourBacklogPlanTests(unittest.TestCase):
    def _state(self) -> dict:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "one_hour_recovery_backlog",
            "operation_id": "12345678-1234-4234-9234-123456789abc",
            "scenario_id": "one_hour_backlog_with_live_traffic",
            "fault_id": "FMX_1234567890AB_1HR",
            "batch_fixtures": [f"FMX_1234567890AB_B{index:02d}" for index in range(20)],
            "correlation_prefix": "fmxtiming:1234567890abh",
            "live_fixture": "FMX_1234567890AB_LIVE",
            "phase": "emitting",
            "completed_batch_count": 7,
            "pause_started_at": "2026-07-26T00:00:00+00:00",
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    def test_one_hour_recovery_load_is_ir_active_and_exceeds_one_rps(self):
        recovery = PHASE_SCENARIOS["recovery_failback"]
        self.assertIn("one_hour_backlog_with_live_traffic", recovery)
        self.assertLess(
            recovery.index("one_hour_backlog_with_live_traffic"),
            recovery.index("fi_epoch_reacquire_and_route_switch"),
        )
        self.assertNotIn(
            "one_hour_backlog_with_live_traffic",
            PHASE_SCENARIOS["capacity_dpi"],
        )
        schedule = _one_hour_backlog_schedule()
        self.assertEqual(len(schedule), 20)
        self.assertEqual(schedule[0]["start_after_seconds"], 0)
        self.assertEqual(schedule[-1]["start_after_seconds"], 3420)
        total_events = sum(int(item["samples_per_route"]) * 2 for item in schedule)
        self.assertGreaterEqual(total_events / 3600, 1.0)
        self.assertLessEqual(
            (int(schedule[-1]["samples_per_route"]) * 2)
            / float(schedule[-1]["target_rps"]),
            180,
        )

    def test_durable_state_has_exact_twenty_batch_cleanup_targets(self):
        state = _one_hour_backlog_state(self._state())
        self.assertEqual(state["completed_batch_count"], 7)
        state["batch_fixtures"] = state["batch_fixtures"][:-1]
        with self.assertRaisesRegex(Exception, "one-hour recovery backlog state"):
            _one_hour_backlog_state(state)

    def test_recovery_dispatches_only_the_exact_one_hour_cleanup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "active-faults.json"
            path.write_text(json.dumps(self._state()) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_one_hour_recovery_backlog"
            ) as cleanup:
                result = recover_active_faults({"_state_root": root})
        self.assertEqual(result["recovered_kinds"], ["one_hour_recovery_backlog"])
        cleanup.assert_called_once()

    def test_cycle_runs_twenty_paused_batches_before_atomic_resume_emit(self):
        class Args:
            operation_id = "12345678-1234-4234-9234-123456789abc"

        def emitter(prefix: str, *, finished: float) -> dict:
            return {
                "schema": "three-site-full-matrix-timing-emitter-v1",
                "status": "passed",
                "role": "webapp_ir",
                "fixture_prefix": "FMX_1234567890A_HX00",
                "correlation_prefix": prefix,
                "sample_count": 200,
                "started_epoch": 0.0,
                "finished_epoch": finished,
                "three_site_writer_fence": True,
                "production_touched": False,
                "samples": [
                    {
                        "sample_id": f"webapp_ir:r:{index:03d}",
                        "correlation_id": f"{prefix}:route:{index:04d}",
                        "route": "webapp_ir_to_webapp_fi",
                        "controller_observed_duration_seconds": 0.1,
                    }
                    for index in range(200)
                ],
            }

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = {"_state_root": root, "_roles": {"webapp_ir": {}} , "release_sha": "a" * 40}
            pending = [
                {
                    "captured_at": __import__("datetime").datetime(2026, 7, 26, tzinfo=__import__("datetime").timezone.utc)
                    + __import__("datetime").timedelta(seconds=index),
                    "pending_events": 200,
                    "oldest_age_seconds": float(index),
                    "snapshot_sha256": f"{index:064x}",
                }
                for index in range(20)
            ]
            applied = [
                {f"event-{index}-{item}" for item in range(200)} for index in range(21)
            ]
            calls: list[str] = []

            def fault(_plan, *, action, fault_id):  # noqa: ANN001
                calls.append(action)
                return {"status": "passed", "action": action, "fault_id": fault_id}

            def timing_emit(_args, _plan, **kwargs):  # noqa: ANN001
                return emitter(kwargs["correlation_prefix"], finished=100.0)

            def resume_emit(_args, _plan, **kwargs):  # noqa: ANN001
                return emitter(kwargs["correlation_prefix"], finished=3700.0)

            def cleanup(cleanup_plan, _state):  # noqa: ANN001
                (cleanup_plan["_state_root"] / "active-faults.json").unlink()
                return {"cleanup": True}

            snapshots = {"webapp_ir": {"captured_at": "2026-07-26T01:01:00+00:00"}}
            with patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_writer_precondition",
                return_value={"writer": "ir"},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._wait_until_monotonic"
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_delivery_fault",
                side_effect=fault,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._run_timing_emitter",
                side_effect=timing_emit,
            ) as emit, patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_snapshot_set",
                return_value=snapshots,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._pending_backlog_snapshot",
                side_effect=pending,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._recovery_delivery_resume_emit",
                side_effect=resume_emit,
            ) as resume_emit_call, patch(
                "scripts.full_matrix_live.scenario_handlers._batch_applied_events",
                side_effect=applied,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._timing_manifest_with_journal_durations",
                side_effect=lambda manifest, snapshots: manifest,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.build_timing_evidence",
                return_value={"artifact": "accepted"},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._one_hour_backlog_outcome",
                return_value={"passed": True},
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_one_hour_recovery_backlog",
                side_effect=cleanup,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.time.monotonic",
                side_effect=[0.0, 3600.0] + [3601.0] * 100,
            ), patch(
                "scripts.full_matrix_live.scenario_handlers.time.time",
                return_value=0.0,
            ):
                outcome, observations = _run_one_hour_backlog_cycle(Args(), plan, label="doer")
        self.assertEqual(outcome, {"passed": True})
        self.assertEqual(calls, ["pause"])
        self.assertEqual(emit.call_count, 20)
        self.assertEqual(resume_emit_call.call_count, 1)
        self.assertEqual(len(observations["paused_batches"]), 20)
        self.assertFalse((root / "active-faults.json").exists())


if __name__ == "__main__":
    unittest.main()
