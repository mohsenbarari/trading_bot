import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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
                'manual_non_regression_tools=3 is below minimum 5',
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

    def test_path_classifiers_cover_root_product_frontend_specs_and_non_product(self):
        self.assertTrue(report_test_matrix.is_product_path('Dockerfile'))
        self.assertTrue(report_test_matrix.is_product_path('scripts/report_test_matrix.py'))
        self.assertTrue(report_test_matrix.is_product_path('frontend/src/components/ChatView.vue'))
        self.assertFalse(report_test_matrix.is_product_path('frontend/src/components/ChatView.test.ts'))
        self.assertTrue(report_test_matrix.is_test_path('frontend/e2e/direct-chat.spec.ts'))
        self.assertFalse(report_test_matrix.is_product_path('docs/notes.md'))

    def test_build_report_payload_includes_diff_gate_when_available(self):
        summary = report_test_matrix.TestMatrixSummary(
            python_unittest_files=201,
            frontend_unit_files=11,
            frontend_e2e_files=8,
            manual_non_regression_tools=4,
        )
        diff = report_test_matrix.DiffGateResult(
            passed=False,
            message='missing test',
            product_changes=['api/routers/chat.py'],
            test_changes=[],
        )

        payload = report_test_matrix.build_report_payload(
            summary,
            ['one failure'],
            diff,
            'base',
            'head',
        )

        self.assertEqual(payload['summary']['python_unittest_files'], 201)
        self.assertEqual(payload['breadth_failures'], ['one failure'])
        self.assertEqual(payload['diff_gate']['base_ref'], 'base')
        self.assertFalse(payload['diff_gate']['passed'])

    def test_get_changed_paths_returns_clean_lines_and_raises_on_git_failure(self):
        ok_result = report_test_matrix.subprocess.CompletedProcess(
            args=['git'],
            returncode=0,
            stdout='api/routers/chat.py\n\n tests/test_chat.py \n',
            stderr='',
        )
        with patch('report_test_matrix.subprocess.run', return_value=ok_result) as run_mock:
            paths = report_test_matrix.get_changed_paths('base', 'head', REPO_ROOT)
        self.assertEqual(paths, ['api/routers/chat.py', 'tests/test_chat.py'])
        run_mock.assert_called_once()

        failed_result = report_test_matrix.subprocess.CompletedProcess(
            args=['git'],
            returncode=1,
            stdout='',
            stderr='bad ref',
        )
        with patch('report_test_matrix.subprocess.run', return_value=failed_result):
            with self.assertRaisesRegex(RuntimeError, 'bad ref'):
                report_test_matrix.get_changed_paths('bad', 'head', REPO_ROOT)

    def test_main_json_and_plaintext_success_and_failure_paths(self):
        summary = report_test_matrix.TestMatrixSummary(
            python_unittest_files=250,
            frontend_unit_files=20,
            frontend_e2e_files=10,
            manual_non_regression_tools=5,
        )

        with patch('report_test_matrix.build_summary', return_value=summary), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            exit_code = report_test_matrix.main(['--json', '--check-breadth'])
        self.assertEqual(exit_code, 0)
        self.assertIn('"breadth_failures": []', stdout.getvalue())

        low_summary = report_test_matrix.TestMatrixSummary(
            python_unittest_files=1,
            frontend_unit_files=1,
            frontend_e2e_files=1,
            manual_non_regression_tools=0,
        )
        with patch('report_test_matrix.build_summary', return_value=low_summary), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            exit_code = report_test_matrix.main(['--check-breadth'])
        self.assertEqual(exit_code, 1)
        self.assertIn('Breadth gate: FAILED', stdout.getvalue())

        with patch('report_test_matrix.build_summary', return_value=summary), patch(
            'report_test_matrix.get_changed_paths',
            side_effect=RuntimeError('git diff failed'),
        ), patch('sys.stderr', new_callable=io.StringIO) as stderr:
            exit_code = report_test_matrix.main(['--check-diff'])
        self.assertEqual(exit_code, 2)
        self.assertIn('git diff failed', stderr.getvalue())

        with patch('report_test_matrix.build_summary', return_value=summary), patch(
            'report_test_matrix.get_changed_paths',
            return_value=['api/routers/chat.py'],
        ), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            exit_code = report_test_matrix.main(['--check-diff', '--base-ref', 'base', '--head-ref', 'head'])
        self.assertEqual(exit_code, 1)
        self.assertIn('Diff gate (base..head): FAILED', stdout.getvalue())

        with patch('report_test_matrix.build_summary', return_value=summary), patch(
            'report_test_matrix.get_changed_paths',
            return_value=['api/routers/chat.py', 'tests/test_chat.py'],
        ), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            exit_code = report_test_matrix.main(['--check-diff'])
        self.assertEqual(exit_code, 0)
        self.assertIn('Product changes: api/routers/chat.py', stdout.getvalue())
        self.assertIn('Test changes: tests/test_chat.py', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()