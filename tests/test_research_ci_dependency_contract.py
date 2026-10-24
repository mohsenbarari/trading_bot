"""Read-only CI contracts; no science imports, dependency install or training."""
import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_TESTS = (
    "tests/test_coin_intelligence_offer_condition_training.py",
    "tests/test_coin_offer_condition_calibration.py",
    "tests/test_coin_offer_condition_owner_review.py",
)


class ResearchCiDependencyContracts(unittest.TestCase):
    def test_research_extras_pin_only_the_separate_python311_test_closure(self):
        rows = [
            line for line in (ROOT / "requirements-test-research.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertEqual(dict(line.split("==", 1) for line in rows), {
            "joblib": "1.6.0", "numpy": "2.4.6", "scikit-learn": "1.9.1",
            "scipy": "1.17.1", "threadpoolctl": "3.7.0", "narwhals": "2.27.1",
            "cloudpickle": "3.1.2",
        })
        self.assertEqual(len(rows), 7)
        production = (ROOT / "requirements.txt").read_text()
        self.assertNotIn("requirements-test-research.txt", production)
        for name in ("joblib", "numpy", "scikit-learn", "scipy"):
            self.assertNotRegex(production, rf"(?im)^\s*{re.escape(name)}\s*[<=>~!]")

    def test_coverage_installs_test_wheels_not_legacy_model_training_dependencies(self):
        workflow = (ROOT / ".github/workflows/coverage-report.yml").read_text()
        step = workflow.split("      - name: Install backend coverage dependencies\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        self.assertIn(
            "python3 -m pip install --only-binary=:all: -r requirements-test-research.txt",
            step,
        )
        self.assertNotIn("apps/coin_rate_estimator/requirements-research.txt", step)
        for recipe in ("Dockerfile", "Dockerfile.iran", "deploy/market-data/Dockerfile"):
            self.assertNotIn("requirements-test-research.txt", (ROOT / recipe).read_text())

    def test_all_eight_synthetic_functions_are_explicit_required_pytest_targets(self):
        workflow = (ROOT / ".github/workflows/coverage-report.yml").read_text()
        step = workflow.split("      - name: Run backend coverage suite\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        pytest_command = step.split("python3 -m coverage run --branch --parallel-mode -m pytest -q", 1)[1]
        pytest_command = pytest_command.split("2>&1 | tee -a tmp/backend-coverage.log", 1)[0]
        count = 0
        for relative in RESEARCH_TESTS:
            self.assertIn(relative, pytest_command)
            tree = ast.parse((ROOT / relative).read_text())
            functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name.startswith("test_")]
            self.assertTrue(functions)
            self.assertTrue(all(not node.decorator_list for node in functions))
            count += len(functions)
        self.assertEqual(count, 8)
        self.assertIn("pytest_status=${PIPESTATUS[0]}", step)
        self.assertIn('status=$pytest_status', step)
        self.assertNotIn("--ignore", pytest_command)


if __name__ == "__main__":
    unittest.main()
