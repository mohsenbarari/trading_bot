from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    LiveMatrixError,
    _repeatability_observation,
)


class FullMatrixRepeatabilityTests(unittest.TestCase):
    @staticmethod
    def _write_evidence(
        root: Path,
        *,
        name: str,
        scenario_id: str,
        iteration: int,
        assertion_count: int,
        attempt: int = 1,
        expected: bool = True,
    ) -> None:
        value = {
            "schema": "three-site-staging-full-matrix-scenario-v2",
            "status": "passed",
            "campaign_id": "12345678-1234-4234-9234-123456789abc",
            "release_sha": "a" * 40,
            "activation_sha": "a" * 40,
            "iteration": iteration,
            "scenario_id": scenario_id,
            "attempt": attempt,
            "assertions": [
                {
                    "name": f"assertion-{index}",
                    "status": "passed",
                    "expected": expected,
                }
                for index in range(assertion_count)
            ],
        }
        path = root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)

    def test_second_cycle_cannot_reduce_retained_oracle_strength(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            root.chmod(0o700)
            state = root / "state"
            state.mkdir(mode=0o700)
            plan = {
                "campaign_id": "12345678-1234-4234-9234-123456789abc",
                "release_sha": "a" * 40,
                "execution_class": "shared-host-safe",
                "_state_root": state,
            }
            args = argparse.Namespace(artifact_root=root, iteration=1)
            self._write_evidence(
                root,
                name="a-first-scenario-evidence.json",
                scenario_id="scenario_a",
                iteration=1,
                assertion_count=2,
            )
            self._write_evidence(
                root,
                name="b-first-scenario-evidence.json",
                scenario_id="scenario_b",
                iteration=1,
                assertion_count=3,
            )
            with patch(
                "scripts.full_matrix_live.scenario_handlers._expected_repeatability_scenarios",
                return_value={"scenario_a", "scenario_b"},
            ):
                first, _evidence = _repeatability_observation(
                    args, plan, allow_write=True
                )
                self.assertEqual(first["cycle_state"], "first_cycle_baseline_recorded")
                verified, _evidence = _repeatability_observation(
                    args, plan, allow_write=False
                )
                self.assertEqual(verified, first)

                args.iteration = 2
                self._write_evidence(
                    root,
                    name="a-second-scenario-evidence.json",
                    scenario_id="scenario_a",
                    iteration=2,
                    assertion_count=2,
                )
                self._write_evidence(
                    root,
                    name="b-second-scenario-evidence.json",
                    scenario_id="scenario_b",
                    iteration=2,
                    assertion_count=4,
                )
                second, _evidence = _repeatability_observation(
                    args, plan, allow_write=False
                )
                self.assertTrue(second["same_or_stronger_verified"])

                self._write_evidence(
                    root,
                    name="b-second-retry-scenario-evidence.json",
                    scenario_id="scenario_b",
                    iteration=2,
                    assertion_count=3,
                    attempt=2,
                    expected=False,
                )
                with self.assertRaises(LiveMatrixError):
                    _repeatability_observation(args, plan, allow_write=False)


if __name__ == "__main__":
    unittest.main()
