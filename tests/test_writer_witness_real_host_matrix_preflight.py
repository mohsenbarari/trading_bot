import json
import stat
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import plan_writer_witness_real_host_matrix as preflight


class WriterWitnessRealHostMatrixPreflightTests(unittest.TestCase):
    def setUp(self):
        self.git_run = mock.patch.object(
            preflight.subprocess,
            "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, preflight.EXPECTED_BRANCH + "\n", ""),
                subprocess.CompletedProcess([], 0, "a" * 40 + "\n", ""),
                subprocess.CompletedProcess([], 0, "", ""),
            ],
        )

    def test_plan_is_dark_witness_only_and_never_authorizes_main_merge(self):
        with self.git_run:
            plan = preflight.build_plan(include_source_tests=False)
        self.assertEqual(plan["scope"], "dark_writer_witness_control_plane_only")
        self.assertFalse(plan["git"]["main_merge_authorized"])
        forbidden = "\n".join(plan["safety_contract"]["forbidden_before_matrix"])
        self.assertIn("merge main into", forbidden)
        self.assertIn("merge the feature branch into main", forbidden)
        self.assertIn("change Arvan", forbidden)

    def test_preflight_commands_are_read_only(self):
        commands = "\n".join(
            " ".join(spec.command)
            for spec in preflight.remote_check_specs(include_source_tests=False)
        )
        for forbidden in (
            "systemctl stop",
            "systemctl restart",
            "reboot",
            "nft add",
            "iptables",
            "date -s",
            "timedatectl set-time",
            "/v1/writer-witness/transitions",
            "WRITER_WITNESS_REQUIRED=true",
        ):
            self.assertNotIn(forbidden, commands)
        self.assertTrue(
            all(
                not spec.mutates_state
                for spec in preflight.remote_check_specs(include_source_tests=False)
            )
        )

    def test_catalog_contains_required_real_host_boundaries(self):
        catalog = preflight.scenario_catalog()
        ids = {item["id"] for item in catalog}
        self.assertEqual(ids, {f"RH-{number:03d}" for number in range(1, 13)})
        names = "\n".join(str(item["name"]) for item in catalog)
        self.assertIn("directional partition", names)
        self.assertIn("disk-full", names)
        self.assertIn("clock skew", names)
        self.assertIn("restore exact vacant baseline", names)

    def test_abort_contract_restores_faults_before_database_and_rechecks_webapps(self):
        contract = preflight.abort_and_rollback_contract()
        steps = contract["ordered_steps"]
        self.assertEqual([step["order"] for step in steps], list(range(1, 9)))
        ids = [step["step_id"] for step in steps]
        self.assertLess(
            ids.index("remove_scoped_network_faults"),
            ids.index("restore_vacant_baseline"),
        )
        self.assertLess(
            ids.index("remove_transient_credentials"),
            ids.index("restore_vacant_baseline"),
        )
        self.assertLess(
            ids.index("restore_vacant_baseline"),
            ids.index("verify_webapp_invariants"),
        )
        aborts = "\n".join(contract["abort_conditions"])
        self.assertIn("Arvan/CDN", aborts)
        self.assertIn("original rollback Witness", aborts)

    def test_cli_plan_writes_no_secret_material(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            with self.git_run:
                exit_code = preflight.main(
                    ["--mode", "plan", "--skip-source-tests", "--output", str(output)]
                )
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text())
            rendered = json.dumps(payload)
            self.assertNotIn("CLIENT_SECRET", rendered)
            self.assertNotIn("PRIVATE_KEY", rendered)
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_dirty_or_wrong_branch_blocks_before_remote_checks(self):
        plan = {
            "git": {"branch": "main", "clean": True},
            "status": "planned",
        }
        result, exit_code = preflight.execute_preflight(plan)
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "blocked_git_baseline")


if __name__ == "__main__":
    unittest.main()
