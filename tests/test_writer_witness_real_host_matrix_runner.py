import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from scripts import run_writer_witness_real_host_matrix as runner
from scripts import writer_witness_matrix_client as client
from scripts.plan_writer_witness_real_host_matrix import abort_and_rollback_contract


HEAD = "a" * 40


def lock_worker(path: str, action: str, ready, release, results) -> None:
    runner.LOCK_PATH = Path(path)
    try:
        descriptor = runner.acquire_local_lock("RH-001", HEAD)
    except runner.MatrixError:
        results.put((action, "blocked"))
        return
    results.put((action, "acquired"))
    if ready is not None:
        ready.set()
    if release is not None:
        release.wait(10)
    runner.release_local_lock(descriptor)


def passing_preflight():
    matrix = {
        "state": "webapp:0:vacant",
        "receipts": "0",
        "manifest_sha256": "b" * 64,
        "cert_sha256": "c" * 64,
        "backup": "writer-witness-20260716T054228Z.dump",
        "backup_sha256": "d" * 64,
        "credential_bundle_sha256": "1" * 64,
        "installed_helpers_match": "yes",
        "running_release_match": "yes",
        "network_policy_semantics_match": "yes",
        "connection_enabled_aux_databases": "0",
        "orphan_candidate_failed_databases": "0",
        "campaign_state": "absent",
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
            "webapp_fi_baseline": {
                "app": "healthy", "db": "healthy", "api": "200",
                "witness_flags_enabled": "no", "client_credentials_installed": "no",
            },
            "webapp_ir_standby_baseline": {
                "app": "stopped", "sync_worker": "stopped", "db": "running",
                "witness_flags_enabled": "no", "client_credentials_installed": "no",
            },
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
        now = datetime.now(timezone.utc)
        approval = {
            "schema_version": runner.APPROVAL_SCHEMA,
            "status": "approved",
            "scenario": "RH-001",
            "expected_commit": HEAD,
            "preflight_sha256": "f" * 64,
            "observer": "abort-observer",
            "incident_commander": "incident-owner",
            "reason": "approved incident reason",
            "change_id": "CHG-1234",
            "authorization_nonce": "1" * 32,
            "approved_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "out_of_band_console_ready": True,
            "alternate_communications_ready": True,
            "maintenance_window_confirmed": True,
            "dpi_budget_confirmed": True,
            "restore_authorized": True,
            "max_control_requests": 100,
            "dpi_byte_budget": 4_000_000,
            "out_of_band_console": "provider-console/session-1234",
            "alternate_communications": "incident bridge 1234",
            "maintenance_window_start": (now - timedelta(minutes=5)).isoformat(),
            "maintenance_window_end": (now + timedelta(hours=1)).isoformat(),
            "restore_backup_sha256": "d" * 64,
            "restore_authorized_by": "incident-owner",
        }
        roles = runner.validate_approval(
            approval,
            scenario="RH-001",
            expected_head=HEAD,
            preflight_sha256="f" * 64,
            operator="matrix-executor",
            reason="approved incident reason",
            change_id="CHG-1234",
            expected_restore_sha256="d" * 64,
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
                reason="approved incident reason",
                change_id="CHG-1234",
                expected_restore_sha256="d" * 64,
            )

    def test_lock_owner_survives_two_competing_processes(self):
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / "active.lock")
            ready = context.Event()
            release = context.Event()
            results = context.Queue()
            owner = context.Process(
                target=lock_worker,
                args=(lock_path, "owner", ready, release, results),
            )
            owner.start()
            self.assertTrue(ready.wait(5))
            competitors = [
                context.Process(
                    target=lock_worker,
                    args=(lock_path, name, None, None, results),
                )
                for name in ("competitor-b", "competitor-c")
            ]
            for process in competitors:
                process.start()
            outcomes = {results.get(timeout=5), results.get(timeout=5), results.get(timeout=5)}
            self.assertIn(("owner", "acquired"), outcomes)
            self.assertIn(("competitor-b", "blocked"), outcomes)
            self.assertIn(("competitor-c", "blocked"), outcomes)
            self.assertTrue(Path(lock_path).exists())
            release.set()
            owner.join(5)
            for process in competitors:
                process.join(5)
            self.assertEqual(owner.exitcode, 0)
            self.assertTrue(all(process.exitcode == 0 for process in competitors))

    def test_cleanup_failure_before_reconnect_never_removes_partition_or_restores(self):
        controller = object.__new__(runner.Controller)
        controller.staged_sites = set()
        controller.network_fault_sites = {"webapp_fi"}
        controller.rotation_sites = set()
        controller.witness_mutated = True
        controller.local_secret_root = Path(tempfile.mkdtemp())
        controller.evidence_failed = False
        controller._cleanup_mode = False
        controller.remote_campaign_conflict = False
        controller.remote_campaign_claimed = False
        controller.journal = mock.Mock()
        controller.journal.values.return_value = set()
        controller.event = mock.Mock()
        controller.stop_and_remove_requesters = mock.Mock(side_effect=runner.MatrixError("ambiguous requester"))
        controller.recover_rotation = mock.Mock()
        controller.capture_pre_recovery = mock.Mock()
        controller.resume_witness_runtime = mock.Mock()
        controller.remove_isolated_pressure = mock.Mock()
        controller.remove_partition = mock.Mock()
        controller.restore_once = mock.Mock()
        with self.assertRaisesRegex(runner.MatrixError, "before reconnect/restore"):
            controller.cleanup()
        controller.remove_partition.assert_not_called()
        controller.restore_once.assert_not_called()

    def test_abort_monitor_failure_interrupts_scenario_wait(self):
        controller = object.__new__(runner.Controller)
        controller.tag = "wwm_0123456789ab"
        controller._abort_event = __import__("threading").Event()
        controller._abort_reason = None
        controller._monitor_thread = None
        controller._abort_probe = mock.Mock(side_effect=runner.MatrixAbort("IR writer started"))
        controller.event = mock.Mock()
        with self.assertRaisesRegex(runner.MatrixAbort, "IR writer started"):
            controller.start_abort_monitor()

    def test_partial_staging_is_owned_before_ambiguous_transfer(self):
        controller = object.__new__(runner.Controller)
        controller.tag = "wwm_0123456789ab"
        controller.remote_root = "/run/writer-witness-matrix/wwm_0123456789ab"
        controller.local_secret_root = Path(tempfile.mkdtemp())
        controller.rotation_sites = set()
        controller.staged_sites = set()
        controller._secret_sentinels = set()
        controller.journal = mock.Mock()
        controller.journal.values.return_value = set()
        controller.remote = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))

        def transfer_from(_role, remote_path, local_path, _name):
            if remote_path.endswith(".env"):
                local_path.write_text(
                    "WRITER_WITNESS_INTERNAL_URL=https://185.206.95.94\n"
                    "WRITER_WITNESS_CLIENT_KEY_ID=matrix-wwm_0123456789ab-fi\n"
                    f"WRITER_WITNESS_CLIENT_SECRET={'s' * 64}\n",
                    encoding="utf-8",
                )
            else:
                local_path.write_text("test-ca", encoding="utf-8")
            local_path.chmod(0o600)

        controller.transfer_from = mock.Mock(side_effect=transfer_from)
        controller.transfer_to = mock.Mock(side_effect=runner.MatrixError("lost scp response"))
        with self.assertRaisesRegex(runner.MatrixError, "lost scp response"):
            controller.stage_site("webapp_fi")
        claimed = [call.args for call in controller.journal.claim.call_args_list]
        self.assertIn(("rotation_sites", "webapp_fi"), claimed)
        self.assertIn(("staged_sites", "webapp_fi"), claimed)
        self.assertIn(("matrix_key_ids", "matrix-wwm_0123456789ab-fi"), claimed)

    def test_approval_nonce_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {"authorization_nonce": "2" * 32}
            first = runner.consume_approval(payload, Path(directory))
            self.assertTrue(first.is_file())
            with self.assertRaises(FileExistsError):
                runner.consume_approval(payload, Path(directory))

    def test_preflight_artifact_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            first = runner.consume_preflight("3" * 64, "RH-001", Path(directory))
            self.assertTrue(first.is_file())
            with self.assertRaises(FileExistsError):
                runner.consume_preflight("3" * 64, "RH-002", Path(directory))

    def test_cleanup_pending_postflight_remains_a_global_dirty_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = runner.CampaignJournal(
                root / "wwm_0123456789ab.json",
                {
                    "schema_version": runner.RUNNER_SCHEMA,
                    "status": "cleanup_verified_pending_postflight",
                    "dirty": True,
                },
                create=True,
            )
            with self.assertRaisesRegex(runner.MatrixError, "require recovery"):
                runner.assert_no_dirty_campaigns(root)
            journal.update(status="completed", dirty=False)
            runner.assert_no_dirty_campaigns(root)

    def test_observer_and_commander_must_have_independent_signer_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "allowed_signers"
            path.write_text(
                "observer,commander ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(runner.MatrixError, "independent signing keys"):
                runner.assert_independent_signer_keys(path, "observer", "commander")

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
        planned = tuple(
            item["step_id"] for item in abort_and_rollback_contract()["ordered_steps"]
        )
        self.assertEqual(runner.CLEANUP_STEP_IDS, planned)

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

    def test_matrix_client_pins_certificate_on_the_request_connection(self):
        certificate = b"pinned-leaf-certificate"
        response = mock.Mock(status=200)
        response.read.return_value = b'{"accepted":true}'
        connection = mock.Mock()
        connection.sock.getpeercert.return_value = certificate
        connection.getresponse.return_value = response
        with mock.patch.object(client, "HTTPSConnection", return_value=connection):
            status, payload, ready, sent = client.perform_http(
                parsed=SimpleNamespace(hostname="185.206.95.94", port=None),
                path=client.STATUS_PATH,
                method="GET",
                body=b"",
                headers={},
                context=object(),
                timeout=5,
                expected_cert_sha256=hashlib.sha256(certificate).hexdigest(),
                not_before_unix_ms=None,
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        self.assertLessEqual(ready, sent)
        connection.request.assert_called_once()
        connection.close.assert_called_once()

    def test_matrix_client_refuses_leaf_mismatch_before_request(self):
        connection = mock.Mock()
        connection.sock.getpeercert.return_value = b"wrong-leaf"
        with (
            mock.patch.object(client, "HTTPSConnection", return_value=connection),
            self.assertRaisesRegex(client.MatrixClientError, "fingerprint mismatch"),
        ):
            client.perform_http(
                parsed=SimpleNamespace(hostname="185.206.95.94", port=443),
                path=client.STATUS_PATH,
                method="GET",
                body=b"",
                headers={},
                context=object(),
                timeout=5,
                expected_cert_sha256="0" * 64,
                not_before_unix_ms=None,
            )
        connection.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
