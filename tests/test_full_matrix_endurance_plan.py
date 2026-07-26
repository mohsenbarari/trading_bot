from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    ACTIVE_FAULT_SCHEMA,
    _endurance_journal,
    _endurance_schedule,
    _endurance_state,
    _run_endurance_cycle,
    recover_active_faults,
)


class _Args:
    operation_id = "12345678-1234-4234-9234-123456789abc"

    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root


class FullMatrixEndurancePlanTests(unittest.TestCase):
    def _state(self, *, phase: str = "completed") -> dict[str, object]:
        return {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "twenty_four_hour_endurance",
            "operation_id": _Args.operation_id,
            "scenario_id": "twenty_four_hour_endurance_no_growth",
            "fixture_prefix": "FMX_1234567890_ENDURANCE",
            "correlation_root": "fmxtiming:1234567890e",
            "phase": phase,
            "completed_sample_count": 288,
            "started_at": "2026-07-26T00:00:00+00:00",
            "last_sample_at": "2026-07-27T00:00:00+00:00",
            "created_at": "2026-07-26T00:00:00+00:00",
        }

    def _record(self, index: int) -> dict[str, object]:
        return {
            "index": index,
            "scheduled_elapsed_seconds": index * 300,
            "observed_elapsed_seconds": float(index * 300),
            "finished_at": datetime(2026, 7, 26, tzinfo=timezone.utc).isoformat(),
            "writer_epoch": 7,
            "emitted_sample_count": 2,
            "convergence_sha256": "a" * 64,
            "host_snapshots_sha256": "b" * 64,
            "database_row_counts": {
                "bot_fi": 100 + index,
                "webapp_fi": 100 + index,
                "webapp_ir": 100 + index,
            },
            "available_storage_bytes": {
                "bot_fi": 100 * 1024**3,
                "webapp_fi": 100 * 1024**3,
                "webapp_ir": 100 * 1024**3,
                "witness": 100 * 1024**3,
            },
        }

    def test_schedule_is_an_actual_24_hour_window_with_288_observations(self):
        schedule = _endurance_schedule()
        self.assertEqual(len(schedule), 288)
        self.assertEqual(schedule[0]["start_after_seconds"], 0)
        self.assertEqual(schedule[-1]["start_after_seconds"], 86_100)
        self.assertEqual(sum(int(item["samples_per_route"]) * 2 for item in schedule), 576)

    def test_state_and_journal_reject_missing_or_out_of_order_samples(self):
        state = _endurance_state(self._state())
        self.assertEqual(state["completed_sample_count"], 288)
        invalid = self._state()
        invalid["phase"] = "unsafe"
        with self.assertRaisesRegex(Exception, "24-hour endurance active state"):
            _endurance_state(invalid)
        journal = {
            "schema": "three-site-full-matrix-endurance-journal-v1",
            "operation_id": _Args.operation_id,
            "scenario_id": "twenty_four_hour_endurance_no_growth",
            "records": [self._record(0), self._record(1)],
        }
        self.assertEqual(len(_endurance_journal(journal)["records"]), 2)
        journal["records"][1]["index"] = 3
        with self.assertRaisesRegex(Exception, "24-hour endurance journal record"):
            _endurance_journal(journal)

    def test_cycle_has_288_durable_samples_and_never_cleans_before_oracle(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            args = _Args(root)
            plan = {"_state_root": root, "release_sha": "a" * 40}
            records = [self._record(index) for index in range(288)]
            with patch(
                "scripts.full_matrix_live.scenario_handlers._wait_until_monotonic"
            ), patch(
                "scripts.full_matrix_live.scenario_handlers._endurance_sample",
                side_effect=records,
            ) as sample, patch(
                "scripts.full_matrix_live.scenario_handlers.time.monotonic",
                side_effect=[0.0, 86_400.0],
            ):
                outcome, observations = _run_endurance_cycle(args, plan)
            self.assertEqual(sample.call_count, 288)
            self.assertTrue(outcome["full_twenty_four_monotonic_hours_completed"])
            self.assertEqual(observations["sample_count"], 288)
            self.assertTrue((root / "active-faults.json").exists())
            self.assertTrue((root / f"{_Args.operation_id}-endurance-journal.json").exists())

    def test_recovery_dispatches_exact_endurance_cleanup(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "active-faults.json"
            path.write_text(json.dumps(self._state()) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with patch(
                "scripts.full_matrix_live.scenario_handlers._cleanup_endurance"
            ) as cleanup:
                result = recover_active_faults({"_state_root": root})
        self.assertEqual(result["recovered_kinds"], ["twenty_four_hour_endurance"])
        cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
