import json
from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(
    str(ROOT / "scripts/generate_writer_witness_command_surfaces.py")
)


class WriterWitnessCommandSurfaceTests(unittest.TestCase):
    def test_generated_surface_separates_controller_and_each_remote_role(self):
        surface = MODULE["build_surface"]()
        self.assertGreaterEqual(len(surface["controller_process_calls"]), 10)
        roles = {
            item["host_role_expression"] for item in surface["remote_host_calls"]
        }
        self.assertTrue(
            {
                "'control'",
                "'matrix_witness'",
                "'rollback_witness'",
                "'webapp_fi'",
                "'webapp_ir'",
            }.issubset(roles)
        )
        calls = {item["call"] for item in surface["controller_process_calls"]}
        self.assertIn("subprocess.run", calls)
        self.assertIn("Controller.command", calls)

    def test_checked_in_review_artifact_matches_actual_ast_call_sites(self):
        observed = MODULE["build_review_artifact"]()
        expected = json.loads(
            (
                ROOT / "deploy/writer-witness/command-surfaces.generated.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(expected, observed)


if __name__ == "__main__":
    unittest.main()
