import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import two_site_pre_review_pytest_gate as gate
from scripts.two_site_agent_sandbox import sandbox_argv


class PreReviewPytestGateTests(unittest.TestCase):
    def setUp(self):
        gate.reset_gate()

    def test_direct_hook_zero_pass_fails(self):
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
        gate.pytest_sessionfinish(session, pytest.ExitCode.OK)
        self.assertEqual(session.exitstatus, pytest.ExitCode.TESTS_FAILED)

    def test_direct_hook_one_pass_succeeds(self):
        gate.pytest_runtest_logreport(SimpleNamespace(when="call", passed=True))
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
        gate.pytest_sessionfinish(session, pytest.ExitCode.OK)
        self.assertEqual(session.exitstatus, pytest.ExitCode.OK)

    def test_direct_hook_preexisting_error_preserved(self):
        session = SimpleNamespace(exitstatus=pytest.ExitCode.INTERRUPTED)
        gate.pytest_sessionfinish(session, pytest.ExitCode.INTERRUPTED)
        self.assertEqual(session.exitstatus, pytest.ExitCode.INTERRUPTED)

    def test_direct_hook_skipped_report_fails(self):
        gate.pytest_runtest_logreport(SimpleNamespace(when="call", passed=False, skipped=True))
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
        gate.pytest_sessionfinish(session, pytest.ExitCode.OK)
        self.assertEqual(session.exitstatus, pytest.ExitCode.TESTS_FAILED)

    def test_direct_hook_xfail_xpass_setup_teardown_excluded(self):
        gate.pytest_runtest_logreport(SimpleNamespace(when="call", passed=False, skipped=True, wasxfail="xfail"))
        gate.pytest_runtest_logreport(SimpleNamespace(when="call", passed=True, wasxfail="xpass"))
        gate.pytest_runtest_logreport(SimpleNamespace(when="setup", passed=True))
        gate.pytest_runtest_logreport(SimpleNamespace(when="teardown", passed=True))
        session = SimpleNamespace(exitstatus=pytest.ExitCode.OK)
        gate.pytest_sessionfinish(session, pytest.ExitCode.OK)
        self.assertEqual(session.exitstatus, pytest.ExitCode.TESTS_FAILED)

    def test_subprocess_all_skipped_fails_gate(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_skip.py"
            test_file.write_text("import pytest\n@pytest.mark.skip\ndef test_s(): pass\n")
            env = dict(os.environ, PYTHONPATH=str(root))
            cmd = [
                sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "-p", "scripts.two_site_pre_review_pytest_gate", str(test_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, int(pytest.ExitCode.TESTS_FAILED))
            self.assertIn("1 skipped", proc.stdout)

    def test_sandbox_argv_includes_gate_plugin(self):
        argv = sandbox_argv(Path("/work"), ["tests/test_a.py"])
        self.assertIn("scripts.two_site_pre_review_pytest_gate", argv)
        idx = argv.index("scripts.two_site_pre_review_pytest_gate")
        self.assertEqual(argv[idx - 1], "-p")


if __name__ == "__main__":
    unittest.main()
