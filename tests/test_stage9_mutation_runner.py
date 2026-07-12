from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.run_stage9_mutation_gate import (
    grouped_evidence,
    parse_mutmut_results,
    parse_mutmut_run_results,
)


ROOT = Path(__file__).resolve().parents[1]


class Stage9MutationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "config/stage9_mutation_manifest.json").read_text())

    def test_parser_and_grouping_require_every_matching_mutant_killed(self):
        raw = "\n".join(
            f"    {target['matcher'].removesuffix('*')}__mutmut_1: killed"
            for target in self.manifest["targets"]
        )
        manifest = {**self.manifest, "equivalent_mutants": []}
        evidence = grouped_evidence(manifest, parse_mutmut_results(raw))
        self.assertTrue(evidence["passed"])
        self.assertTrue(all(item["status"] == "killed" for item in evidence["results"].values()))

    def test_missing_or_surviving_group_fails(self):
        first = self.manifest["targets"][0]
        raw = f"{first['matcher'].removesuffix('*')}__mutmut_1: survived"
        manifest = {**self.manifest, "equivalent_mutants": []}
        evidence = grouped_evidence(manifest, parse_mutmut_results(raw))
        self.assertFalse(evidence["passed"])
        self.assertEqual(evidence["results"][first["id"]]["status"], "survived")
        self.assertTrue(any(item["status"] == "missing" for item in evidence["results"].values()))

    def test_run_output_parser_and_named_equivalent_mutants_are_explicit(self):
        target = self.manifest["targets"][2]
        equivalent = self.manifest["equivalent_mutants"][0]
        killed_name = target["matcher"].removesuffix("*") + "__mutmut_999"
        raw = parse_mutmut_run_results(
            f"🎉 {killed_name}\n🙁 {equivalent['name']}\n"
        )
        focused_manifest = {
            "targets": [target],
            "equivalent_mutants": [equivalent],
        }

        evidence = grouped_evidence(focused_manifest, raw)

        self.assertTrue(evidence["passed"])
        result = evidence["results"][target["id"]]
        self.assertEqual(result["status"], "killed")
        self.assertEqual(result["evaluated_mutant_count"], 1)
        self.assertEqual(result["equivalent_mutants"], {equivalent["name"]: equivalent["reason"]})


if __name__ == "__main__":
    unittest.main()
