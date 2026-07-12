from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_stage9_diff_coverage as diff_checker
from scripts.check_stage9_diff_coverage import (
    _python_executable_lines,
    changed_lines_from_diff,
    check_backend,
    check_frontend,
    git_diff,
)


SAMPLE_DIFF = """diff --git a/frontend/src/sample.ts b/frontend/src/sample.ts
--- a/frontend/src/sample.ts
+++ b/frontend/src/sample.ts
@@ -1,1 +1,2 @@
 const oldValue = true
+const changedValue = condition ? 1 : 2
"""


class Stage9DiffCoverageTests(unittest.TestCase):
    def test_python_executable_lines_match_coverage_statement_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.py"
            source.write_text(
                "def value(\n"
                "    condition: bool,\n"
                ") -> int:\n"
                "    return (\n"
                "        1 if condition else 2\n"
                "    )\n",
                encoding="utf-8",
            )
            executable = _python_executable_lines(source)
        self.assertEqual(executable, {1, 4})

    def test_python_executable_lines_honor_repository_coverage_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sample.py"
            source.write_text(
                "def protocol_stub(): ...  # pragma: no cover\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(0)\n",
                encoding="utf-8",
            )
            executable = _python_executable_lines(source)
        self.assertEqual(executable, set())

    def test_diff_parser_returns_added_and_modified_new_lines(self):
        self.assertEqual(
            changed_lines_from_diff(SAMPLE_DIFF),
            {"frontend/src/sample.ts": {2}},
        )

    def test_frontend_gate_fails_for_uncovered_changed_branch(self):
        changed = {"frontend/src/sample.ts": {2}}
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}},
                "s": {"0": 1},
                "branchMap": {
                    "0": {
                        "locations": [
                            {"start": {"line": 2}, "end": {"line": 2}},
                            {"start": {"line": 2}, "end": {"line": 2}},
                        ]
                    }
                },
                "b": {"0": [1, 0]},
                "fnMap": {},
                "f": {},
            }
        }
        report = check_frontend(changed, coverage)
        self.assertFalse(report["passed"])
        self.assertIn("uncovered_branches:frontend/src/sample.ts:0:1", report["failures"])

    def test_frontend_gate_passes_when_all_changed_metrics_are_covered(self):
        changed = {"frontend/src/sample.ts": {2}}
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}},
                "s": {"0": 1},
                "branchMap": {"0": {"locations": [{"start": {"line": 2}, "end": {"line": 2}}]}},
                "b": {"0": [1]},
                "fnMap": {
                    "0": {"decl": {"start": {"line": 2}, "end": {"line": 2}}},
                    "1": {"loc": {"start": {"line": 8}, "end": {"line": 9}}},
                },
                "f": {"0": 1, "1": 0},
            }
        }
        report = check_frontend(changed, coverage)
        self.assertTrue(report["passed"])
        self.assertEqual(
            report["files"]["frontend/src/sample.ts"]["functions"]["total"],
            1,
        )

    def test_frontend_gate_fails_when_changed_line_has_no_coverage_map_entry(self):
        changed = {"frontend/src/sample.ts": {99}}
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 1}}},
                "s": {"0": 1},
                "branchMap": {},
                "b": {},
                "fnMap": {},
                "f": {},
            }
        }
        report = check_frontend(changed, coverage)
        self.assertFalse(report["passed"])
        self.assertIn("coverage_map_missing:frontend/src/sample.ts:99", report["failures"])

    def test_frontend_gate_accepts_only_explicit_unmapped_line_exclusion(self):
        changed = {"frontend/src/sample.ts": {99}}
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {}, "s": {}, "branchMap": {}, "b": {}, "fnMap": {}, "f": {}
            }
        }
        report = check_frontend(
            changed,
            coverage,
            {"frontend/src/sample.ts": {99: "type-only declaration"}},
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["files"]["frontend/src/sample.ts"]["excluded"][0]["line"], 99)

    def test_frontend_body_change_creates_function_obligation_from_full_location(self):
        changed = {"frontend/src/sample.ts": {5}}
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {"0": {"start": {"line": 5}, "end": {"line": 5}}},
                "s": {"0": 1},
                "branchMap": {},
                "b": {},
                "fnMap": {
                    "0": {
                        "decl": {"start": {"line": 2}, "end": {"line": 2}},
                        "loc": {"start": {"line": 2}, "end": {"line": 8}},
                    }
                },
                "f": {"0": 0},
            }
        }
        report = check_frontend(changed, coverage)
        self.assertFalse(report["passed"])
        self.assertIn("uncovered_functions:frontend/src/sample.ts:0", report["failures"])

    def test_backend_gate_fails_for_partial_changed_branch(self):
        xml = """<coverage><packages><package><classes>
        <class filename="core/sample.py"><lines>
        <line number="7" hits="1" branch="true" condition-coverage="50% (1/2)"/>
        </lines></class></classes></package></packages></coverage>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            with patch(
                "scripts.check_stage9_diff_coverage._python_executable_lines",
                return_value={7},
            ):
                report = check_backend({"core/sample.py": {7}}, path)
        self.assertFalse(report["passed"])
        self.assertIn("uncovered_branches:core/sample.py:7", report["failures"])

    def test_backend_gate_fails_when_executable_line_is_missing_from_coverage_map(self):
        xml = """<coverage><packages><package><classes>
        <class filename="scripts/sample.py"><lines>
        <line number="1" hits="1"/>
        </lines></class></classes></package></packages></coverage>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            with patch(
                "scripts.check_stage9_diff_coverage._python_executable_lines",
                return_value={99},
            ):
                report = check_backend({"scripts/sample.py": {99}}, path)
        self.assertFalse(report["passed"])
        self.assertIn("coverage_map_missing:scripts/sample.py:99", report["failures"])

    def test_backend_gate_ignores_changed_non_python_scripts(self):
        xml = "<coverage><packages/></coverage>"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            with patch(
                "scripts.check_stage9_diff_coverage._python_executable_lines"
            ) as executable_lines:
                report = check_backend({"scripts/deploy_staging.sh": {77}}, path)
        executable_lines.assert_not_called()
        self.assertTrue(report["passed"])

    def test_diff_parser_handles_context_deletions_and_multiple_files(self):
        diff = """diff --git a/core/a.py b/core/a.py
--- a/core/a.py
+++ b/core/a.py
@@ -1,3 +1,3 @@
 old
-removed
+added
 context
diff --git a/core/b.py b/core/b.py
--- a/core/b.py
+++ b/core/b.py
@@ -0,0 +2,2 @@
+first
+second
"""
        self.assertEqual(
            changed_lines_from_diff(diff),
            {"core/a.py": {2}, "core/b.py": {2, 3}},
        )

    def test_git_diff_uses_merge_base_and_requested_pathspec(self):
        completed = SimpleNamespace(stdout="diff-output")
        with patch.object(
            diff_checker.subprocess,
            "check_output",
            return_value="base-sha\n",
        ), patch.object(diff_checker.subprocess, "run", return_value=completed) as run:
            self.assertEqual(git_diff("main", "HEAD", "frontend/src"), "diff-output")
        self.assertEqual(run.call_args.args[0][-1], "frontend/src")
        self.assertIn("base-sha", run.call_args.args[0])

    def test_coverage_path_and_location_normalization_are_fail_closed(self):
        absolute = str(diff_checker.REPO_ROOT / "core/sample.py")
        self.assertEqual(diff_checker._normalized_coverage_path(absolute), "core/sample.py")
        self.assertEqual(
            diff_checker._normalized_coverage_path("/tmp/build/frontend/src/sample.ts"),
            "frontend/src/sample.ts",
        )
        self.assertEqual(diff_checker._normalized_coverage_path("./core/sample.py"), "core/sample.py")
        for value in (None, {}, {"start": {}, "end": {}}, {"start": {"line": "x"}, "end": {"line": 2}}):
            with self.subTest(value=value):
                self.assertEqual(diff_checker._location_lines(value), set())
        self.assertEqual(
            diff_checker._location_lines(
                {"start": {"line": 2}, "end": {"line": 4}}
            ),
            {2, 3, 4},
        )

    def test_exclusion_manifest_rejects_schema_shape_duplicates_and_invalid_lines(self):
        cases = [
            ({"schema_version": 2, "exclusions": []}, "unsupported_coverage_exclusion_schema"),
            ({"schema_version": 1, "exclusions": ["bad"]}, "invalid_coverage_exclusion"),
            (
                {"schema_version": 1, "exclusions": [{"path": "x", "lines": [], "reason": "why"}]},
                "incomplete_coverage_exclusion",
            ),
            (
                {"schema_version": 1, "exclusions": [{"path": "x", "lines": [0], "reason": "why"}]},
                "invalid_coverage_exclusion_line",
            ),
            (
                {
                    "schema_version": 1,
                    "exclusions": [
                        {"path": "x", "lines": [1], "reason": "a"},
                        {"path": "x", "lines": [2], "reason": "b"},
                    ],
                },
                "duplicate_coverage_exclusion_path",
            ),
        ]
        for payload, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                diff_checker._validated_exclusions(payload)
        self.assertEqual(
            diff_checker._validated_exclusions(
                {
                    "schema_version": 1,
                    "exclusions": [{"path": "x", "lines": [2], "reason": "type-only"}],
                }
            ),
            {"x": {2: "type-only"}},
        )

    def test_frontend_gate_ignores_tests_and_rejects_missing_or_stale_maps(self):
        changed = {
            "frontend/src/sample.test.ts": {1},
            "frontend/src/sample.spec.ts": {1},
            "frontend/src/__tests__/sample.ts": {1},
            "frontend/src/missing.ts": {2},
            "README.md": {1},
        }
        report = check_frontend(changed, {"ignored": []})
        self.assertEqual(report["failures"], ["coverage_file_missing:frontend/src/missing.ts"])

        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {},
                "s": {},
                "branchMap": {"bad": "invalid", "short": {"locations": [{}]}},
                "b": {"short": []},
                "fnMap": {"bad": "invalid"},
                "f": {},
            }
        }
        report = check_frontend(
            {"frontend/src/sample.ts": {9}},
            coverage,
            {"frontend/src/sample.ts": {8: "stale", 9: "type-only"}},
        )
        self.assertIn("stale_coverage_exclusion:frontend/src/sample.ts:8", report["failures"])
        self.assertEqual(report["files"]["frontend/src/sample.ts"]["excluded"][0]["line"], 9)

        malformed_maps = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": [],
                "s": {},
                "branchMap": {"bad-shape": {"locations": {}}},
                "b": {"bad-shape": {}},
                "fnMap": [],
                "f": {},
            }
        }
        report = check_frontend({"frontend/src/sample.ts": {4}}, malformed_maps)
        self.assertIn("coverage_map_missing:frontend/src/sample.ts:4", report["failures"])

        malformed_branch_map = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {},
                "s": {},
                "branchMap": [],
                "b": {},
                "fnMap": {},
                "f": {},
            }
        }
        report = check_frontend(
            {"frontend/src/sample.ts": {5}}, malformed_branch_map
        )
        self.assertIn("coverage_map_missing:frontend/src/sample.ts:5", report["failures"])

    def test_frontend_gate_counts_uncovered_lines_statements_functions_and_short_branches(self):
        coverage = {
            "/repo/frontend/src/sample.ts": {
                "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 3}}},
                "s": {"0": 0},
                "branchMap": {
                    "0": {
                        "locations": [
                            {"start": {"line": 2}, "end": {"line": 2}},
                            {"start": {"line": 2}, "end": {"line": 2}},
                        ]
                    }
                },
                "b": {"0": [1]},
                "fnMap": {"0": {"loc": {"start": {"line": 2}, "end": {"line": 3}}}},
                "f": {"0": 0},
            }
        }
        report = check_frontend({"frontend/src/sample.ts": {2, 3}}, coverage)
        self.assertFalse(report["passed"])
        self.assertEqual(report["files"]["frontend/src/sample.ts"]["lines"]["uncovered"], [2, 3])
        self.assertIn("0:1", report["files"]["frontend/src/sample.ts"]["branches"]["uncovered"])
        self.assertEqual(report["files"]["frontend/src/sample.ts"]["functions"]["uncovered"], ["0"])

    def test_backend_gate_rejects_missing_file_uncovered_line_and_stale_exclusion(self):
        xml = """<coverage><packages><package><classes>
        <class filename="core/present.py"><lines>
        <line number="7" hits="0"/>
        </lines></class></classes></package></packages></coverage>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            with patch.object(
                diff_checker,
                "_python_executable_lines",
                side_effect=lambda source: {7} if source.name == "present.py" else {9},
            ):
                report = check_backend(
                    {"core/present.py": {7}, "core/missing.py": {9}, "docs/x.py": {1}},
                    path,
                    {"core/present.py": {8: "stale"}},
                )
        self.assertIn("uncovered_lines:core/present.py:7", report["failures"])
        self.assertIn("coverage_file_missing:core/missing.py", report["failures"])
        self.assertIn("stale_coverage_exclusion:core/present.py:8", report["failures"])

        full_branch_xml = """<coverage><packages><package><classes>
        <class filename="core/present.py"><lines>
        <line number="7" hits="1" branch="true" condition-coverage="100% (2/2)"/>
        </lines></class></classes></package></packages></coverage>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(full_branch_xml, encoding="utf-8")
            with patch.object(
                diff_checker,
                "_python_executable_lines",
                side_effect=lambda source: set() if source.name == "empty.py" else {7},
            ):
                report = check_backend(
                    {"core/present.py": {7}, "core/empty.py": {1}},
                    path,
                )
        self.assertTrue(report["passed"])

    def test_load_diff_selects_file_or_git_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "change.diff"
            path.write_text(SAMPLE_DIFF, encoding="utf-8")
            args = SimpleNamespace(diff_file=str(path), base_ref="main", head_ref="HEAD")
            self.assertEqual(
                diff_checker._load_diff(args, "."),
                {"frontend/src/sample.ts": {2}},
            )
        args = SimpleNamespace(diff_file=None, base_ref="main", head_ref="HEAD")
        with patch.object(diff_checker, "git_diff", return_value=SAMPLE_DIFF):
            self.assertEqual(
                diff_checker._load_diff(args, "frontend/src"),
                {"frontend/src/sample.ts": {2}},
            )

    def test_main_writes_frontend_and_backend_reports_with_current_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exclusions = root / "exclusions.json"
            exclusions.write_text('{"schema_version":1,"exclusions":[]}', encoding="utf-8")
            diff = root / "change.diff"
            diff.write_text(SAMPLE_DIFF, encoding="utf-8")
            frontend = root / "frontend.json"
            frontend.write_text(
                json.dumps(
                    {
                        "/repo/frontend/src/sample.ts": {
                            "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}},
                            "s": {"0": 1},
                            "branchMap": {},
                            "b": {},
                            "fnMap": {},
                            "f": {},
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "frontend-report.json"
            with patch.object(
                diff_checker.subprocess,
                "check_output",
                return_value="f" * 40 + "\n",
            ):
                result = diff_checker.main(
                    [
                        "frontend",
                        "--diff-file",
                        str(diff),
                        "--coverage",
                        str(frontend),
                        "--output",
                        str(output),
                        "--exclusions",
                        str(exclusions),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.read_text())["commit"], "f" * 40)

            failing_frontend = root / "failing-frontend.json"
            failing_frontend.write_text("{}", encoding="utf-8")
            with patch.object(
                diff_checker.subprocess,
                "check_output",
                return_value="f" * 40 + "\n",
            ):
                result = diff_checker.main(
                    [
                        "frontend",
                        "--diff-file",
                        str(diff),
                        "--coverage",
                        str(failing_frontend),
                        "--output",
                        str(root / "failed-report.json"),
                        "--exclusions",
                        str(exclusions),
                    ]
                )
            self.assertEqual(result, 1)

            backend_diff = root / "backend.diff"
            backend_diff.write_text(
                "diff --git a/core/sample.py b/core/sample.py\n"
                "--- a/core/sample.py\n+++ b/core/sample.py\n"
                "@@ -0,0 +1 @@\n+value = 1\n",
                encoding="utf-8",
            )
            backend_xml = root / "backend.xml"
            backend_xml.write_text(
                '<coverage><packages><package><classes><class filename="core/sample.py"><lines>'
                '<line number="1" hits="1"/></lines></class></classes></package></packages></coverage>',
                encoding="utf-8",
            )
            with patch.object(diff_checker, "_python_executable_lines", return_value={1}), patch.object(
                diff_checker.subprocess,
                "check_output",
                return_value="a" * 40 + "\n",
            ):
                result = diff_checker.main(
                    [
                        "backend",
                        "--diff-file",
                        str(backend_diff),
                        "--coverage",
                        str(backend_xml),
                        "--output",
                        str(root / "backend-report.json"),
                        "--exclusions",
                        str(exclusions),
                    ]
                )
            self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
