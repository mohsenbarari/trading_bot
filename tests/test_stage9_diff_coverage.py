from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_stage9_diff_coverage import (
    changed_lines_from_diff,
    check_backend,
    check_frontend,
)


SAMPLE_DIFF = """diff --git a/frontend/src/sample.ts b/frontend/src/sample.ts
--- a/frontend/src/sample.ts
+++ b/frontend/src/sample.ts
@@ -1,1 +1,2 @@
 const oldValue = true
+const changedValue = condition ? 1 : 2
"""


class Stage9DiffCoverageTests(unittest.TestCase):
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
                "fnMap": {"0": {"decl": {"start": {"line": 2}, "end": {"line": 2}}}},
                "f": {"0": 1},
            }
        }
        self.assertTrue(check_frontend(changed, coverage)["passed"])

    def test_backend_gate_fails_for_partial_changed_branch(self):
        xml = """<coverage><packages><package><classes>
        <class filename="core/sample.py"><lines>
        <line number="7" hits="1" branch="true" condition-coverage="50% (1/2)"/>
        </lines></class></classes></package></packages></coverage>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coverage.xml"
            path.write_text(xml, encoding="utf-8")
            report = check_backend({"core/sample.py": {7}}, path)
        self.assertFalse(report["passed"])
        self.assertIn("uncovered_branches:core/sample.py:7", report["failures"])


if __name__ == "__main__":
    unittest.main()
