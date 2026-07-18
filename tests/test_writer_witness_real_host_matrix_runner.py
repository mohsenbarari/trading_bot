import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shlex
import subprocess
import sys
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
    python_runtime = json.loads(
        (runner.ROOT / "deploy/writer-witness/python-runtime.json").read_text(encoding="utf-8")
    )
    nftables_policy = json.loads(
        (runner.ROOT / "deploy/writer-witness/nftables-policy.json").read_text(encoding="utf-8")
    )
    release_manifest_sha256 = "9" * 64
    matrix = {
        "state": "webapp:0:vacant",
        "receipts": "0",
        "manifest_sha256": "b" * 64,
        "cert_sha256": "c" * 64,
        "backup": "writer-witness-20260716T054228Z.dump",
        "backup_sha256": "d" * 64,
        "credential_bundle_sha256": "1" * 64,
        "database_inventory_sha256": "2" * 64,
        "release_manifest_sha256": release_manifest_sha256,
        "installed_helpers_match": "yes",
        "running_release_match": "yes",
        "release_manifest_attested": "yes",
        "release_metadata_attested": "yes",
        "effective_unit_attested": "yes",
        "system_runtime_attested": "yes",
        "runtime_attested": "yes",
        "runtime_provenance_attested": "yes",
        "offsite_upload_attested": "yes",
        "network_policy_semantics_match": "yes",
        "nftables_policy_sha256": nftables_policy["policy_sha256"],
        "connection_enabled_aux_databases": "0",
        "orphan_candidate_failed_databases": "0",
        "campaign_state": "absent",
    }
    original = {
        "state": "webapp:0:vacant",
        "receipts": "0",
        "manifest_sha256": "e" * 64,
        "cert_sha256": "f" * 64,
        "rollback_helper_attested": "yes",
    }
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": runner.PREFLIGHT_SCHEMA,
        "generated_at": now,
        "completed_at": now,
        "status": "preflight_passed",
        "git": {"head": HEAD, "expected_commit": HEAD},
        "run_bundle": {
            "expected_commit": HEAD,
            "source_sha256": "8" * 64,
            "witness_release_manifest_sha256": release_manifest_sha256,
            "python_runtime": python_runtime,
            "requirements_lock_sha256": hashlib.sha256(
                (runner.ROOT / "deploy/writer-witness/requirements.lock").read_bytes()
            ).hexdigest(),
            "wheelhouse_manifest_sha256": hashlib.sha256(
                (runner.ROOT / "deploy/writer-witness/wheelhouse.sha256").read_bytes()
            ).hexdigest(),
            "nftables_policy": nftables_policy,
            "expected_active_campaign_tag": None,
            "expected_active_campaign_scenario": None,
            "source_gate_requires_zero_skips": True,
            "source_gate_requires_guarded_postgres_tests": 5,
            "source_gate_requires_four_database_drill": True,
        },
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
                "check_id": check_id,
                "status": "passed",
                "return_code": 0,
                "stdout": (
                    '{"guarded_postgres_tests":5,"skipped":0,'
                    '"four_database_drill":true}'
                    if check_id == "source_regression_gate"
                    else ""
                ),
                "stderr": "",
            }
            for check_id in sorted(runner.REQUIRED_PREFLIGHT_CHECK_IDS)
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
        next(
            item
            for item in payload["preflight_results"]
            if item["check_id"] == "source_regression_gate"
        )["stdout"] = '{"skipped":2}'
        with self.assertRaisesRegex(runner.MatrixError, "zero-skip"):
            runner.validate_preflight(payload, HEAD)

    def test_preflight_refuses_a_different_exact_commit(self):
        with self.assertRaisesRegex(runner.MatrixError, "exact commit"):
            runner.validate_preflight(passing_preflight(), "1" * 40)

    def test_preflight_requires_every_result_to_have_passed(self):
        payload = passing_preflight()
        payload["preflight_results"][0]["status"] = "failed"
        payload["preflight_results"][0]["return_code"] = 1
        with self.assertRaisesRegex(runner.MatrixError, "did not pass"):
            runner.validate_preflight(payload, HEAD)

    def test_preflight_requires_strong_runtime_release_and_nft_markers(self):
        for marker in (
            "release_metadata_attested",
            "effective_unit_attested",
            "system_runtime_attested",
            "runtime_provenance_attested",
            "nftables_policy_sha256",
        ):
            with self.subTest(marker=marker):
                payload = passing_preflight()
                payload["observed_baseline"]["matrix_witness_dark_baseline"].pop(marker)
                with self.assertRaisesRegex(runner.MatrixError, "required current marker"):
                    runner.validate_preflight(payload, HEAD)

    def test_preflight_rejects_bundle_digest_drift(self):
        payload = passing_preflight()
        payload["run_bundle"]["requirements_lock_sha256"] = "0" * 64
        with self.assertRaisesRegex(runner.MatrixError, "requirements_lock_sha256 drifted"):
            runner.validate_preflight(payload, HEAD)

    def test_preflight_freshness_begins_when_expensive_checks_complete(self):
        payload = passing_preflight()
        payload["generated_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        runner.validate_preflight(payload, HEAD)

        payload["completed_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=6)
        ).isoformat()
        with self.assertRaisesRegex(runner.MatrixError, "five-minute"):
            runner.validate_preflight(payload, HEAD)

    def test_preflight_refuses_completion_before_generation(self):
        payload = passing_preflight()
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        payload["completed_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        with self.assertRaisesRegex(runner.MatrixError, "predates"):
            runner.validate_preflight(payload, HEAD)

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
            "max_scenario_seconds": runner.MAX_SCENARIO_SECONDS,
            "dpi_byte_budget": runner.MIN_DPI_BYTE_BUDGET,
            "out_of_band_console": "provider-console/session-1234",
            "alternate_communications": "incident bridge 1234",
            "maintenance_window_start": (now - timedelta(minutes=5)).isoformat(),
            "maintenance_window_end": (now + timedelta(hours=1)).isoformat(),
            "restore_backup_sha256": "d" * 64,
            "restore_authorized_by": "incident-owner",
            "allowed_signers_sha256": "9" * 64,
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
            expected_allowed_signers_sha256="9" * 64,
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
                expected_allowed_signers_sha256="9" * 64,
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
        controller.remote_campaign_claimed = True
        controller.remote_campaign_ambiguous = False
        controller.journal = mock.Mock()
        cleanup_started_at = datetime.now(timezone.utc)
        controller.journal.payload = {
            "lifecycle_phase": "scenario_executing",
            "initial_database_inventory": [],
            "cleanup_started_at": cleanup_started_at.isoformat(),
            "cleanup_not_after": (
                cleanup_started_at
                + timedelta(seconds=runner.MAX_CLEANUP_SECONDS)
            ).isoformat(),
        }
        controller.journal.values.return_value = set()
        controller.event = mock.Mock()
        controller.assert_remote_campaign_owned = mock.Mock()
        controller.stop_and_remove_requesters = mock.Mock(side_effect=runner.MatrixError("ambiguous requester"))
        controller.recover_rotation = mock.Mock()
        controller.capture_pre_recovery = mock.Mock()
        controller.recover_active_live_restore = mock.Mock()
        controller.resume_witness_runtime = mock.Mock()
        controller.remove_isolated_pressure = mock.Mock()
        controller.remove_partition = mock.Mock()
        controller.restore_once = mock.Mock()
        controller.remove_owned_aux_databases = mock.Mock()
        controller.verify_complete_baseline = mock.Mock()
        with self.assertRaisesRegex(runner.MatrixError, "preserve fault isolation"):
            controller.cleanup()
        controller.capture_pre_recovery.assert_called_once()
        controller.recover_rotation.assert_called_once()
        controller.recover_active_live_restore.assert_called_once()
        controller.resume_witness_runtime.assert_not_called()
        controller.remove_partition.assert_not_called()
        controller.restore_once.assert_not_called()

    def test_requester_and_hidden_state_guards_use_strict_non_masking_shells(self):
        controller = object.__new__(runner.Controller)
        controller.staged_sites = {"webapp_fi"}
        controller.remote_root = "/run/writer-witness-matrix/wwm_0123456789ab"
        controller.journal = mock.Mock()
        controller.journal.values.return_value = set()
        controller.remote = mock.Mock()
        controller.stop_and_remove_requesters()
        command = controller.remote.call_args.args[2]
        self.assertTrue(command.startswith("set -Eeuo pipefail;"))
        self.assertLess(command.index("! pgrep"), command.index("rm -rf"))
        self.assertLess(command.index("! ss"), command.index("rm -rf"))

        source = (
            Path(__file__).resolve().parents[1]
            / "scripts/run_writer_witness_real_host_matrix.py"
        ).read_text(encoding="utf-8")
        hidden = source[source.index('f"verify_no_hidden_state_{role}"') :]
        hidden = hidden[: hidden.index("self.event(\"baseline.verified\"")]
        self.assertIn('"set -Eeuo pipefail; "', hidden)
        self.assertIn("test ! -L", hidden)
        self.assertIn("if nft list table", hidden)
        self.assertNotIn("&& exit 1 || true", hidden)

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

    def test_local_consumption_rejects_a_symlinked_index_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            (root / "consumed-preflights").symlink_to(
                outside,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(runner.MatrixError, "must be real"):
                runner.consume_preflight("3" * 64, "RH-001", root)
            self.assertEqual(list(outside.iterdir()), [])

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
                runner.assert_independent_signer_keys(path.read_bytes(), "observer", "commander")

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


class WriterWitnessRealHostMatrixAdversarialTests(unittest.TestCase):
    @staticmethod
    def _approval(now: datetime, *, expires_at: datetime) -> dict[str, object]:
        return {
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
            "expires_at": expires_at.isoformat(),
            "out_of_band_console_ready": True,
            "alternate_communications_ready": True,
            "maintenance_window_confirmed": True,
            "dpi_budget_confirmed": True,
            "restore_authorized": True,
            "max_control_requests": 100,
            "max_scenario_seconds": runner.MAX_SCENARIO_SECONDS,
            "dpi_byte_budget": runner.MIN_DPI_BYTE_BUDGET,
            "out_of_band_console": "provider-console/session-1234",
            "alternate_communications": "incident bridge 1234",
            "maintenance_window_start": (now - timedelta(minutes=5)).isoformat(),
            "maintenance_window_end": (now + timedelta(hours=1)).isoformat(),
            "restore_backup_sha256": "d" * 64,
            "restore_authorized_by": "incident-owner",
            "allowed_signers_sha256": "9" * 64,
        }

    @staticmethod
    def _validate_approval(payload: dict[str, object]) -> tuple[str, str]:
        return runner.validate_approval(
            payload,
            scenario="RH-001",
            expected_head=HEAD,
            preflight_sha256="f" * 64,
            operator="matrix-executor",
            reason="approved incident reason",
            change_id="CHG-1234",
            expected_restore_sha256="d" * 64,
            expected_allowed_signers_sha256="9" * 64,
        )

    def test_approval_accepts_exactly_one_hour_but_rejects_one_microsecond_more(self):
        approved_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        exact = self._approval(
            approved_at,
            expires_at=approved_at + timedelta(hours=1),
        )
        self.assertEqual(
            self._validate_approval(exact),
            ("abort-observer", "incident-owner"),
        )
        too_long = dict(exact)
        too_long["expires_at"] = (
            approved_at + timedelta(hours=1, microseconds=1)
        ).isoformat()
        with self.assertRaisesRegex(runner.MatrixError, "expired"):
            self._validate_approval(too_long)

    def test_approval_binds_runtime_and_all_transport_budget(self):
        approved_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        exact = self._approval(
            approved_at,
            expires_at=approved_at + timedelta(minutes=30),
        )
        missing_runtime = dict(exact)
        missing_runtime.pop("max_scenario_seconds")
        with self.assertRaisesRegex(runner.MatrixError, "runtime bound"):
            self._validate_approval(missing_runtime)
        undersized = dict(exact)
        undersized["dpi_byte_budget"] = runner.MIN_DPI_BYTE_BUDGET - 1
        with self.assertRaisesRegex(runner.MatrixError, "all-transport"):
            self._validate_approval(undersized)

    def test_transport_budget_reserves_handshake_until_success_is_known(self):
        controller = object.__new__(runner.Controller)
        controller._budget_lock = __import__("threading").Lock()
        controller._cleanup_mode = False
        controller.control_requests = 0
        controller.control_bytes_upper_bound = 0
        controller.transport_operations = 0
        controller.transport_bytes_upper_bound = 0
        controller.cleanup_transport_operations = 0
        controller.cleanup_transport_bytes_upper_bound = 0
        controller._transport_master_roles = set()
        controller.dpi_byte_budget = runner.MIN_DPI_BYTE_BUDGET
        controller.journal = mock.Mock()

        first = controller._reserve_transport_budget(
            role="webapp_fi", kind="ssh", payload_bytes=4
        )
        second = controller._reserve_transport_budget(
            role="webapp_fi", kind="ssh", payload_bytes=4
        )
        self.assertEqual(first, second)
        self.assertNotIn("webapp_fi", controller._transport_master_roles)
        controller._mark_transport_master("webapp_fi")
        reused = controller._reserve_transport_budget(
            role="webapp_fi", kind="ssh", payload_bytes=4
        )
        self.assertEqual(first, reused)

    def test_failed_transport_forgets_master_before_next_budget_reservation(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = False
        controller.raise_if_abort_requested = mock.Mock()
        controller.event = mock.Mock(return_value=True)
        controller._reserve_transport_budget = mock.Mock()
        controller._mark_transport_master = mock.Mock()
        controller._forget_transport_master = mock.Mock()
        controller.redact_command_output = lambda value: value
        controller.secret_output_detected = False

        failed = subprocess.CompletedProcess([], 255, "", "transport failed")
        with mock.patch.object(runner.subprocess, "run", return_value=failed):
            completed = controller.command(
                "failed_transport",
                ["ssh", "example.invalid", "true"],
                check=False,
                transport_role="webapp_fi",
                transport_kind="ssh",
                transport_payload_bytes=4,
            )

        self.assertEqual(completed.returncode, 255)
        controller._forget_transport_master.assert_called_once_with("webapp_fi")
        controller._mark_transport_master.assert_not_called()

    def test_timed_out_transport_forgets_ambiguous_master_before_retry(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = False
        controller.raise_if_abort_requested = mock.Mock()
        controller.event = mock.Mock(return_value=True)
        controller._reserve_transport_budget = mock.Mock()
        controller._mark_transport_master = mock.Mock()
        controller._forget_transport_master = mock.Mock()
        controller.redact_command_output = lambda value: value
        controller.secret_output_detected = False

        with mock.patch.object(
            runner.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["ssh"], 8),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                controller.command(
                    "timed_out_transport",
                    ["ssh", "example.invalid", "true"],
                    transport_role="webapp_fi",
                    transport_kind="ssh",
                    transport_payload_bytes=4,
                )
        controller._forget_transport_master.assert_called_once_with("webapp_fi")
        controller._mark_transport_master.assert_not_called()

    def test_cleanup_command_timeout_is_clamped_to_remaining_deadline(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = True
        controller._cleanup_deadline = 102.0
        controller.event = mock.Mock(return_value=True)
        controller.redact_command_output = lambda value: value
        controller.secret_output_detected = False
        completed = subprocess.CompletedProcess(["true"], 0, "", "")
        with (
            mock.patch.object(runner.time, "monotonic", return_value=100.0),
            mock.patch.object(
                runner.subprocess,
                "run",
                return_value=completed,
            ) as run_mock,
        ):
            controller.command("bounded_cleanup", ["true"], timeout=900)
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 2.0)

    def test_expired_control_master_reserves_a_new_handshake(self):
        controller = object.__new__(runner.Controller)
        controller._budget_lock = __import__("threading").Lock()
        controller._cleanup_mode = False
        controller.control_requests = 0
        controller.control_bytes_upper_bound = 0
        controller.transport_operations = 0
        controller.transport_bytes_upper_bound = 0
        controller.cleanup_transport_operations = 0
        controller.cleanup_transport_bytes_upper_bound = 0
        controller._transport_master_roles = {"webapp_fi"}
        controller._transport_master_deadlines = {"webapp_fi": 99.0}
        controller.dpi_byte_budget = runner.MIN_DPI_BYTE_BUDGET
        controller.journal = mock.Mock()

        with mock.patch.object(runner.time, "monotonic", return_value=100.0):
            reserved = controller._reserve_transport_budget(
                role="webapp_fi", kind="ssh", payload_bytes=4
            )
        self.assertEqual(
            reserved,
            runner.SSH_COMMAND_BYTES_UPPER_BOUND
            + runner.SSH_MASTER_BYTES_UPPER_BOUND
            + 4,
        )
        self.assertNotIn("webapp_fi", controller._transport_master_roles)

    def test_reboot_reconnect_accounts_every_direct_ssh_attempt(self):
        controller = object.__new__(runner.Controller)
        controller._forget_transport_master = mock.Mock()
        controller._ssh_control_path = mock.Mock(
            return_value=Path(tempfile.mkdtemp()) / "absent.sock"
        )
        controller._reserve_transport_budget = mock.Mock()
        controller.ssh_args = mock.Mock(return_value=["ssh", "witness", "true"])
        controller.interruptible_sleep = mock.Mock()
        controller._mark_transport_master = mock.Mock()
        controller.event = mock.Mock(return_value=True)
        down = subprocess.CompletedProcess([], 255, "", "down")
        up = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(runner.subprocess, "run", side_effect=(down, up)):
            controller.wait_for_ssh_after_reboot()
        self.assertEqual(controller._reserve_transport_budget.call_count, 2)
        self.assertTrue(
            all(
                call.kwargs["force_master_reservation"] is True
                for call in controller._reserve_transport_budget.call_args_list
            )
        )
        controller._mark_transport_master.assert_called_once_with("matrix_witness")

    def test_recovery_deadline_is_derived_from_original_campaign_start(self):
        with tempfile.TemporaryDirectory(prefix="matrix-deadline-") as directory:
            root = Path(directory)
            secret_root = root / "secrets"
            artifact_dir = root / "artifacts"
            campaign_root = root / "campaigns"
            for path in (secret_root, artifact_dir, campaign_root):
                path.mkdir(mode=0o700)
                path.chmod(0o700)
            tag = "wwm_0123456789ab"
            created_at = datetime.now(timezone.utc) - timedelta(
                seconds=runner.MAX_SCENARIO_SECONDS - 1
            )
            journal = runner.CampaignJournal(
                campaign_root / f"{tag}.json",
                {
                    "schema_version": runner.RUNNER_SCHEMA,
                    "status": "active",
                    "dirty": True,
                    "tag": tag,
                    "scenario": "RH-001",
                    "expected_commit": HEAD,
                    "dpi_byte_budget": runner.MIN_DPI_BYTE_BUDGET,
                    "max_scenario_seconds": runner.MAX_SCENARIO_SECONDS,
                    "created_at": created_at.isoformat(),
                    "artifact_dir": str(artifact_dir),
                    "resources": {},
                },
                create=True,
            )
            findmnt = subprocess.CompletedProcess([], 0, "tmpfs\n", "")
            with (
                mock.patch.object(runner, "DEFAULT_SECRET_ROOT", secret_root),
                mock.patch.object(runner.subprocess, "run", return_value=findmnt),
            ):
                controller = runner.Controller(
                    scenario="RH-001",
                    operator="operator",
                    observer="observer",
                    incident_commander="commander",
                    reason="recovery deadline test",
                    expected_head=HEAD,
                    preflight={},
                    baseline={},
                    artifact_dir=artifact_dir,
                    campaign_root=campaign_root,
                    dpi_byte_budget=runner.MIN_DPI_BYTE_BUDGET,
                    tag=tag,
                    existing_journal=journal,
                )
            remaining = controller._scenario_deadline - __import__("time").monotonic()
            self.assertGreaterEqual(remaining, 0)
            self.assertLess(remaining, 2)

    def test_cleanup_has_an_independent_bounded_recovery_deadline(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_deadline = __import__("time").monotonic() - 1
        with self.assertRaisesRegex(runner.MatrixAbort, "cleanup exceeded"):
            controller.raise_if_cleanup_deadline_exceeded()

    def test_cleanup_deadline_is_persisted_and_cannot_renew_across_processes(self):
        with tempfile.TemporaryDirectory(prefix="matrix-cleanup-window-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            now = datetime.now(timezone.utc)
            journal = runner.CampaignJournal(
                root / "wwm_0123456789ab.json",
                {
                    "schema_version": runner.RUNNER_SCHEMA,
                    "cleanup_started_at": (now - timedelta(seconds=899)).isoformat(),
                    "cleanup_not_after": (now + timedelta(seconds=1)).isoformat(),
                },
                create=True,
            )
            first = object.__new__(runner.Controller)
            first.journal = journal
            first._cleanup_mode = False
            first.enter_cleanup_mode()
            first_remaining = first._cleanup_deadline - __import__("time").monotonic()
            self.assertGreater(first_remaining, 0)
            self.assertLessEqual(first_remaining, 1.1)

            reloaded = runner.CampaignJournal.load(journal.path)
            second = object.__new__(runner.Controller)
            second.journal = reloaded
            second._cleanup_mode = False
            second.enter_cleanup_mode()
            second_remaining = second._cleanup_deadline - __import__("time").monotonic()
            self.assertLessEqual(second_remaining, first_remaining)
            self.assertEqual(
                reloaded.payload["cleanup_not_after"],
                journal.payload["cleanup_not_after"],
            )

    def test_expired_cleanup_allows_only_fixed_timeout_emergency_revocation(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = True
        controller._cleanup_deadline = __import__("time").monotonic() - 1
        controller.event = mock.Mock(return_value=True)
        controller.redact_command_output = lambda value: value
        controller.secret_output_detected = False
        completed = subprocess.CompletedProcess(["true"], 0, "", "")
        with mock.patch.object(
            runner.subprocess,
            "run",
            return_value=completed,
        ) as run_mock:
            controller.command(
                "exact_revocation_after_expiry",
                ["true"],
                timeout=900,
                emergency_revocation=True,
            )
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 30.0)
        with self.assertRaisesRegex(runner.MatrixAbort, "cleanup exceeded"):
            controller.command("forbidden_recovery_after_expiry", ["true"])

    def test_cleanup_expiry_mid_run_routes_to_exact_emergency_revocation(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_within_deadline = mock.Mock(
            side_effect=runner.MatrixAbort("cleanup exceeded")
        )
        controller._revoke_after_expired_cleanup_window = mock.Mock(
            side_effect=runner.MatrixError("exact authority revoked; reauthorization required")
        )
        with self.assertRaisesRegex(runner.MatrixError, "reauthorization required"):
            controller.cleanup()
        controller._cleanup_within_deadline.assert_called_once_with()
        controller._revoke_after_expired_cleanup_window.assert_called_once_with()

    def test_approve_mode_derives_a_deterministic_sub_hour_expiry(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory(prefix="matrix-approval-") as directory:
            root = Path(directory)
            controller_root = root / "controller"
            artifact_root = controller_root / "runs"
            campaign_root = controller_root / "campaigns"
            trusted_signers = root / "allowed_signers"
            trusted_signers.write_text(
                "observer ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestObserver\n"
                "commander ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestCommander\n",
                encoding="utf-8",
            )
            trusted_signers.chmod(0o600)
            preflight = root / "preflight.json"
            preflight.write_text(
                json.dumps(passing_preflight()) + "\n",
                encoding="utf-8",
            )
            preflight.chmod(0o600)
            output = controller_root / "approvals" / "rh-001.json"
            args = SimpleNamespace(
                mode="approve",
                scenario="RH-001",
                expected_commit=HEAD,
                campaign_journal=None,
                preflight=preflight,
                observer="observer",
                incident_commander="commander",
                reason="approved incident reason",
                change_id="CHG-1234",
                out_of_band_console="provider-console/session-1234",
                alternate_communications="incident bridge 1234",
                maintenance_window_start=(now - timedelta(minutes=1)).isoformat(),
                maintenance_window_end=(now + timedelta(hours=1)).isoformat(),
                dpi_byte_budget=runner.MIN_DPI_BYTE_BUDGET,
                restore_authorized_by="incident-owner",
                output=output,
                allowed_signers=trusted_signers,
            )
            with (
                mock.patch.object(runner, "parse_args", return_value=args),
                mock.patch.object(runner, "DEFAULT_CONTROLLER_ROOT", controller_root),
                mock.patch.object(runner, "DEFAULT_ARTIFACT_ROOT", artifact_root),
                mock.patch.object(runner, "DEFAULT_CAMPAIGN_ROOT", campaign_root),
                mock.patch.object(runner, "TRUSTED_ALLOWED_SIGNERS", trusted_signers),
                mock.patch.object(
                    runner,
                    "TRUSTED_ALLOWED_SIGNERS_SHA256",
                    hashlib.sha256(trusted_signers.read_bytes()).hexdigest(),
                ),
                mock.patch.dict(
                    os.environ,
                    {runner.OBSERVER_CONFIRM_ENV: runner.OBSERVER_CONFIRM_VALUE},
                ),
            ):
                self.assertEqual(runner.main(), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            approved = datetime.fromisoformat(payload["approved_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
            self.assertEqual(expires - approved, timedelta(minutes=45))
            self.assertLessEqual(expires, approved + timedelta(hours=1))

    def test_signer_policy_is_fail_closed_until_an_external_source_pin_exists(self):
        with self.assertRaisesRegex(runner.MatrixError, "not yet pinned"):
            runner.assert_source_pinned_signer_policy(b"two locally selected keys\n")
        value = b"observer ssh-ed25519 AAAA\ncommander ssh-ed25519 BBBB\n"
        with mock.patch.object(
            runner,
            "TRUSTED_ALLOWED_SIGNERS_SHA256",
            hashlib.sha256(value).hexdigest(),
        ):
            self.assertEqual(
                runner.assert_source_pinned_signer_policy(value),
                hashlib.sha256(value).hexdigest(),
            )

    def test_approve_mode_rejects_a_caller_selected_trust_store(self):
        now = datetime.now(timezone.utc)
        args = SimpleNamespace(
            mode="approve",
            scenario="RH-001",
            expected_commit=HEAD,
            campaign_journal=None,
            preflight=Path("/not/read/before/trust-check.json"),
            observer="observer",
            incident_commander="commander",
            reason="approved incident reason",
            change_id="CHG-1234",
            out_of_band_console="provider-console/session-1234",
            alternate_communications="incident bridge 1234",
            maintenance_window_start=(now - timedelta(minutes=1)).isoformat(),
            maintenance_window_end=(now + timedelta(hours=1)).isoformat(),
            dpi_byte_budget=runner.MIN_DPI_BYTE_BUDGET,
            restore_authorized_by="incident-owner",
            output=Path("/not/used.json"),
            allowed_signers=Path("/tmp/caller-selected-allowed-signers"),
        )
        with (
            mock.patch.object(runner, "parse_args", return_value=args),
            mock.patch.object(runner, "secure_directory"),
            mock.patch.dict(
                os.environ,
                {runner.OBSERVER_CONFIRM_ENV: runner.OBSERVER_CONFIRM_VALUE},
            ),
            self.assertRaisesRegex(runner.MatrixError, "canonical trusted signer"),
        ):
            runner.main()

    def test_signature_verification_uses_one_immutable_byte_snapshot(self):
        approval_raw = b'{"approval":"snapshot-a"}\n'
        signature_raw = b"snapshot-signature"
        allowed_raw = b"observer ssh-ed25519 AAAAC3NzaSnapshot\n"
        observed: dict[str, object] = {}

        def inspect_run(args, *, input, capture_output, pass_fds):
            observed["args"] = args
            observed["input"] = input
            observed["fd_bytes"] = tuple(
                os.pread(descriptor, 1_048_576, 0) for descriptor in pass_fds
            )
            return subprocess.CompletedProcess(args, 0, b"", b"")

        with mock.patch.object(runner.subprocess, "run", side_effect=inspect_run):
            runner.verify_approval_signature(
                approval_raw,
                signature_raw,
                identity="observer",
                allowed_signers_raw=allowed_raw,
            )
        self.assertEqual(observed["input"], approval_raw)
        self.assertEqual(observed["fd_bytes"], (allowed_raw, signature_raw))

    def test_secure_reader_rejects_symlink_and_hardlink_inputs(self):
        with tempfile.TemporaryDirectory(prefix="matrix-secure-read-") as directory:
            root = Path(directory)
            original = root / "approval.json"
            original.write_text("{}\n", encoding="utf-8")
            original.chmod(0o600)
            symlink = root / "approval-link.json"
            symlink.symlink_to(original)
            with self.assertRaisesRegex(runner.MatrixError, "securely open"):
                runner.read_secure_regular(symlink, label="approval")
            hardlink = root / "approval-hardlink.json"
            os.link(original, hardlink)
            with self.assertRaisesRegex(runner.MatrixError, "owner-only regular file"):
                runner.read_secure_regular(original, label="approval")

    def test_lock_rejects_symlink_wrong_mode_and_wrong_owner(self):
        with tempfile.TemporaryDirectory(prefix="matrix-lock-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            target = root / "target"
            target.write_text("do-not-truncate\n", encoding="utf-8")
            target.chmod(0o600)
            symlink_lock = root / "symlink.lock"
            symlink_lock.symlink_to(target)
            with (
                mock.patch.object(runner, "LOCK_PATH", symlink_lock),
                self.assertRaises(runner.MatrixError),
            ):
                runner.acquire_local_lock("RH-001", HEAD)
            self.assertEqual(target.read_text(encoding="utf-8"), "do-not-truncate\n")

            wrong_mode = root / "wrong-mode.lock"
            wrong_mode.touch(mode=0o644)
            wrong_mode.chmod(0o644)
            with (
                mock.patch.object(runner, "LOCK_PATH", wrong_mode),
                self.assertRaisesRegex(runner.MatrixError, "owner-only regular file"),
            ):
                runner.acquire_local_lock("RH-001", HEAD)

            wrong_owner = root / "wrong-owner.lock"
            wrong_owner.touch(mode=0o600)
            wrong_owner.chmod(0o600)
            real_fstat = os.fstat

            def foreign_owner(descriptor):
                metadata = real_fstat(descriptor)
                return SimpleNamespace(
                    st_mode=metadata.st_mode,
                    st_uid=os.geteuid() + 1,
                    st_nlink=metadata.st_nlink,
                )

            with (
                mock.patch.object(runner, "LOCK_PATH", wrong_owner),
                mock.patch.object(runner.os, "fstat", side_effect=foreign_owner),
                self.assertRaisesRegex(runner.MatrixError, "owner-only regular file"),
            ):
                runner.acquire_local_lock("RH-001", HEAD)

    def test_secure_directory_rejects_a_symlink_parent(self):
        with tempfile.TemporaryDirectory(prefix="matrix-dir-") as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir(mode=0o700)
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(runner.MatrixError, "real, owner-controlled"):
                runner.secure_directory(linked)

    @staticmethod
    def _bare_cleanup_controller(*, claimed: bool, conflict: bool = False):
        controller = object.__new__(runner.Controller)
        controller.staged_sites = {"webapp_fi"}
        controller.network_fault_sites = {"webapp_fi"}
        controller.rotation_sites = {"webapp_fi"}
        controller.witness_mutated = True
        controller.local_secret_root = Path(tempfile.mkdtemp(prefix="matrix-cleanup-"))
        controller.evidence_failed = False
        controller._cleanup_mode = False
        controller.remote_campaign_conflict = conflict
        controller.remote_campaign_claimed = claimed
        controller.remote_campaign_ambiguous = False
        controller.journal = mock.Mock()
        cleanup_started_at = datetime.now(timezone.utc)
        controller.journal.payload = {
            "lifecycle_phase": "scenario_executing",
            "initial_database_inventory": [],
            "cleanup_started_at": cleanup_started_at.isoformat(),
            "cleanup_not_after": (
                cleanup_started_at
                + timedelta(seconds=runner.MAX_CLEANUP_SECONDS)
            ).isoformat(),
        }
        controller.journal.values.return_value = set()
        controller.event = mock.Mock(return_value=True)
        for name in (
            "claim_remote_campaign",
            "assert_remote_campaign_owned",
            "stop_and_remove_requesters",
            "recover_rotation",
            "capture_pre_recovery",
            "recover_active_live_restore",
            "resume_witness_runtime",
            "remove_isolated_pressure",
            "remove_partition",
            "restore_once",
            "remove_owned_aux_databases",
            "verify_complete_baseline",
            "release_remote_campaign",
        ):
            setattr(controller, name, mock.Mock())
        return controller

    def test_cleanup_without_remote_ownership_is_a_complete_remote_noop(self):
        controller = self._bare_cleanup_controller(claimed=False)
        with self.assertRaisesRegex(runner.MatrixError, "without proven campaign ownership"):
            controller.cleanup()
        for name in (
            "claim_remote_campaign",
            "assert_remote_campaign_owned",
            "stop_and_remove_requesters",
            "recover_rotation",
            "capture_pre_recovery",
            "recover_active_live_restore",
            "resume_witness_runtime",
            "remove_isolated_pressure",
            "remove_partition",
            "restore_once",
            "remove_owned_aux_databases",
            "verify_complete_baseline",
            "release_remote_campaign",
        ):
            getattr(controller, name).assert_not_called()

    def test_cleanup_on_explicit_foreign_ownership_is_a_complete_remote_noop(self):
        controller = self._bare_cleanup_controller(claimed=False, conflict=True)
        with self.assertRaisesRegex(runner.MatrixError, "foreign campaign"):
            controller.cleanup()
        for name in (
            "assert_remote_campaign_owned",
            "stop_and_remove_requesters",
            "recover_rotation",
            "capture_pre_recovery",
            "recover_active_live_restore",
            "resume_witness_runtime",
            "remove_isolated_pressure",
            "remove_partition",
            "restore_once",
            "remove_owned_aux_databases",
            "verify_complete_baseline",
            "release_remote_campaign",
        ):
            getattr(controller, name).assert_not_called()
        controller.journal.update.assert_called_once()
        self.assertEqual(
            controller.journal.update.call_args.kwargs["status"],
            "failed",
        )

    def test_cleanup_reconciles_an_ambiguous_claim_before_any_mutation(self):
        controller = self._bare_cleanup_controller(claimed=False)
        controller.remote_campaign_ambiguous = True
        ordered = mock.Mock()

        def prove_ownership():
            controller.remote_campaign_claimed = True
            controller.remote_campaign_ambiguous = False

        controller.claim_remote_campaign.side_effect = prove_ownership
        ordered.attach_mock(controller.claim_remote_campaign, "prove")
        ordered.attach_mock(controller.stop_and_remove_requesters, "first_mutation")
        controller.cleanup()
        names = [call[0] for call in ordered.mock_calls]
        self.assertLess(names.index("prove"), names.index("first_mutation"))

    def test_cleanup_revokes_credentials_but_keeps_network_isolated_after_requester_stop_failure(self):
        controller = self._bare_cleanup_controller(claimed=True)
        controller.stop_and_remove_requesters.side_effect = runner.MatrixError(
            "requester still running"
        )
        with self.assertRaisesRegex(runner.MatrixError, "preserve fault isolation"):
            controller.cleanup()
        controller.capture_pre_recovery.assert_called_once()
        controller.recover_active_live_restore.assert_called_once()
        controller.recover_rotation.assert_called_once()
        controller.resume_witness_runtime.assert_not_called()
        controller.remove_isolated_pressure.assert_not_called()
        controller.remove_partition.assert_not_called()
        controller.restore_once.assert_not_called()

    def test_remote_campaign_conflict_and_ambiguity_are_durable(self):
        cases = (
            (
                "foreign_then_transport",
                [
                    subprocess.CompletedProcess(
                        [], 1, "", "campaign_claim is owned by a different campaign identity"
                    ),
                    subprocess.CompletedProcess([], 255, "", "ssh transport failed"),
                    subprocess.CompletedProcess([], 255, "", "ssh transport failed"),
                ],
            ),
            (
                "transport",
                [subprocess.CompletedProcess([], 255, "", "ssh transport failed")] * 3,
            ),
        )
        for label, results in cases:
            with self.subTest(label=label):
                controller = object.__new__(runner.Controller)
                controller.tag = "wwm_0123456789ab"
                controller.expected_head = HEAD
                controller.scenario = "RH-001"
                controller.remote_campaign_claimed = False
                controller.remote_campaign_conflict = False
                controller.remote_campaign_ambiguous = False
                controller.journal = mock.Mock()
                controller.remote = mock.Mock(side_effect=results)
                with self.assertRaises(runner.MatrixError):
                    controller.claim_remote_campaign()
                self.assertTrue(controller.remote_campaign_ambiguous)
                self.assertFalse(controller.remote_campaign_conflict)
                updates = [call.kwargs for call in controller.journal.update.call_args_list]
                self.assertIn("ambiguous", [item.get("remote_campaign_claim_state") for item in updates])
                commands = [call.args[2] for call in controller.remote.call_args_list]
                self.assertTrue(all("writer-witness-matrix-campaign" in item for item in commands))
                self.assertTrue(all(HEAD in item and "RH-001" in item for item in commands))

    def test_structured_remote_inspection_is_authoritative_over_historical_stderr(self):
        controller = object.__new__(runner.Controller)
        controller.tag = "wwm_0123456789ab"
        controller.expected_head = HEAD
        controller.scenario = "RH-001"
        controller.remote_campaign_claimed = False
        controller.remote_campaign_conflict = False
        controller.remote_campaign_ambiguous = False
        controller.journal = mock.Mock()
        foreign = {
            "status": "inspected",
            "state": "active_foreign",
            "active_relation": "foreign",
            "release_relation": "absent",
            "tag": controller.tag,
            "expected_commit": HEAD,
            "scenario": controller.scenario,
            "not_after": controller.campaign_not_after,
            "expired": False,
            "active_identity": {
                "tag": "wwm_aaaaaaaaaaaa",
                "expected_commit": "b" * 40,
                "scenario": "RH-002",
            },
        }
        controller.remote = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess([], 1, "", "old non-authoritative error"),
                subprocess.CompletedProcess([], 1, "", "another old error"),
                subprocess.CompletedProcess([], 0, json.dumps(foreign) + "\n", ""),
            ]
        )
        with self.assertRaisesRegex(runner.MatrixError, "foreign or stale"):
            controller.claim_remote_campaign()
        self.assertTrue(controller.remote_campaign_conflict)
        self.assertFalse(controller.remote_campaign_ambiguous)

    def test_run_scenario_attests_inventory_before_consuming_remote_authorization(self):
        controller = object.__new__(runner.Controller)
        controller.scenario = "RH-001"
        controller.journal = mock.Mock()
        ordered = mock.Mock()
        for name in (
            "claim_remote_campaign",
            "attest_initial_database_inventory",
            "consume_remote_authorization",
            "critical_precheck",
            "start_abort_monitor",
            "raise_if_abort_requested",
            "stop_abort_monitor",
            "event",
        ):
            setattr(controller, name, mock.Mock())
        controller.scenario_RH_001 = mock.Mock()
        ordered.attach_mock(controller.claim_remote_campaign, "claim")
        ordered.attach_mock(controller.attest_initial_database_inventory, "inventory")
        ordered.attach_mock(controller.consume_remote_authorization, "consume")
        controller.run_scenario(
            authorization_nonce="a" * 32,
            preflight_sha256="b" * 64,
        )
        names = [call[0] for call in ordered.mock_calls]
        self.assertLess(names.index("claim"), names.index("inventory"))
        self.assertLess(names.index("inventory"), names.index("consume"))

    def test_cleanup_recaptures_inventory_only_at_proven_pre_scenario_phase(self):
        controller = self._bare_cleanup_controller(claimed=True)
        controller.journal.payload = {"lifecycle_phase": "remote_claimed"}
        controller.attest_initial_database_inventory = mock.Mock(
            side_effect=lambda: controller.journal.payload.update(
                initial_database_inventory=[]
            )
        )
        ordered = mock.Mock()
        ordered.attach_mock(controller.attest_initial_database_inventory, "inventory")
        ordered.attach_mock(controller.stop_and_remove_requesters, "first_mutation")
        controller.cleanup()
        names = [call[0] for call in ordered.mock_calls]
        self.assertLess(names.index("inventory"), names.index("first_mutation"))

        unsafe = self._bare_cleanup_controller(claimed=True)
        unsafe.journal.payload = {"lifecycle_phase": "scenario_executing"}
        unsafe.attest_initial_database_inventory = mock.Mock()
        with self.assertRaisesRegex(runner.MatrixError, "pre-scenario recovery boundary"):
            unsafe.cleanup()
        unsafe.attest_initial_database_inventory.assert_not_called()
        unsafe.stop_and_remove_requesters.assert_not_called()

    def test_every_local_consumption_phase_recovers_without_remote_mutation(self):
        for phase in (
            "controller_created",
            "local_authorization_consumption_intent",
            "local_preflight_consumed",
            "local_authorization_consumed",
        ):
            with self.subTest(phase=phase):
                controller = self._bare_cleanup_controller(claimed=False)
                controller.journal.payload = {
                    "remote_campaign_protocol": "atomic-helper-v1",
                    "lifecycle_phase": phase,
                }
                controller.cleanup()
                controller.claim_remote_campaign.assert_not_called()
                controller.stop_and_remove_requesters.assert_not_called()
                self.assertFalse(controller.journal.update.call_args.kwargs["dirty"])
                self.assertTrue(
                    controller.journal.update.call_args.kwargs[
                        "local_authorization_reconciled"
                    ]
                )

    def test_main_publishes_journal_and_intent_before_local_one_shot_consumption(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts/run_writer_witness_real_host_matrix.py"
        ).read_text(encoding="utf-8")
        main_source = source[source.index("def main(") :]
        controller_created = main_source.index("controller = Controller(")
        intent = main_source.index(
            'lifecycle_phase="local_authorization_consumption_intent"'
        )
        preflight_consumed = main_source.index("consumed_preflight = consume_preflight(")
        approval_consumed = main_source.index("consumed_approval = consume_approval(")
        self.assertLess(controller_created, intent)
        self.assertLess(intent, preflight_consumed)
        self.assertLess(preflight_consumed, approval_consumed)

    def test_cleanup_before_remote_claim_is_local_only_and_marks_journal_clean(self):
        controller = self._bare_cleanup_controller(claimed=False)
        controller.journal.payload = {
            "remote_campaign_protocol": "atomic-helper-v1",
            "lifecycle_phase": "controller_created",
        }
        controller.cleanup()
        for name in (
            "claim_remote_campaign",
            "assert_remote_campaign_owned",
            "stop_and_remove_requesters",
            "recover_rotation",
            "recover_active_live_restore",
            "release_remote_campaign",
        ):
            getattr(controller, name).assert_not_called()
        self.assertEqual(
            controller.journal.update.call_args.kwargs["status"],
            "completed_without_remote_claim",
        )
        self.assertFalse(controller.journal.update.call_args.kwargs["dirty"])

    def test_release_recovery_requires_exact_tombstone_before_absent_postflight(self):
        controller = object.__new__(runner.Controller)
        controller.inspect_remote_campaign = mock.Mock(
            side_effect=runner.MatrixError("tombstone missing")
        )
        controller.run_full_postflight = mock.Mock()
        controller.finalize_campaign = mock.Mock()
        controller.journal = mock.Mock()
        with self.assertRaisesRegex(runner.MatrixError, "ambiguous"):
            controller.recover_postflight_release_pending()
        controller.run_full_postflight.assert_not_called()
        self.assertEqual(
            controller.journal.update.call_args.kwargs["remote_campaign_release_state"],
            "ambiguous",
        )

        controller.journal.reset_mock()
        controller.inspect_remote_campaign.side_effect = None
        controller.inspect_remote_campaign.return_value = "released_exact"
        controller.recover_postflight_release_pending()
        controller.run_full_postflight.assert_called_once_with(
            expect_remote_campaign=False
        )
        self.assertEqual(controller.journal.update.call_args.kwargs["status"], "completed")
        self.assertFalse(controller.journal.update.call_args.kwargs["dirty"])

    def test_release_recovery_uses_real_helper_and_rejects_absence_or_mismatched_identity(self):
        helper = (
            Path(__file__).resolve().parents[1]
            / "deploy/writer-witness/writer-witness-matrix-campaign.py"
        )
        with tempfile.TemporaryDirectory(prefix="matrix-real-campaign-") as directory:
            state_root = Path(directory) / "state"

            def build_controller(*, expected_head: str = HEAD):
                controller = object.__new__(runner.Controller)
                controller.tag = "wwm_0123456789ab"
                controller.expected_head = expected_head
                controller.scenario = "RH-001"
                controller.remote_campaign_claimed = False
                controller.remote_campaign_conflict = False
                controller.remote_campaign_ambiguous = False
                controller.journal = mock.Mock()
                controller.journal.values.return_value = set()
                controller.run_full_postflight = mock.Mock()
                controller.finalize_campaign = mock.Mock()

                def local_remote(_role, _label, command, **_kwargs):
                    arguments = shlex.split(command)
                    helper_index = arguments.index(
                        "/usr/local/sbin/writer-witness-matrix-campaign"
                    )
                    return subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            "-S",
                            "-B",
                            "-X",
                            "utf8",
                            "-X",
                            "pycache_prefix=/dev/null",
                            str(helper),
                            "--test-mode",
                            "--state-root",
                            str(state_root),
                            *arguments[helper_index + 1 :],
                        ],
                        capture_output=True,
                        text=True,
                    )

                controller.remote = mock.Mock(side_effect=local_remote)
                return controller

            exact = build_controller()
            exact.claim_remote_campaign()
            released = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-X",
                    "utf8",
                    "-X",
                    "pycache_prefix=/dev/null",
                    str(helper),
                    "--test-mode",
                    "--state-root",
                    str(state_root),
                    "release",
                    "--tag",
                    exact.tag,
                    "--expected-commit",
                    exact.expected_head,
                    "--scenario",
                    exact.scenario,
                    "--not-after",
                    exact.campaign_not_after,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(released.returncode, 0, released.stderr)
            exact.recover_postflight_release_pending()
            exact.run_full_postflight.assert_called_once_with(
                expect_remote_campaign=False
            )

            mismatch = build_controller(expected_head="b" * 40)
            with self.assertRaisesRegex(runner.MatrixError, "exact active claim"):
                mismatch.recover_postflight_release_pending()
            mismatch.run_full_postflight.assert_not_called()

            absent_root = Path(directory) / "absent"
            absent_root.mkdir(mode=0o700)
            for name in (
                "releases",
                "authorization-intents",
                "authorizations",
                "consumed-approvals",
                "consumed-preflights",
            ):
                (absent_root / name).mkdir(mode=0o700)
            campaign_lock = absent_root / ".campaign.lock"
            campaign_lock.touch(mode=0o600)
            campaign_lock.chmod(0o600)
            absent = build_controller()

            def absent_remote(_role, _label, command, **_kwargs):
                arguments = shlex.split(command)
                helper_index = arguments.index(
                    "/usr/local/sbin/writer-witness-matrix-campaign"
                )
                return subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-S",
                        "-B",
                        "-X",
                        "utf8",
                        "-X",
                        "pycache_prefix=/dev/null",
                        str(helper),
                        "--test-mode",
                        "--state-root",
                        str(absent_root),
                        *arguments[helper_index + 1 :],
                    ],
                    capture_output=True,
                    text=True,
                )

            absent.remote = mock.Mock(side_effect=absent_remote)
            with self.assertRaisesRegex(runner.MatrixError, "exact active claim"):
                absent.recover_postflight_release_pending()
            absent.run_full_postflight.assert_not_called()

    def test_remote_marker_is_held_through_postflight_and_released_only_on_finalize(self):
        with tempfile.TemporaryDirectory(prefix="matrix-postflight-") as directory:
            controller = object.__new__(runner.Controller)
            controller.artifact_dir = Path(directory)
            controller.expected_head = HEAD
            controller.tag = "wwm_0123456789ab"
            controller.scenario = "RH-001"
            controller.journal = mock.Mock()
            controller.journal.payload = {"status": "cleanup_verified_pending_postflight"}
            controller.release_remote_campaign = mock.Mock()
            controller._reserve_transport_budget = mock.Mock()

            def complete_postflight(_name, args, **_kwargs):
                output = Path(args[args.index("--output") + 1])
                output.write_text(
                    json.dumps({"status": "preflight_passed"}) + "\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args, 0, "", "")

            controller.command = mock.Mock(side_effect=complete_postflight)
            controller.run_full_postflight()
            controller.release_remote_campaign.assert_not_called()
            self.assertEqual(controller._reserve_transport_budget.call_count, 4)
            self.assertEqual(
                {call.kwargs["role"] for call in controller._reserve_transport_budget.call_args_list},
                {"webapp_fi", "webapp_ir", "matrix_witness", "rollback_witness"},
            )
            self.assertTrue(
                all(
                    call.kwargs["force_master_reservation"] is True
                    for call in controller._reserve_transport_budget.call_args_list
                )
            )
            command_args = controller.command.call_args.args[1]
            self.assertIn("--expected-active-campaign-tag", command_args)
            self.assertIn("--expected-active-campaign-scenario", command_args)
            self.assertIn("--expected-active-campaign-not-after", command_args)
            self.assertIn(controller.tag, command_args)

            controller.journal.payload.update(
                status="postflight_verified_release_pending",
                lifecycle_phase="postflight_verified_release_pending",
            )
            ordered = mock.Mock()
            controller.assert_remote_campaign_owned = mock.Mock()
            ordered.attach_mock(controller.assert_remote_campaign_owned, "owned")
            ordered.attach_mock(controller.release_remote_campaign, "release")
            controller.finalize_campaign()
            self.assertEqual(
                [call[0] for call in ordered.mock_calls],
                ["owned", "release"],
            )

    def test_database_inventory_attestation_matches_preflight_wire_format(self):
        inventory = [
            {"name": "writer_witness_rollback_old", "oid": 17, "allow_connections": False},
            {"name": "writer_witness", "oid": 12, "allow_connections": True},
        ]
        expected = hashlib.sha256(
            b"writer_witness:12:true\nwriter_witness_rollback_old:17:false"
        ).hexdigest()
        self.assertEqual(runner.Controller.database_inventory_sha256(inventory), expected)

    @staticmethod
    def _restore_controller():
        controller = object.__new__(runner.Controller)
        controller.tag = "wwm_0123456789ab"
        controller.baseline = {
            "matrix_witness_dark_baseline": {
                "backup": "writer-witness-20260716T054228Z.dump",
                "backup_sha256": "d" * 64,
                "manifest_sha256": "b" * 64,
            }
        }
        controller.event = mock.Mock()
        controller.claim = mock.Mock()
        return controller

    def test_hard_killed_restore_runs_guarded_recovery_before_inspection(self):
        controller = self._restore_controller()
        current = {
            "state": {"writer_epoch": 1, "lease_status": "held"},
            "receipts": 3,
            "manifest_sha256": "c" * 64,
        }
        inventory = [
            {"name": "writer_witness", "oid": 16390, "allow_connections": True}
        ]
        controller.witness_state = mock.Mock(side_effect=[current, current])
        controller.database_inventory = mock.Mock(side_effect=[inventory, inventory])
        controller.remote = mock.Mock(
            side_effect=[
                subprocess.CompletedProcess([], 137, "", "killed"),
                subprocess.CompletedProcess([], 0, '{"status":"recovered"}\n', ""),
            ]
        )

        controller.restore_once(fail_after="candidate_created", expect_failure=True)

        recover_command = controller.remote.call_args_list[1].args[2]
        self.assertIn("writer-witness-live-restore --recover", recover_command)
        self.assertIn("WRITER_WITNESS_RESTORE_REQUIRED_CURRENT_STATE", recover_command)
        self.assertIn(".active.*.env", recover_command)
        self.assertIn(".replacement-restore.*.dump", recover_command)
        controller.claim.assert_not_called()

    def test_successful_restore_journals_exact_rollback_name_and_oid(self):
        controller = self._restore_controller()
        current = {
            "state": {"writer_epoch": 1, "lease_status": "held"},
            "receipts": 3,
            "manifest_sha256": "c" * 64,
        }
        rollback_name = "writer_witness_rollback_wwm_0123456789ab_20260717120000_42"
        before = [{"name": "writer_witness", "oid": 16390, "allow_connections": True}]
        after = [
            {"name": "writer_witness", "oid": 24500, "allow_connections": True},
            {"name": rollback_name, "oid": 16390, "allow_connections": False},
        ]
        controller.witness_state = mock.Mock(return_value=current)
        controller.database_inventory = mock.Mock(side_effect=[before, after])
        controller.remote = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "status": "restored-live-dark",
                        "rollback_database": rollback_name,
                    }
                )
                + "\n",
                "",
            )
        )

        controller.restore_once()

        controller.claim.assert_called_once_with(
            "restore_owned_databases", f"{rollback_name}:16390"
        )

    def test_aux_cleanup_refuses_tag_match_without_exact_journaled_oid(self):
        controller = self._restore_controller()
        controller.journal = mock.Mock()
        controller.journal.payload = {
            "initial_database_inventory": [
                {"name": "writer_witness", "oid": 16390, "allow_connections": True}
            ]
        }
        controller.journal.values.return_value = set()
        controller.database_inventory = mock.Mock(
            return_value=[
                {"name": "writer_witness", "oid": 16390, "allow_connections": True},
                {
                    "name": "writer_witness_rollback_wwm_0123456789ab_20260717120000_99",
                    "oid": 19000,
                    "allow_connections": False,
                },
            ]
        )
        controller.remote = mock.Mock()

        with self.assertRaisesRegex(runner.MatrixError, "lacks exact journaled OID"):
            controller.remove_owned_aux_databases()
        controller.remote.assert_not_called()

    def test_campaign_journal_never_publishes_an_empty_final_file(self):
        with tempfile.TemporaryDirectory(prefix="matrix-journal-create-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "wwm_0123456789ab.json"
            with (
                mock.patch.object(
                    runner,
                    "rename_noreplace",
                    side_effect=OSError("injected publication failure"),
                ),
                self.assertRaises(OSError),
            ):
                runner.CampaignJournal(
                    path,
                    {"schema_version": runner.RUNNER_SCHEMA, "dirty": True},
                    create=True,
                )
            self.assertFalse(path.exists())

    def test_dirty_gate_removes_only_strict_journal_temps(self):
        with tempfile.TemporaryDirectory(prefix="matrix-journal-temp-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            stale = root / ".wwm_0123456789ab.json.create-dead.tmp"
            stale.write_text("complete but unpublished\n", encoding="utf-8")
            stale.chmod(0o600)
            runner.assert_no_dirty_campaigns(root)
            self.assertFalse(stale.exists())

            foreign = root / ".wwm_0123456789ab.json.create-foreign.tmp"
            foreign.write_text("do not remove\n", encoding="utf-8")
            foreign.chmod(0o644)
            with self.assertRaisesRegex(runner.MatrixError, "unrecognized"):
                runner.assert_no_dirty_campaigns(root)
            self.assertTrue(foreign.exists())

    def test_artifact_path_is_one_canonical_direct_durable_child(self):
        with tempfile.TemporaryDirectory(prefix="matrix-artifact-root-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            accepted = root / "20260717T000000.000000Z-rh-001"
            self.assertEqual(
                runner.direct_durable_child(
                    accepted, root, label="Matrix artifact directory"
                ),
                accepted,
            )
            (root / "nested").mkdir(mode=0o700)
            with self.assertRaisesRegex(runner.MatrixError, "canonical direct child"):
                runner.direct_durable_child(
                    root / "nested" / ".." / "escape",
                    root,
                    label="Matrix artifact directory",
                )
            target = root / "real"
            target.mkdir(mode=0o700)
            link = root / "linked"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(runner.MatrixError, "symlink"):
                runner.direct_durable_child(
                    link, root, label="Matrix artifact directory"
                )

    def test_recovery_rehomes_missing_evidence_without_blocking_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="matrix-recover-") as directory:
            root = Path(directory)
            controller_root = root / "controller"
            artifact_root = controller_root / "runs"
            campaign_root = controller_root / "campaigns"
            for path in (controller_root, artifact_root, campaign_root):
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.chmod(0o700)
            tag = "wwm_0123456789ab"
            missing_artifact = artifact_root / "deleted-original-run"
            journal_path = campaign_root / f"{tag}.json"
            runner.CampaignJournal(
                journal_path,
                {
                    "schema_version": runner.RUNNER_SCHEMA,
                    "status": "failed",
                    "dirty": True,
                    "tag": tag,
                    "scenario": "RH-001",
                    "expected_commit": HEAD,
                    "operator": "operator",
                    "observer": "observer",
                    "incident_commander": "commander",
                    "reason": "recover test",
                    "dpi_byte_budget": runner.MIN_DPI_BYTE_BUDGET,
                    "max_scenario_seconds": runner.MAX_SCENARIO_SECONDS,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "artifact_dir": str(missing_artifact),
                    "baseline": passing_preflight()["observed_baseline"],
                    "resources": {},
                },
                create=True,
            )
            args = SimpleNamespace(mode="recover", campaign_journal=journal_path)
            fake_controller = mock.Mock()

            def git_value(*git_args):
                if git_args == ("branch", "--show-current"):
                    return runner.EXPECTED_BRANCH
                if git_args == ("rev-parse", "HEAD"):
                    return HEAD
                if git_args == ("status", "--porcelain"):
                    return ""
                raise AssertionError(git_args)

            with (
                mock.patch.object(runner, "parse_args", return_value=args),
                mock.patch.object(runner, "DEFAULT_CONTROLLER_ROOT", controller_root),
                mock.patch.object(runner, "DEFAULT_ARTIFACT_ROOT", artifact_root),
                mock.patch.object(runner, "DEFAULT_CAMPAIGN_ROOT", campaign_root),
                mock.patch.object(runner, "git_value", side_effect=git_value),
                mock.patch.object(runner, "acquire_local_lock", return_value=101),
                mock.patch.object(runner, "release_local_lock"),
                mock.patch.object(runner, "Controller", return_value=fake_controller) as constructor,
            ):
                self.assertEqual(runner.main(), 0)
            recovery_artifact = constructor.call_args.kwargs["artifact_dir"]
            self.assertNotEqual(recovery_artifact, missing_artifact)
            self.assertTrue(recovery_artifact.is_dir())
            persisted = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertTrue(persisted["evidence_loss_detected"])
            self.assertEqual(persisted["original_artifact_dir"], str(missing_artifact))
            fake_controller.enter_cleanup_mode.assert_not_called()
            fake_controller.cleanup.assert_called_once()

    def test_active_restore_journal_is_recovered_before_runtime_resume(self):
        controller = self._bare_cleanup_controller(claimed=True)
        controller.witness_mutated = False
        ordered = mock.Mock()
        ordered.attach_mock(controller.recover_active_live_restore, "recover_restore")
        ordered.attach_mock(controller.recover_rotation, "recover_hmac")
        ordered.attach_mock(controller.resume_witness_runtime, "resume_runtime")
        controller.cleanup()
        names = [call[0] for call in ordered.mock_calls]
        self.assertLess(names.index("recover_restore"), names.index("recover_hmac"))
        self.assertLess(names.index("recover_restore"), names.index("resume_runtime"))

        direct = object.__new__(runner.Controller)
        direct.remote = mock.Mock()
        direct.recover_active_live_restore()
        remote_command = direct.remote.call_args.args[2]
        self.assertIn("writer-witness-live-restore --recover", remote_command)
        self.assertIn("test -L", remote_command)
        self.assertIn(".replacement-restore.*.dump", remote_command)
        self.assertLess(
            remote_command.index("writer-witness-live-restore --recover"),
            remote_command.rindex("test ! -e"),
        )

    def test_evidence_intent_failure_prevents_new_remote_mutation(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = False
        controller._abort_reason = None
        controller.event = mock.Mock(return_value=False)
        with (
            mock.patch.object(runner.subprocess, "run") as run,
            self.assertRaisesRegex(runner.MatrixError, "evidence intent"),
        ):
            controller.command("must-not-run", ["ssh", "example", "mutate"])
        run.assert_not_called()

    def test_evidence_result_failure_aborts_before_a_second_mutation(self):
        controller = object.__new__(runner.Controller)
        controller._cleanup_mode = False
        controller._abort_reason = None
        controller.secret_output_detected = False
        controller._secret_sentinels = set()
        controller.event = mock.Mock(side_effect=(True, False))
        completed = subprocess.CompletedProcess(["ssh"], 0, "", "")
        with (
            mock.patch.object(runner.subprocess, "run", return_value=completed) as run,
            self.assertRaisesRegex(runner.MatrixError, "outcome is ambiguous"),
        ):
            controller.command("first-mutation", ["ssh", "example", "mutate"])
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
