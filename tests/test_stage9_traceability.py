from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.build_stage9_traceability import (
    TraceabilityError,
    expand_id_ranges,
    validate_configuration,
    validate_runtime_evidence,
)
from scripts.check_stage9_mutation_evidence import (
    MutationEvidenceError,
    validate_mutation_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / "docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md"


class Stage9TraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/stage9_traceability.json").read_text())
        self.mutation_manifest = json.loads((ROOT / "config/stage9_mutation_manifest.json").read_text())

    def _runtime_results(self):
        automated = set(self.config["automated_prefixes"])
        scenario_ids = {
            item
            for item in expand_id_ranges(self.config["id_ranges"])
            if item.split("-", 1)[0] in automated
        }
        return {
            "scenarios": {item: {"status": "passed"} for item in scenario_ids},
            "transitions": {item: {"status": "passed"} for item in self.config["expected_transition_ids"]},
        }

    def test_repository_traceability_configuration_matches_roadmap(self):
        ids = validate_configuration(self.config, ROADMAP)
        self.assertIn("MIG-001", ids)
        self.assertIn("MIG-010", ids)

    def test_missing_registry_tuple_fails_closed(self):
        broken = copy.deepcopy(self.config)
        broken["id_ranges"]["REG"] = 9
        with self.assertRaisesRegex(TraceabilityError, "registry_set_mismatch"):
            validate_configuration(broken, ROADMAP)

    def test_missing_transition_fails_closed(self):
        broken = copy.deepcopy(self.config)
        broken["transitions"] = broken["transitions"][:-1]
        with self.assertRaisesRegex(TraceabilityError, "transition_set_mismatch"):
            validate_configuration(broken, ROADMAP)

    def test_nonexistent_reference_fails_closed(self):
        broken = copy.deepcopy(self.config)
        broken["test_references"]["REG"].append("tests/not_real.py")
        with self.assertRaisesRegex(TraceabilityError, "nonexistent_test_reference"):
            validate_configuration(broken, ROADMAP)

    def test_skipped_required_result_fails_closed(self):
        results = self._runtime_results()
        results["scenarios"]["REG-001"] = {"status": "skipped"}
        with self.assertRaisesRegex(TraceabilityError, "required_test_not_passed:REG-001:skipped"):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage={"passed": True},
                frontend_coverage={"passed": True},
                mutation={"passed": True},
            )

    def test_runtime_evidence_requires_exact_sets_and_green_gates(self):
        validate_runtime_evidence(
            self.config,
            results=self._runtime_results(),
            backend_coverage={"passed": True},
            frontend_coverage={"passed": True},
            mutation={"passed": True},
        )

    def test_surviving_critical_mutant_fails_closed(self):
        evidence = {
            "schema_version": 1,
            "results": {
                target["id"]: {"status": "killed"}
                for target in self.mutation_manifest["targets"]
            },
        }
        evidence["results"]["MUT-OTP-CONSUME"] = {"status": "survived"}
        with self.assertRaisesRegex(MutationEvidenceError, "critical_mutant_not_killed"):
            validate_mutation_evidence(self.mutation_manifest, evidence)


if __name__ == "__main__":
    unittest.main()
