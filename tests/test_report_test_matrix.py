import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / 'scripts' / 'report_test_matrix.py'


spec = importlib.util.spec_from_file_location('report_test_matrix', MODULE_PATH)
report_test_matrix = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = report_test_matrix
spec.loader.exec_module(report_test_matrix)


class ReportTestMatrixTests(unittest.TestCase):
    def test_build_summary_matches_repository_breadth_baseline(self):
        summary = report_test_matrix.build_summary(REPO_ROOT)

        self.assertGreaterEqual(summary.python_unittest_files, 200)
        self.assertGreaterEqual(summary.frontend_unit_files, 10)
        self.assertGreaterEqual(summary.frontend_e2e_files, 7)
        self.assertEqual(
            summary.manual_non_regression_tools,
            len(report_test_matrix.MANUAL_NON_REGRESSION_TOOLS),
        )

    def test_evaluate_breadth_reports_missing_thresholds(self):
        summary = report_test_matrix.TestMatrixSummary(
            python_unittest_files=199,
            frontend_unit_files=9,
            frontend_e2e_files=6,
            manual_non_regression_tools=3,
        )

        failures = report_test_matrix.evaluate_breadth(summary)

        self.assertEqual(
            failures,
            [
                'python_unittest_files=199 is below minimum 200',
                'frontend_unit_files=9 is below minimum 10',
                'frontend_e2e_files=6 is below minimum 7',
                'manual_non_regression_tools=3 is below minimum 4',
            ],
        )

    def test_diff_gate_passes_when_product_change_has_test_change(self):
        result = report_test_matrix.evaluate_diff_gate([
            'api/routers/chat.py',
            'tests/test_chat_router_direct_mutation.py',
        ])

        self.assertTrue(result.passed)
        self.assertIn('Product changes are accompanied', result.message)
        self.assertEqual(result.product_changes, ['api/routers/chat.py'])
        self.assertEqual(result.test_changes, ['tests/test_chat_router_direct_mutation.py'])

    def test_diff_gate_fails_when_product_change_has_no_test_change(self):
        result = report_test_matrix.evaluate_diff_gate([
            'frontend/src/views/MarketView.vue',
            'docs/AUTOMATED_TEST_CHECKLIST.md',
        ])

        self.assertFalse(result.passed)
        self.assertEqual(result.product_changes, ['frontend/src/views/MarketView.vue'])
        self.assertEqual(result.test_changes, [])

    def test_diff_gate_ignores_docs_only_changes(self):
        result = report_test_matrix.evaluate_diff_gate([
            'docs/AUTOMATED_TEST_CHECKLIST.md',
            '.github/copilot-instructions.md',
        ])

        self.assertTrue(result.passed)
        self.assertEqual(result.product_changes, [])
        self.assertEqual(result.test_changes, [])


if __name__ == '__main__':
    unittest.main()