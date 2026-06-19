import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable

PYTHON_SCRIPTS = (
    'scripts/backfill_direct_chats.py',
    'scripts/create_superadmin.py',
    'scripts/dev_admin.py',
    'scripts/free_deleted_user.py',
    'scripts/inspect_shared_sync_state.py',
    'scripts/report_bot_webapp_integration_matrix.py',
    'scripts/report_messenger_query_plans.py',
    'scripts/report_test_matrix.py',
    'scripts/run_observability_gate.py',
    'scripts/reset_sessions.py',
    'scripts/restore_default_commodities.py',
    'scripts/seed_shared_sync_tables.py',
    'scripts/test_invite.py',
    'scripts/test_session_realtime.py',
    'scripts/test_ws_e2e.py',
)

SHELL_SCRIPTS = (
    'scripts/init_offline_map.sh',
    'scripts/recover_cross_server_sync.sh',
    'scripts/setup_iran_nginx.sh',
    'scripts/setup_network.sh',
)


def run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class ScriptsSurfaceSmokeTests(unittest.TestCase):
    def test_python_scripts_compile(self):
        result = run_checked([PYTHON_BIN, '-m', 'py_compile', *PYTHON_SCRIPTS])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_shell_scripts_have_valid_bash_syntax(self):
        result = run_checked(['bash', '-n', *SHELL_SCRIPTS])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_report_test_matrix_cli_outputs_parseable_json(self):
        result = run_checked([PYTHON_BIN, 'scripts/report_test_matrix.py', '--json'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

        payload = json.loads(result.stdout)

        self.assertGreaterEqual(payload['summary']['python_unittest_files'], 200)
        self.assertGreaterEqual(payload['summary']['frontend_unit_files'], 10)
        self.assertGreaterEqual(payload['summary']['frontend_e2e_files'], 7)
        self.assertEqual(payload['summary']['manual_non_regression_tools'], 5)

    def test_bot_webapp_integration_matrix_cli_outputs_parseable_json(self):
        result = run_checked([PYTHON_BIN, 'scripts/report_bot_webapp_integration_matrix.py', '--json', '--check'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

        payload = json.loads(result.stdout)

        self.assertTrue(payload['matrix']['passed'])
        self.assertTrue(payload['matrix']['manual_signoff_required'])
        self.assertGreaterEqual(payload['matrix']['scenario_count'], 27)

    def test_messenger_query_plan_report_help_executes(self):
        result = run_checked([PYTHON_BIN, 'scripts/report_messenger_query_plans.py', '--help'])
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn('Run EXPLAIN ANALYZE for the core Messenger chat queries', result.stdout)


if __name__ == '__main__':
    unittest.main()
