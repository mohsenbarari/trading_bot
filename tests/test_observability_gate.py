import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_observability_gate.py"


spec = importlib.util.spec_from_file_location("run_observability_gate", MODULE_PATH)
run_observability_gate = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = run_observability_gate
spec.loader.exec_module(run_observability_gate)


class ObservabilityGateScriptTests(unittest.TestCase):
    def test_observability_path_classifier_matches_core_surfaces(self):
        self.assertTrue(run_observability_gate.is_observability_path("core/request_logging.py"))
        self.assertTrue(run_observability_gate.is_observability_path("observability/promtail/promtail-config.yml"))
        self.assertTrue(run_observability_gate.is_observability_path("docs/OBSERVABILITY_ALERTS.md"))
        self.assertFalse(run_observability_gate.is_observability_path("frontend/src/views/MessengerView.vue"))

    def test_main_prints_diff_note_and_runs_suite(self):
        with patch(
            "run_observability_gate.get_changed_paths",
            return_value=["core/request_logging.py", "frontend/src/views/App.vue"],
        ), patch("run_observability_gate.run_observability_tests", return_value=0) as run_suite, patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            exit_code = run_observability_gate.main(["--base-ref", "base", "--head-ref", "head"])

        self.assertEqual(exit_code, 0)
        self.assertIn("observability-related changes detected", stdout.getvalue())
        self.assertIn("core/request_logging.py", stdout.getvalue())
        run_suite.assert_called_once()

    def test_main_returns_error_when_git_diff_fails(self):
        with patch(
            "run_observability_gate.get_changed_paths",
            side_effect=RuntimeError("git diff failed"),
        ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            exit_code = run_observability_gate.main(["--base-ref", "base"])

        self.assertEqual(exit_code, 2)
        self.assertIn("git diff failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
