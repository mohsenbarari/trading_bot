import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts import run_writer_witness_real_host_matrix as runner
from scripts import writer_witness_matrix_client as client


HEAD = "a" * 40


def passing_preflight():
    matrix = {
        "state": "webapp:0:vacant",
        "receipts": "0",
        "manifest_sha256": "b" * 64,
        "cert_sha256": "c" * 64,
        "backup": "writer-witness-20260716T054228Z.dump",
        "backup_sha256": "d" * 64,
    }
    original = {
        "state": "webapp:0:vacant",
        "receipts": "0",
        "manifest_sha256": "e" * 64,
        "cert_sha256": "f" * 64,
    }
    return {
        "schema_version": runner.PREFLIGHT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "preflight_passed",
        "git": {"head": HEAD, "expected_commit": HEAD},
        "run_bundle": {"expected_commit": HEAD},
        "failed_checks": [],
        "observed_baseline": {
            "webapp_fi_baseline": {},
            "webapp_ir_standby_baseline": {},
            "matrix_witness_dark_baseline": matrix,
            "rollback_witness_baseline": original,
        },
        "preflight_results": [
            {
                "check_id": "source_regression_gate",
                "stdout": (
                    '{"guarded_postgres_tests":4,"skipped":0,'
                    '"four_database_drill":true}'
                ),
            }
        ],
    }


class WriterWitnessRealHostMatrixRunnerTests(unittest.TestCase):
    def test_every_catalog_entry_has_an_executable_handler(self):
        missing = [
            scenario
            for scenario in runner.SCENARIOS
            if not hasattr(runner.Controller, f"scenario_{scenario.replace('-', '_')}")
        ]
        self.assertEqual(missing, [])
        self.assertEqual(set(runner.SCENARIOS), {f"RH-{index:03d}" for index in range(1, 13)})

    def test_plan_is_one_scenario_fail_closed_and_never_authorizes_arvan(self):
        args = argparse.Namespace(scenario="RH-004")
        plan = runner.build_plan(args, HEAD)
        self.assertEqual(plan["scenario"], "RH-004")
        self.assertTrue(plan["safety"]["one_scenario_per_process"])
        self.assertTrue(plan["safety"]["arvan_changes_forbidden"])
        self.assertTrue(plan["safety"]["webapp_writer_activation_forbidden"])
        self.assertIn("partition_fi_to_witness", plan["steps"])

    def test_preflight_requires_exact_commit_zero_skip_and_pinned_baselines(self):
        payload = passing_preflight()
        baseline = runner.validate_preflight(payload, HEAD)
        self.assertEqual(baseline["matrix_witness_dark_baseline"]["backup_sha256"], "d" * 64)
        payload["preflight_results"][0]["stdout"] = '{"skipped":2}'
        with self.assertRaisesRegex(runner.MatrixError, "zero-skip"):
            runner.validate_preflight(payload, HEAD)

    def test_preflight_refuses_a_different_exact_commit(self):
        with self.assertRaisesRegex(runner.MatrixError, "exact commit"):
            runner.validate_preflight(passing_preflight(), "1" * 40)

    def test_observer_approval_is_bound_to_preflight_and_separate_roles(self):
        approval = {
            "schema_version": runner.APPROVAL_SCHEMA,
            "status": "approved",
            "scenario": "RH-001",
            "expected_commit": HEAD,
            "preflight_sha256": "f" * 64,
            "observer": "abort-observer",
            "incident_commander": "incident-owner",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "out_of_band_console_ready": True,
            "alternate_communications_ready": True,
            "maintenance_window_confirmed": True,
            "dpi_budget_confirmed": True,
            "restore_authorized": True,
        }
        roles = runner.validate_approval(
            approval,
            scenario="RH-001",
            expected_head=HEAD,
            preflight_sha256="f" * 64,
            operator="matrix-executor",
        )
        self.assertEqual(roles, ("abort-observer", "incident-owner"))
        approval["preflight_sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.MatrixError, "preflight"):
            runner.validate_approval(
                approval,
                scenario="RH-001",
                expected_head=HEAD,
                preflight_sha256="f" * 64,
                operator="matrix-executor",
            )

    def test_cleanup_contract_keeps_network_faults_after_requester_and_evidence_steps(self):
        source = (Path(__file__).resolve().parents[1] / "scripts/run_writer_witness_real_host_matrix.py").read_text()
        requester = source.index('"stop_and_join_requesters"')
        capability = source.index('"revoke_transient_capability"')
        evidence = source.index('"retain_pre_recovery_evidence"')
        network = source.index('"remove_scoped_network_faults"')
        restore = source.index('"restore_vacant_baseline"')
        self.assertLess(requester, network)
        self.assertLess(capability, network)
        self.assertLess(evidence, restore)

    def test_matrix_client_body_and_signature_are_stable_without_exposing_secret(self):
        args = argparse.Namespace(
            action="acquire",
            expected_epoch=0,
            expected_lease_id=None,
            request_id="matrix-test",
            reason="test",
            lease_duration_seconds=30,
        )
        body = client.request_payload(args)
        first = client.signed_headers(
            key_id="key-v1",
            secret="s" * 64,
            site="webapp_fi",
            method="POST",
            path=client.TRANSITION_PATH,
            body=body,
            request_id="matrix-test",
            timestamp=123,
        )
        second = client.signed_headers(
            key_id="key-v1",
            secret="s" * 64,
            site="webapp_fi",
            method="POST",
            path=client.TRANSITION_PATH,
            body=body,
            request_id="matrix-test",
            timestamp=123,
        )
        self.assertEqual(first, second)
        self.assertNotIn("s" * 64, json.dumps(first))

    def test_matrix_client_requires_owner_only_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "client.env"
            path.write_text("A=B\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(client.MatrixClientError, "owner-only"):
                client.settings(path)


if __name__ == "__main__":
    unittest.main()
