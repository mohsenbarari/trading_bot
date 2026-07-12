from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_stage9_mutation_gate as mutation_runner
from scripts.run_stage9_mutation_gate import (
    grouped_evidence,
    parse_mutmut_results,
    parse_mutmut_run_results,
    validate_manifest,
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
        equivalent = self.manifest["equivalent_mutants"][0]
        target = next(
            target
            for target in self.manifest["targets"]
            if equivalent["name"].startswith(target["matcher"].removesuffix("*"))
        )
        killed_name = target["matcher"].removesuffix("*") + "__mutmut_999"
        raw = parse_mutmut_run_results(
            f"🎉 {killed_name}\n🙁 {equivalent['name']}\n"
        )
        focused_manifest = {
            "schema_version": 2,
            "required_invariant_classes": [target["invariant_class"]],
            "targets": [target],
            "equivalent_mutants": [equivalent],
        }

        evidence = grouped_evidence(focused_manifest, raw)

        self.assertTrue(evidence["passed"])
        result = evidence["results"][target["id"]]
        self.assertEqual(result["status"], "killed")
        self.assertEqual(result["evaluated_mutant_count"], 1)
        self.assertEqual(result["equivalent_mutants"], {equivalent["name"]: equivalent["reason"]})

    def test_missing_required_invariant_class_fails_closed(self):
        broken = json.loads(json.dumps(self.manifest))
        broken["targets"] = broken["targets"][:-1]
        with self.assertRaisesRegex(ValueError, "mutation_invariant_set_mismatch"):
            validate_manifest(broken)

    def test_manifest_rejects_schema_empty_inventory_and_duplicate_keys(self):
        cases = []
        broken = json.loads(json.dumps(self.manifest))
        broken["schema_version"] = 1
        cases.append((broken, "unsupported_mutation_manifest_schema"))
        broken = json.loads(json.dumps(self.manifest))
        broken["required_invariant_classes"] = []
        cases.append((broken, "mutation_invariant_inventory_required"))
        broken = json.loads(json.dumps(self.manifest))
        broken["targets"][1]["id"] = broken["targets"][0]["id"]
        cases.append((broken, "mutation_target_ids_and_matchers_must_be_unique"))
        broken = json.loads(json.dumps(self.manifest))
        broken["targets"][1]["matcher"] = broken["targets"][0]["matcher"]
        cases.append((broken, "mutation_target_ids_and_matchers_must_be_unique"))
        for manifest, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_manifest(manifest)

    def test_equivalent_mutant_inventory_fails_closed(self):
        target = self.manifest["targets"][0]
        base = {
            "schema_version": 2,
            "required_invariant_classes": [target["invariant_class"]],
            "targets": [target],
        }
        invalid_entries = [None, [{}], [{"name": "x"}], [{"reason": "why"}]]
        for entries in invalid_entries:
            manifest = {**base, "equivalent_mutants": entries}
            expected = (
                "equivalent_mutants_must_be_a_list"
                if entries is None
                else "equivalent_mutant_name_and_reason_required"
            )
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, expected):
                grouped_evidence(manifest, {})

        unmatched = {"name": "not_a_target__mutmut_1", "reason": "equivalent"}
        with self.assertRaisesRegex(ValueError, "equivalent_mutant_target_mismatch"):
            grouped_evidence({**base, "equivalent_mutants": [unmatched]}, {})

        matching = {
            "name": target["matcher"].removesuffix("*") + "__mutmut_7",
            "reason": "equivalent",
        }
        with self.assertRaisesRegex(ValueError, "equivalent_mutants_not_generated"):
            grouped_evidence({**base, "equivalent_mutants": [matching]}, {})

    def test_parsers_ignore_unrelated_lines_and_cover_all_run_statuses(self):
        self.assertEqual(parse_mutmut_results("noise\n"), {})
        parsed = parse_mutmut_run_results(
            "noise\n🎉 killed_name\n🙁 survived_name\n⏰ timed_name\n🤔 suspicious_name\n"
        )
        self.assertEqual(
            parsed,
            {
                "killed_name": "killed",
                "survived_name": "survived",
                "timed_name": "timeout",
                "suspicious_name": "suspicious",
            },
        )

    def test_test_environment_isolated_defaults_and_preserves_existing_pythonpath(self):
        with patch.dict(os.environ, {"PYTHONPATH": "existing"}, clear=True):
            env = mutation_runner._test_environment()
        self.assertEqual(env["SERVER_MODE"], "foreign")
        self.assertEqual(env["REDIS_URL"], "redis://127.0.0.1:6379/15")
        self.assertTrue(env["PYTHONPATH"].endswith(os.pathsep + "existing"))

    def test_main_writes_evidence_and_fails_for_run_or_survivor(self):
        target = self.manifest["targets"][0]
        manifest = {
            "schema_version": 2,
            "required_invariant_classes": [target["invariant_class"]],
            "targets": [target],
            "equivalent_mutants": [],
        }
        mutant_name = target["matcher"].removesuffix("*") + "__mutmut_1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "evidence.json"
            log = root / "mutation.log"

            for run_code, status, expected in (
                (0, "killed", 0),
                (2, "killed", 1),
                (0, "survived", 1),
            ):
                run = SimpleNamespace(
                    returncode=run_code,
                    stdout=f"{'🎉' if status == 'killed' else '🙁'} {mutant_name}\n",
                )
                results = SimpleNamespace(returncode=0, stdout="")
                with patch.object(mutation_runner.subprocess, "run", side_effect=[run, results]), patch.object(
                    mutation_runner.subprocess,
                    "check_output",
                    return_value="a" * 40 + "\n",
                ), patch.object(mutation_runner.shutil, "rmtree") as rmtree:
                    result = mutation_runner.main(
                        [
                            "--manifest",
                            str(manifest_path),
                            "--output",
                            str(output),
                            "--log",
                            str(log),
                            "--fresh",
                        ]
                    )
                self.assertEqual(result, expected)
                rmtree.assert_called_once_with(mutation_runner.REPO_ROOT / "mutants", ignore_errors=True)
                self.assertEqual(json.loads(output.read_text())["run_exit_code"], run_code)
                self.assertEqual(log.read_text(), run.stdout)

    def test_main_falls_back_to_results_output_when_run_has_no_named_rows(self):
        target = self.manifest["targets"][0]
        manifest = {
            "schema_version": 2,
            "required_invariant_classes": [target["invariant_class"]],
            "targets": [target],
            "equivalent_mutants": [],
        }
        mutant_name = target["matcher"].removesuffix("*") + "__mutmut_1"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch.object(
                mutation_runner.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=0, stdout="no named run rows\n"),
                    SimpleNamespace(returncode=0, stdout=f"{mutant_name}: killed\n"),
                ],
            ), patch.object(
                mutation_runner.subprocess,
                "check_output",
                return_value="b" * 40 + "\n",
            ):
                result = mutation_runner.main(
                    [
                        "--manifest",
                        str(manifest_path),
                        "--output",
                        str(root / "evidence.json"),
                        "--log",
                        str(root / "mutation.log"),
                    ]
                )
        self.assertEqual(result, 0)

    def test_fresh_main_rejects_mutants_symlink_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (root / "mutants").symlink_to(outside, target_is_directory=True)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
            with patch.object(mutation_runner, "REPO_ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "unsafe_mutants_path"):
                    mutation_runner.main(
                        [
                            "--manifest",
                            str(manifest_path),
                            "--fresh",
                        ]
                    )


if __name__ == "__main__":
    unittest.main()
