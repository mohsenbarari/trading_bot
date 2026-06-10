import unittest
from pathlib import Path

from scripts.check_deployment_surface_guard import run_guard


class DeploymentSurfaceGuardTests(unittest.TestCase):
    def test_repository_has_no_runtime_or_entrypoint_deployment_identity_leaks(self):
        repo_root = Path(__file__).resolve().parents[1]
        findings = run_guard(repo_root)

        self.assertEqual(
            findings,
            [],
            "\n".join(f"{finding.path}: {finding.detail}" for finding in findings),
        )


if __name__ == "__main__":
    unittest.main()
