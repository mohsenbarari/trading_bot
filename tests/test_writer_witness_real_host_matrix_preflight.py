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

    def test_matrix_preflight_attests_every_release_entry_against_exact_build_manifest(self):
        expected_manifest_sha256 = "b" * 64
        command = next(
            " ".join(spec.command)
            for spec in preflight.remote_check_specs(
                include_source_tests=False,
                expected_commit="a" * 40,
                expected_release_manifest_sha256=expected_manifest_sha256,
            )
            if spec.check_id == "matrix_witness_dark_baseline"
        )
        self.assertIn("verify_writer_witness_release.py", command)
        self.assertIn("--expected-manifest-sha256", command)
        self.assertIn("--expected-uid 0", command)
        self.assertIn("--expected-gid 0", command)
        self.assertIn(expected_manifest_sha256, command)
        self.assertIn("$release/release-manifest.json", command)
        self.assertIn("writer-witness-offsite-backup", command)
        self.assertIn("writer-witness-s3-put", command)
        self.assertIn("writer-witness-offsite-backup.timer", command)

    def test_campaign_helper_is_attested_before_execution_and_absence_rejects_symlinks(self):
        active_command = next(
            " ".join(spec.command)
            for spec in preflight.remote_check_specs(
                include_source_tests=False,
                expected_commit="a" * 40,
                expected_release_manifest_sha256="b" * 64,
                expected_active_campaign_tag="wwm_0123456789ab",
                expected_active_campaign_scenario="RH-001",
                expected_active_campaign_not_after="2099-01-01T00:00:00Z",
            )
            if spec.check_id == "matrix_witness_dark_baseline"
        )
        helper_attestation = "installed_artifacts_attested=yes"
        helper_execution = (
            "/usr/local/sbin/writer-witness-matrix-campaign assert"
        )
        self.assertLess(
            active_command.index(helper_attestation),
            active_command.index(helper_execution),
        )

        absent_command = next(
            " ".join(spec.command)
            for spec in preflight.remote_check_specs(
                include_source_tests=False,
                expected_commit="a" * 40,
                expected_release_manifest_sha256="b" * 64,
            )
            if spec.check_id == "matrix_witness_dark_baseline"
        )
        self.assertIn(
            "test ! -L /var/lib/trading-bot-witness/matrix-campaign/active.json",
            absent_command,
        )
        self.assertIn(
            "test ! -L /var/lib/trading-bot-witness/restore-state/active.env",
            absent_command,
        )
        self.assertIn("! -name '.runtime.lock'", absent_command)
        self.assertIn('stat -c %h "$rotation_root/.runtime.lock"', absent_command)
        self.assertIn('flock -n "$rotation_root/.runtime.lock"', absent_command)
        self.assertIn("verify_writer_witness_runtime.py", absent_command)
        self.assertIn("PIP_CONFIG_FILE=/dev/null", absent_command)
        self.assertIn("runpy.run_module", absent_command)
        self.assertIn("'check'", absent_command)
        self.assertNotIn(" -m pip check", absent_command)
        self.assertIn(".active.*.env", absent_command)
        self.assertNotIn("-type f -name '.campaign-write.*.tmp'", absent_command)

    def test_rollback_helper_and_network_config_are_attested_before_use(self):
        specs = preflight.remote_check_specs(
            include_source_tests=False,
            expected_commit="a" * 40,
            expected_release_manifest_sha256="b" * 64,
        )
        matrix_command = next(
            " ".join(spec.command)
            for spec in specs
            if spec.check_id == "matrix_witness_dark_baseline"
        )
        rollback_command = next(
            " ".join(spec.command)
            for spec in specs
            if spec.check_id == "rollback_witness_baseline"
        )
        self.assertLess(
            matrix_command.index("/etc/nginx/sites-available/writer-witness"),
            matrix_command.index("nginx -T"),
        )
        self.assertIn("offsite_upload_attested", matrix_command)
        self.assertIn("wc -l", matrix_command)
        self.assertLess(
            rollback_command.index(preflight.ROLLBACK_STATE_MANIFEST_SHA256),
            rollback_command.index("manifest=$(/usr/local/sbin/writer-witness-state-manifest)"),
        )

    def test_controller_provisioner_is_pinned_and_in_the_source_gate(self):
        self.assertIn(
            "scripts/provision_writer_witness_matrix_controller.py",
            preflight.PINNED_SOURCE_PATHS,
        )
        source_gate = (preflight.ROOT / "scripts/run_writer_witness_preflight_source_gate.sh").read_text()
        self.assertIn("tests.test_writer_witness_matrix_controller_provision", source_gate)
        self.assertIn("tests.test_verify_writer_witness_runtime", source_gate)
        self.assertIn("tests.test_verify_writer_witness_runtime_provenance", source_gate)
        self.assertIn("tests.test_verify_writer_witness_process_maps", source_gate)
        self.assertIn("tests.test_verify_writer_witness_nftables", source_gate)
        self.assertIn("tests.test_verify_writer_witness_release", source_gate)
        self.assertIn("tests.test_render_writer_witness_credentials", source_gate)
        self.assertIn("WRITER_WITNESS_SOURCE_GATE_HERMETIC", source_gate)
        self.assertIn("exec /usr/bin/env -i", source_gate)
        self.assertIn("python3 -I", source_gate)
        self.assertIn("bash -n", source_gate)
        self.assertIn("writer-witness-activation.py", source_gate)

    def test_runtime_provenance_and_effective_nftables_are_release_bound(self):
        command = next(
            " ".join(spec.command)
            for spec in preflight.remote_check_specs(
                include_source_tests=False,
                expected_commit="a" * 40,
                expected_release_manifest_sha256="b" * 64,
            )
            if spec.check_id == "matrix_witness_dark_baseline"
        )
        runtime = preflight.python_runtime_binding()
        policy = preflight.nftables_policy_binding()
        self.assertIn("verify_writer_witness_runtime_provenance.py", command)
        self.assertIn("runtime-provenance.json", command)
        self.assertIn(str(runtime["python_version"]), command)
        self.assertIn(str(runtime["python_sha256"]), command)
        self.assertIn(str(runtime["system_runtime_manifest_sha256"]), command)
        self.assertIn("--system-only", command)
        self.assertIn("--system-runtime-manifest", command)
        self.assertIn("--expected-system-runtime-manifest-sha256", command)
        self.assertIn("env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin", command)
        self.assertIn("-I -S -B -X utf8 -X pycache_prefix=/dev/null", command)
        self.assertIn("runtime_provenance_attested", command)
        self.assertIn("verify_writer_witness_process_maps.py", command)
        self.assertIn("process_maps_attested", command)
        self.assertIn("LD_AUDIT", command)
        self.assertLess(command.index("--system-only"), command.index("runpy.run_module"))
        self.assertIn("nft -j list ruleset", command)
        self.assertIn("verify_writer_witness_nftables.py", command)
        self.assertIn(str(policy["policy_sha256"]), command)
        self.assertIn("nftables_policy_attested", command)
        self.assertLess(
            command.index("verify_writer_witness_nftables.py"),
            command.index("network_policy_semantics_match=yes"),
        )

    def test_preflight_rejects_missing_release_attestation_marker(self):
        expected_manifest_sha256 = "b" * 64
        certificate_sha256 = "c" * 64
        state_manifest_sha256 = "d" * 64
        specs_and_stdout = (
            (
                preflight.CheckSpec("source_regression_gate", ("true",), "control"),
                '{"guarded_postgres_tests":5,"skipped":0,"four_database_drill":true}\n',
            ),
            (
                preflight.CheckSpec("webapp_fi_baseline", ("true",), "webapp_fi"),
                f"witness_cert_sha256={certificate_sha256}\n",
            ),
            (
                preflight.CheckSpec("webapp_ir_standby_baseline", ("true",), "webapp_ir"),
                f"witness_cert_sha256={certificate_sha256}\n",
            ),
            (
                preflight.CheckSpec("matrix_witness_dark_baseline", ("true",), "matrix_witness"),
                (
                    f"cert_sha256={certificate_sha256}\n"
                    f"manifest_sha256={state_manifest_sha256}\n"
                    f"release_manifest_sha256={expected_manifest_sha256}\n"
                    "release_manifest_entries=42\n"
                ),
            ),
            (
                preflight.CheckSpec("rollback_witness_baseline", ("true",), "rollback_witness"),
                f"manifest_sha256={state_manifest_sha256}\n",
            ),
        )
        plan = {
            "git": {
                "branch": preflight.EXPECTED_BRANCH,
                "clean": True,
                "head": "a" * 40,
                "expected_commit": "a" * 40,
            },
            "run_bundle": {
                "source_sha256": preflight.source_manifest(),
                "witness_release_manifest_sha256": expected_manifest_sha256,
            },
        }
        completed = [
            subprocess.CompletedProcess(spec.command, 0, stdout, "")
            for spec, stdout in specs_and_stdout
        ]
        with (
            mock.patch.object(
                preflight,
                "witness_release_manifest_sha256",
                return_value=expected_manifest_sha256,
            ),
            mock.patch.object(
                preflight,
                "remote_check_specs",
                return_value=[item[0] for item in specs_and_stdout],
            ),
            mock.patch.object(preflight.subprocess, "run", side_effect=completed),
        ):
            result, exit_code = preflight.execute_preflight(plan)
        self.assertEqual(exit_code, 1)
        self.assertIn(
            "matrix_witness_release_manifest_attestation_missing",
            result["failed_checks"],
        )

    def test_preflight_blocks_a_release_manifest_bundle_not_rebuilt_from_source(self):
        plan = {
            "git": {
                "branch": preflight.EXPECTED_BRANCH,
                "clean": True,
                "head": "a" * 40,
                "expected_commit": "a" * 40,
            },
            "run_bundle": {
                "source_sha256": preflight.source_manifest(),
                "witness_release_manifest_sha256": "a" * 64,
            },
        }
        with mock.patch.object(
            preflight,
            "witness_release_manifest_sha256",
            return_value="b" * 64,
        ):
            result, exit_code = preflight.execute_preflight(plan)
        self.assertEqual(exit_code, 2)
        self.assertEqual(result["status"], "blocked_release_bundle_drift")

    def test_catalog_contains_required_real_host_boundaries(self):
        catalog = preflight.scenario_catalog()
        ids = {item["id"] for item in catalog}
        self.assertEqual(ids, {f"RH-{number:03d}" for number in range(1, 13)})
        names = "\n".join(str(item["name"]) for item in catalog)
        self.assertIn("directional partition", names)
        self.assertIn("disk-full", names)
        self.assertIn("clock skew", names)
        self.assertIn("restore exact vacant baseline", names)

    def test_abort_contract_stops_requesters_and_retains_evidence_before_reconnect(self):
        contract = preflight.abort_and_rollback_contract()
        steps = contract["ordered_steps"]
        self.assertEqual([step["order"] for step in steps], list(range(1, 10)))
        ids = [step["step_id"] for step in steps]
        self.assertLess(ids.index("stop_and_join_requesters"), ids.index("remove_scoped_network_faults"))
        self.assertLess(ids.index("revoke_transient_capability"), ids.index("remove_scoped_network_faults"))
        self.assertLess(ids.index("retain_pre_recovery_evidence"), ids.index("restore_vacant_baseline"))
        self.assertLess(
            ids.index("restore_vacant_baseline"),
            ids.index("verify_complete_baseline"),
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
