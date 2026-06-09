import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObservabilityWorkflowTests(unittest.TestCase):
    def test_merge_gate_runs_focused_observability_job(self):
        workflow = (ROOT / ".github/workflows/merge-gate.yml").read_text(encoding="utf-8")

        self.assertIn("observability-gate:", workflow)
        self.assertIn("make observability-gate", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("needs:\n      - repository-governance\n      - observability-gate", workflow)

    def test_pre_release_gate_runs_focused_observability_job(self):
        workflow = (ROOT / ".github/workflows/pre-release-gate.yml").read_text(encoding="utf-8")

        self.assertIn("observability-gate:", workflow)
        self.assertIn("make observability-gate", workflow)
        self.assertIn("needs:\n      - repository-governance\n      - observability-gate", workflow)


if __name__ == "__main__":
    unittest.main()
