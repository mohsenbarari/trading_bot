from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts import build_stage9_traceability as traceability
from scripts import check_stage9_mutation_evidence as mutation_checker
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
        self.commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()

    def _runtime_results(self):
        automated = set(self.config["automated_prefixes"])
        scenario_ids = {
            item
            for item in expand_id_ranges(self.config["id_ranges"])
            if item.split("-", 1)[0] in automated
        }
        scenarios = {}
        for scenario_id in scenario_ids:
            binding = self.config["registry_bindings"][scenario_id]
            evidence_stage = binding["evidence_stage"]
            if evidence_stage in {"matrix_stage10", "stage10", "stage12"}:
                scenarios[scenario_id] = {
                    "status": "deferred",
                    "blocker": binding["blocker"],
                }
            elif evidence_stage == "matrix":
                scenarios[scenario_id] = {
                    "status": "passed",
                    "matrix_row": scenario_id,
                }
            else:
                scenarios[scenario_id] = {
                    "status": "passed",
                    "test_ids": binding["test_ids"],
                }
        return {
            "commit": self.commit,
            "scenarios": scenarios,
            "transitions": {
                transition["id"]: {
                    "status": "passed",
                    "test_ids": transition["test_ids"],
                    "observed_outcome": (
                        "accepted"
                        if transition["legal"]
                        else "rejected_without_mutation"
                    ),
                }
                for transition in self.config["transitions"]
            },
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

    def test_nonexistent_transition_test_method_fails_closed(self):
        broken = copy.deepcopy(self.config)
        broken["transitions"][0]["test_ids"] = [
            "tests.test_registration_stage1_contracts.RegistrationStage1ContractTests.test_not_real"
        ]
        with self.assertRaisesRegex(TraceabilityError, "nonexistent_test_id"):
            validate_configuration(broken, ROADMAP)

    def test_every_automated_registry_id_has_an_exact_binding(self):
        validate_configuration(self.config, ROADMAP)
        broken = copy.deepcopy(self.config)
        broken["registry_bindings"].pop("REG-001")
        with self.assertRaisesRegex(TraceabilityError, "registry_binding_set_mismatch"):
            validate_configuration(broken, ROADMAP)

        broken = copy.deepcopy(self.config)
        broken["registry_bindings"]["REG-001"]["test_ids"] = [
            "tests.test_authoritative_registration_service.AuthoritativeRegistrationServiceTests.test_not_real"
        ]
        with self.assertRaisesRegex(TraceabilityError, "nonexistent_test_id"):
            validate_configuration(broken, ROADMAP)

    def test_transition_inventory_requires_legal_and_illegal_rows(self):
        broken = copy.deepcopy(self.config)
        broken["transitions"] = [
            transition for transition in broken["transitions"] if transition["legal"]
        ]
        broken["expected_transition_ids"] = [
            transition["id"] for transition in broken["transitions"]
        ]
        with self.assertRaisesRegex(TraceabilityError, "legal_and_illegal_transitions_required"):
            validate_configuration(broken, ROADMAP)

    def test_skipped_required_result_fails_closed(self):
        results = self._runtime_results()
        results["scenarios"]["REG-001"] = {"status": "skipped"}
        with self.assertRaisesRegex(TraceabilityError, "required_test_not_passed:REG-001:skipped"):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage={"passed": True, "commit": self.commit},
                frontend_coverage={"passed": True, "commit": self.commit},
                mutation={"passed": True, "commit": self.commit},
            )

    def test_runtime_evidence_requires_exact_sets_and_green_gates(self):
        validate_runtime_evidence(
            self.config,
            results=self._runtime_results(),
            backend_coverage={"passed": True, "commit": self.commit},
            frontend_coverage={"passed": True, "commit": self.commit},
            mutation={"passed": True, "commit": self.commit},
        )

    def test_runtime_transition_requires_exact_test_and_observed_outcome(self):
        results = self._runtime_results()
        transition_id = self.config["transitions"][0]["id"]
        results["transitions"][transition_id]["test_ids"] = ["tests.not_real"]
        with self.assertRaisesRegex(TraceabilityError, "transition_test_result_mismatch"):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage={"passed": True, "commit": self.commit},
                frontend_coverage={"passed": True, "commit": self.commit},
                mutation={"passed": True, "commit": self.commit},
            )

        results = self._runtime_results()
        results["transitions"][transition_id]["observed_outcome"] = "rejected_without_mutation"
        with self.assertRaisesRegex(TraceabilityError, "transition_outcome_mismatch"):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage={"passed": True, "commit": self.commit},
                frontend_coverage={"passed": True, "commit": self.commit},
                mutation={"passed": True, "commit": self.commit},
            )

    def test_runtime_evidence_from_another_commit_fails_closed(self):
        results = self._runtime_results()
        results["commit"] = "0" * 40
        with self.assertRaisesRegex(TraceabilityError, "runtime_evidence_commit_mismatch:results"):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage={"passed": True, "commit": self.commit},
                frontend_coverage={"passed": True, "commit": self.commit},
                mutation={"passed": True, "commit": self.commit},
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

    def test_mutation_evidence_rejects_invalid_schema_inventory_results_and_sets(self):
        valid_results = {
            target["id"]: {"status": "killed"}
            for target in self.mutation_manifest["targets"]
        }
        cases = []
        broken_manifest = copy.deepcopy(self.mutation_manifest)
        broken_manifest["schema_version"] = 1
        cases.append((broken_manifest, {"schema_version": 1, "results": valid_results}, "unsupported_schema_version"))
        broken_manifest = copy.deepcopy(self.mutation_manifest)
        broken_manifest["targets"] = []
        cases.append((broken_manifest, {"schema_version": 1, "results": {}}, "mutation_targets_required"))
        cases.append((self.mutation_manifest, {"schema_version": 1, "results": []}, "mutation_results_required"))
        missing = dict(valid_results)
        missing.pop(next(iter(missing)))
        cases.append((self.mutation_manifest, {"schema_version": 1, "results": missing}, "mutation_result_set_mismatch"))
        invalid = dict(valid_results)
        invalid[next(iter(invalid))] = "invalid"
        cases.append((self.mutation_manifest, {"schema_version": 1, "results": invalid}, "critical_mutant_not_killed"))
        for manifest, evidence, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(MutationEvidenceError, message):
                validate_mutation_evidence(manifest, evidence)

    def test_mutation_evidence_main_writes_verified_report_and_fails_closed(self):
        evidence = {
            "schema_version": 1,
            "results": {
                target["id"]: {"status": "killed"}
                for target in self.mutation_manifest["targets"]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            evidence_path = root / "evidence.json"
            output_path = root / "verification.json"
            manifest_path.write_text(json.dumps(self.mutation_manifest), encoding="utf-8")
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            self.assertEqual(
                mutation_checker.main(
                    [
                        "--manifest",
                        str(manifest_path),
                        "--evidence",
                        str(evidence_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                0,
            )
            self.assertEqual(
                json.loads(output_path.read_text())["target_count"],
                len(self.mutation_manifest["targets"]),
            )
            evidence_path.write_text("[]", encoding="utf-8")
            self.assertEqual(
                mutation_checker.main(
                    [
                        "--manifest",
                        str(manifest_path),
                        "--evidence",
                        str(evidence_path),
                        "--output",
                        str(output_path),
                    ]
                ),
                1,
            )

    def test_configuration_rejects_classification_references_bindings_and_transitions(self):
        cases = []

        def broken(message, mutate):
            payload = copy.deepcopy(self.config)
            mutate(payload)
            cases.append((payload, message))

        broken("unsupported_schema_version", lambda value: value.__setitem__("schema_version", 2))
        broken("registry_prefix_classification_mismatch", lambda value: value["automated_prefixes"].remove("REG"))
        broken("test_reference_map_required", lambda value: value.__setitem__("test_references", []))
        broken("test_reference_required", lambda value: value["test_references"].__setitem__("REG", []))
        broken("registry_binding_set_mismatch", lambda value: value.__setitem__("registry_bindings", []))
        broken("invalid_registry_binding", lambda value: value["registry_bindings"].__setitem__("REG-001", []))
        broken("invalid_registry_binding_stage", lambda value: value["registry_bindings"]["REG-001"].__setitem__("evidence_stage", "stage11"))
        broken("registry_test_ids_required", lambda value: value["registry_bindings"]["REG-001"].__setitem__("test_ids", []))
        broken("registry_matrix_row_mismatch", lambda value: value["registry_bindings"]["MKT-001"].__setitem__("matrix_row", "MKT-002"))
        broken("registry_deferred_blocker_required", lambda value: value["registry_bindings"]["MKT-004"].__setitem__("blocker", ""))
        broken("transitions_required", lambda value: value.__setitem__("transitions", {}))
        broken("transition_legality_required", lambda value: value["transitions"][0].__setitem__("legal", "yes"))
        broken("transition_test_required", lambda value: value["transitions"][0].__setitem__("test_ids", []))
        for config, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(TraceabilityError, message):
                validate_configuration(config, ROADMAP)

    def test_test_id_validation_rejects_shape_missing_module_and_missing_class(self):
        for test_id, message in (
            ("tests.bad", "invalid_test_id"),
            ("tests.not_real.Case.test_case", "nonexistent_test_id_module"),
            (
                "tests.test_stage9_traceability.NotAClass.test_case",
                "nonexistent_test_id",
            ),
        ):
            with self.subTest(test_id=test_id), self.assertRaisesRegex(TraceabilityError, message):
                traceability._assert_test_id(test_id)

    def test_runtime_evidence_rejects_sets_bindings_transitions_and_each_red_gate(self):
        green = {"passed": True, "commit": self.commit}

        def validate(results, backend=green, frontend=green, mutation=green):
            validate_runtime_evidence(
                self.config,
                results=results,
                backend_coverage=backend,
                frontend_coverage=frontend,
                mutation=mutation,
            )

        cases = []
        results = self._runtime_results()
        results["scenarios"] = []
        cases.append((results, "scenario_results_required"))
        results = self._runtime_results()
        results["scenarios"].pop("REG-001")
        cases.append((results, "scenario_result_set_mismatch"))
        results = self._runtime_results()
        results["scenarios"]["REG-001"] = "invalid"
        cases.append((results, "required_test_not_passed:REG-001:invalid"))
        deferred_id = next(
            key
            for key, binding in self.config["registry_bindings"].items()
            if binding["evidence_stage"] in {"matrix_stage10", "stage10", "stage12"}
        )
        results = self._runtime_results()
        results["scenarios"][deferred_id]["blocker"] = "wrong"
        cases.append((results, "deferred_blocker_mismatch"))
        results = self._runtime_results()
        results["scenarios"]["REG-001"]["test_ids"] = []
        cases.append((results, "registry_test_result_mismatch"))
        results = self._runtime_results()
        results["scenarios"]["MKT-001"]["matrix_row"] = "MKT-002"
        cases.append((results, "registry_matrix_result_mismatch"))
        results = self._runtime_results()
        results["transitions"] = []
        cases.append((results, "transition_results_required"))
        results = self._runtime_results()
        results["transitions"].pop(next(iter(results["transitions"])))
        cases.append((results, "transition_result_set_mismatch"))
        results = self._runtime_results()
        transition_id = next(iter(results["transitions"]))
        results["transitions"][transition_id] = "invalid"
        cases.append((results, "transition_not_passed"))
        for results, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(TraceabilityError, message):
                validate(results)

        for label in ("backend", "frontend", "mutation"):
            evidence = {"passed": False, "commit": self.commit}
            kwargs = {"backend": green, "frontend": green, "mutation": green}
            kwargs[label] = evidence
            with self.subTest(label=label), self.assertRaisesRegex(
                TraceabilityError, f"{label}_evidence_not_passed"
            ):
                validate(self._runtime_results(), **kwargs)

    def test_build_artifact_contains_every_registry_row_and_runtime_commit(self):
        ids = validate_configuration(self.config, ROADMAP)
        results = self._runtime_results()
        evidence = {"passed": True, "commit": self.commit}
        artifact = traceability.build_artifact(
            self.config,
            ids,
            results=results,
            backend_coverage=evidence,
            frontend_coverage=evidence,
            mutation=evidence,
        )
        self.assertEqual(len(artifact["registry_rows"]), len(ids))
        self.assertEqual(artifact["commit"], self.commit)
        self.assertEqual(set(artifact["evidence_commits"].values()), {self.commit})
        automated = next(row for row in artifact["registry_rows"] if row["id"] == "REG-001")
        deferred = next(row for row in artifact["registry_rows"] if row["id"] == "DN-01")
        self.assertEqual(automated["test_evidence"], self.config["registry_bindings"]["REG-001"])
        self.assertEqual(deferred["test_evidence"], [])

    def test_read_json_and_main_fail_closed_then_generate_runtime_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            array_path = root / "array.json"
            array_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(TraceabilityError, "object_required"):
                traceability._read_json(str(array_path))

            self.assertEqual(
                traceability.main(
                    [
                        "--config",
                        str(ROOT / "config/stage9_traceability.json"),
                        "--roadmap",
                        str(ROADMAP),
                        "--validate-only",
                    ]
                ),
                0,
            )
            self.assertEqual(
                traceability.main(
                    [
                        "--config",
                        str(ROOT / "config/stage9_traceability.json"),
                        "--roadmap",
                        str(ROADMAP),
                    ]
                ),
                1,
            )
            self.assertEqual(
                traceability.main(
                    [
                        "--config",
                        str(ROOT / "config/stage9_traceability.json"),
                        "--roadmap",
                        str(ROADMAP),
                        "--validate-only",
                        "--require-runtime-evidence",
                    ]
                ),
                1,
            )
            self.assertEqual(
                traceability.main(
                    [
                        "--config",
                        str(ROOT / "config/stage9_traceability.json"),
                        "--roadmap",
                        str(ROADMAP),
                        "--require-runtime-evidence",
                    ]
                ),
                1,
            )

            results = self._runtime_results()
            green = {"passed": True, "commit": self.commit}
            files = {}
            for name, payload in (
                ("results", results),
                ("backend", green),
                ("frontend", green),
                ("mutation", green),
            ):
                path = root / f"{name}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                files[name] = path
            output = root / "traceability.json"
            self.assertEqual(
                traceability.main(
                    [
                        "--config",
                        str(ROOT / "config/stage9_traceability.json"),
                        "--roadmap",
                        str(ROADMAP),
                        "--require-runtime-evidence",
                        "--results",
                        str(files["results"]),
                        "--backend-coverage",
                        str(files["backend"]),
                        "--frontend-coverage",
                        str(files["frontend"]),
                        "--mutation",
                        str(files["mutation"]),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            self.assertEqual(json.loads(output.read_text())["commit"], self.commit)


if __name__ == "__main__":
    unittest.main()
