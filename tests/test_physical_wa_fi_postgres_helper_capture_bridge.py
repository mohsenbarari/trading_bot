"""Local-only contracts for the helper-container base-backup bridge."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_postgres_deployment_scaffold import canonical_json_bytes
import core.physical_wa_fi_postgres_helper_capture_bridge as bridge
import core.physical_wa_fi_postgres_helper_container as helper_container
from core.physical_wal_base_backup_spool import PhysicalWalBaseBackupManifestBinding


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "a" * 30
ARTIFACT = b"helper-container physical base backup" * 512
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wa_fi_postgres_helper_capture_bridge.py"
)


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def public_key(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class RecordingHelperRunner:
    """Only identity plumbing; it never starts a process in this test."""

    def run(self, *, invocation):  # pragma: no cover - fake helper does not execute it
        raise AssertionError("the bridge must not execute the runner itself")


class RecordingHelperExecutor:
    """Test seam that emulates only the reviewed helper's collected artifact."""

    def __init__(self, *, manifest_lock_sha256: str, bad_path: bool = False) -> None:
        self.manifest_lock_sha256 = manifest_lock_sha256
        self.bad_path = bad_path
        self.calls: list[tuple[object, object, object]] = []

    def __call__(self, arguments, *, request, runner):
        self.calls.append((arguments, request, runner))
        artifact = request.capture_output_root / "base.tar"
        artifact.write_bytes(ARTIFACT)
        artifact.chmod(0o600)
        collected = artifact
        if self.bad_path:
            collected = request.capture_output_root / "wrong.tar"
        return helper_container.PhysicalWaFiPostgresHelperContainerResult(
            configuration_sha256="1" * 64,
            installation_attestation_sha256="2" * 64,
            capture_configuration_sha256=request.capture_configuration_sha256,
            deployment_manifest_lock_sha256=self.manifest_lock_sha256,
            local_base_backup_auth_preflight_sha256="3" * 64,
            invocation_sha256="5" * 64,
            collected_artifact_path=collected,
            collected_artifact_sha256=sha(ARTIFACT),
            collected_artifact_bytes=len(ARTIFACT),
        )


class PhysicalWaFiPostgresHelperCaptureBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="helper-capture-bridge-")
        self.root = Path(self.temporary.name).resolve()
        self.capture_parent = self.root / "capture"
        self.evidence_root = self.root / "evidence"
        self.capture_parent.mkdir(mode=0o700)
        self.evidence_root.mkdir(mode=0o700)
        os.chmod(self.capture_parent, 0o700)
        os.chmod(self.evidence_root, 0o700)
        self.term = self._term()
        self.request = self._strict_request()
        self.observation = self._strict_observation()
        self.manifest_binding = PhysicalWalBaseBackupManifestBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id="helper-bridge-20260731",
            release_sha=RELEASE,
            baseline_generation_id="helper-bridge-generation-20260731",
            database_system_identifier="7392847193847192834",
            timeline_id=1,
            wal_segment_size_bytes=16 * 1024 * 1024,
            baseline_wal_lsn="0/1800000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/2800000",
            destination_age_recipient=RECIPIENT,
        )
        self.capture_configuration_sha256 = "6" * 64

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _term(self):
        signer = Ed25519PrivateKey.generate()
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=73,
            writer_lease_id="writer-lease-73",
            witness_transition_id="witness-transition-73",
            issued_at=NOW - timedelta(seconds=5),
            expires_at=NOW + timedelta(seconds=60),
            witness_signer=signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public_key(signer),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )

    def _writer_term_sha256(self) -> str:
        return sha(
            canonical_json_bytes(
                {
                    "holder_site": self.term.holder_site,
                    "writer_epoch": self.term.writer_epoch,
                    "writer_lease_id": self.term.writer_lease_id,
                    "witness_transition_id": self.term.witness_transition_id,
                    "term_proof_sha256": self.term.proof_sha256,
                }
            )
        )

    def _strict_request(self, **changes: object):
        result: dict[str, object] = {
            "campaign_id": "helper-bridge-20260731",
            "release_sha": RELEASE,
            "manifest_lock_sha256": "a" * 64,
            "route_binding_sha256": "b" * 64,
            "writer_term_sha256": self._writer_term_sha256(),
            "installation_binding_sha256": "c" * 64,
            "request_sha256": "d" * 64,
        }
        result.update(changes)
        return SimpleNamespace(**result)

    def _strict_observation(self, **changes: object):
        result: dict[str, object] = {
            "installation_binding_sha256": self.request.installation_binding_sha256,
            "request_sha256": self.request.request_sha256,
            "attestation_sha256es": (
                ("wa_fi_local_wal_archive_capture", "e" * 64),
                ("encrypted_private_versioned_object_storage_publish_receipt", "f" * 64),
                ("witness_locator_ledger", "1" * 64),
                ("writer_response_commit_boundary", "2" * 64),
            ),
            "verified_at": NOW - timedelta(seconds=1),
            "expires_at": NOW + timedelta(seconds=30),
        }
        result.update(changes)
        return SimpleNamespace(**result)

    @contextmanager
    def _strict_gate(self, *, reject_after: datetime | None = None):
        def request_normalizer(value):
            self.assertIs(value, self.request)
            return value

        def observation_normalizer(value, *, request, now):
            self.assertIs(value, self.observation)
            self.assertIs(request, self.request)
            if reject_after is not None and now >= reject_after:
                raise RuntimeError("stale test observation")
            return value

        with (
            patch.object(
                bridge,
                "require_physical_postgres_strict_runtime_installation_request",
                side_effect=request_normalizer,
            ),
            patch.object(
                bridge,
                "require_verified_physical_postgres_strict_runtime_installations",
                side_effect=observation_normalizer,
            ),
        ):
            yield

    @contextmanager
    def _roots(self):
        with patch.multiple(
            bridge,
            FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_CAPTURE_ROOT=self.capture_parent,
            FIXED_WA_FI_POSTGRES_HELPER_CAPTURE_BRIDGE_EVIDENCE_ROOT=self.evidence_root,
        ):
            yield

    def _control(self):
        with self._strict_gate():
            return bridge.build_physical_wa_fi_postgres_helper_capture_bridge_control(
                strict_installation_request=self.request,  # type: ignore[arg-type]
                strict_installation_observation=self.observation,  # type: ignore[arg-type]
                manifest_binding=self.manifest_binding,
                witnessed_term=self.term,
                capture_configuration_sha256=self.capture_configuration_sha256,
                now=NOW,
            )

    def _execute(
        self,
        *,
        control=None,
        executor: RecordingHelperExecutor | None = None,
        clock=lambda: NOW + timedelta(seconds=1),
    ):
        actual_control = self._control() if control is None else control
        actual_executor = (
            RecordingHelperExecutor(manifest_lock_sha256=self.request.manifest_lock_sha256)
            if executor is None
            else executor
        )
        runner = RecordingHelperRunner()
        config = bridge.PhysicalWaFiPostgresHelperCaptureBridgeConfig(
            control=actual_control, enabled=True
        )
        with (
            self._strict_gate(),
            self._roots(),
            patch.object(
                bridge,
                "execute_wa_fi_postgres_helper_container_capture",
                side_effect=actual_executor,
            ),
            patch.object(bridge.os, "geteuid", return_value=0),
        ):
            result = bridge.execute_physical_wa_fi_postgres_helper_capture_bridge(
                config=config,
                now=NOW,
                completion_recheck_clock=clock,
                helper_runner=runner,
            )
        return result, actual_executor, runner

    def test_only_helper_container_runner_is_forwarded_and_result_is_spool_handoff(self) -> None:
        result, executor, runner = self._execute()

        self.assertEqual(1, len(executor.calls))
        arguments, request, supplied_runner = executor.calls[0]
        self.assertEqual((), arguments)
        self.assertIs(runner, supplied_runner)
        self.assertEqual(self.capture_configuration_sha256, request.capture_configuration_sha256)
        self.assertEqual(self.term.writer_epoch, request.writer_epoch)
        self.assertEqual(self.term.writer_lease_id, request.writer_lease_id)
        self.assertEqual(self.term.witness_transition_id, request.witness_transition_id)
        self.assertEqual(self.term.proof_sha256, request.witnessed_term_proof_sha256)
        self.assertEqual(result.capture_source_root, request.capture_output_root)
        self.assertEqual("base.tar", result.completed_artifact.artifact_name)
        self.assertEqual(sha(ARTIFACT), result.completed_artifact.plaintext_sha256)
        self.assertEqual(len(ARTIFACT), result.completed_artifact.plaintext_bytes)
        self.assertEqual(0o700, result.capture_source_root.stat().st_mode & 0o777)
        self.assertEqual(0o600, (result.capture_source_root / "base.tar").stat().st_mode & 0o777)

        raw = result.completion_receipt_path.read_bytes()
        self.assertEqual(raw, result.canonical_completion_receipt)
        self.assertEqual(sha(raw), result.completion_receipt_sha256)
        self.assertEqual(0o600, result.completion_receipt_path.stat().st_mode & 0o777)
        completion = json.loads(raw)
        self.assertFalse(completion["object_storage_handoff_performed"])
        self.assertTrue(completion["not_an_object_storage_upload"])
        self.assertTrue(completion["not_a_release_authorization"])
        self.assertTrue(completion["not_a_launch_authorization"])
        self.assertTrue(completion["not_a_writer_authorization"])
        self.assertTrue(completion["not_a_promotion_authorization"])
        self.assertNotIn(RECIPIENT, raw.decode("ascii"))
        self.assertNotIn(str(result.capture_source_root), raw.decode("ascii"))
        attestation = completion["capture_attestation"]
        self.assertEqual(self.request.campaign_id, attestation["campaign_id"])
        self.assertEqual(RELEASE, attestation["release_sha"])
        self.assertEqual(self.request.manifest_lock_sha256, attestation["deployment_manifest_lock_sha256"])
        self.assertEqual(self.term.writer_epoch, attestation["writer_witness"]["writer_epoch"])
        self.assertEqual("5" * 64, attestation["helper"]["invocation_sha256"])
        self.assertEqual(sha(ARTIFACT), attestation["artifact"]["plaintext_sha256"])
        self.assertEqual(len(ARTIFACT), attestation["artifact"]["plaintext_bytes"])
        self.assertEqual(
            result.capture_attestation_sha256,
            result.completed_artifact.completion_attestation_sha256,
        )

        with self._strict_gate(), self._roots():
            self.assertIs(
                result,
                bridge.require_physical_wa_fi_postgres_helper_capture_bridge_handoff(
                    result, now=NOW + timedelta(seconds=1)
                ),
            )

    def test_disabled_or_nonroot_never_calls_helper(self) -> None:
        executor = RecordingHelperExecutor(manifest_lock_sha256=self.request.manifest_lock_sha256)
        config = bridge.PhysicalWaFiPostgresHelperCaptureBridgeConfig(
            control=self._control(), enabled=False
        )
        with self.assertRaisesRegex(
            bridge.PhysicalWaFiPostgresHelperCaptureBridgeError, "DISABLED"
        ):
            bridge.execute_physical_wa_fi_postgres_helper_capture_bridge(
                config=config,
                now=NOW,
                completion_recheck_clock=lambda: NOW,
                helper_runner=RecordingHelperRunner(),
            )
        self.assertEqual([], executor.calls)

        enabled = bridge.PhysicalWaFiPostgresHelperCaptureBridgeConfig(
            control=self._control(), enabled=True
        )
        with (
            self._roots(),
            patch.object(
                bridge,
                "execute_wa_fi_postgres_helper_container_capture",
                side_effect=executor,
            ),
            patch.object(bridge.os, "geteuid", return_value=1000),
        ):
            with self.assertRaisesRegex(
                bridge.PhysicalWaFiPostgresHelperCaptureBridgeError, "ROOT_RUNTIME_REQUIRED"
            ):
                bridge.execute_physical_wa_fi_postgres_helper_capture_bridge(
                    config=enabled,
                    now=NOW,
                    completion_recheck_clock=lambda: NOW,
                    helper_runner=RecordingHelperRunner(),
                )
        self.assertEqual([], executor.calls)

    def test_binding_mismatch_rejected_before_helper(self) -> None:
        self.request = self._strict_request(release_sha="f" * 40)
        self.observation = self._strict_observation()
        executor = RecordingHelperExecutor(manifest_lock_sha256=self.request.manifest_lock_sha256)
        with self._strict_gate():
            with self.assertRaisesRegex(
                bridge.PhysicalWaFiPostgresHelperCaptureBridgeError, "BINDING_MISMATCH"
            ):
                bridge.build_physical_wa_fi_postgres_helper_capture_bridge_control(
                    strict_installation_request=self.request,  # type: ignore[arg-type]
                    strict_installation_observation=self.observation,  # type: ignore[arg-type]
                    manifest_binding=self.manifest_binding,
                    witnessed_term=self.term,
                    capture_configuration_sha256=self.capture_configuration_sha256,
                    now=NOW,
                )
        self.assertEqual([], executor.calls)

    def test_post_helper_strict_observation_recheck_failure_never_issues_receipt(self) -> None:
        control = self._control()
        executor = RecordingHelperExecutor(manifest_lock_sha256=self.request.manifest_lock_sha256)
        config = bridge.PhysicalWaFiPostgresHelperCaptureBridgeConfig(control=control, enabled=True)
        with (
            self._strict_gate(reject_after=NOW + timedelta(seconds=1)),
            self._roots(),
            patch.object(
                bridge,
                "execute_wa_fi_postgres_helper_container_capture",
                side_effect=executor,
            ),
            patch.object(bridge.os, "geteuid", return_value=0),
        ):
            with self.assertRaisesRegex(
                bridge.PhysicalWaFiPostgresHelperCaptureBridgeError,
                "STRICT_RUNTIME_NOT_ATTESTED",
            ):
                bridge.execute_physical_wa_fi_postgres_helper_capture_bridge(
                    config=config,
                    now=NOW,
                    completion_recheck_clock=lambda: NOW + timedelta(seconds=1),
                    helper_runner=RecordingHelperRunner(),
                )
        self.assertEqual(1, len(executor.calls))
        self.assertEqual([], list(self.evidence_root.iterdir()))

    def test_unsafe_helper_path_or_tampered_atomic_receipt_fails_closed(self) -> None:
        bad = RecordingHelperExecutor(
            manifest_lock_sha256=self.request.manifest_lock_sha256,
            bad_path=True,
        )
        with self.assertRaisesRegex(
            bridge.PhysicalWaFiPostgresHelperCaptureBridgeError, "HELPER_RESULT_INVALID"
        ):
            self._execute(executor=bad)
        self.assertEqual([], list(self.evidence_root.iterdir()))

        result, _executor, _runner = self._execute()
        result.completion_receipt_path.write_bytes(b"{}\n")
        result.completion_receipt_path.chmod(0o600)
        with self._strict_gate(), self._roots():
            with self.assertRaisesRegex(
                bridge.PhysicalWaFiPostgresHelperCaptureBridgeError,
                "HANDOFF_INVALID",
            ):
                bridge.require_physical_wa_fi_postgres_helper_capture_bridge_handoff(
                    result, now=NOW + timedelta(seconds=1)
                )

    def test_exact_identical_receipt_is_create_only_idempotent(self) -> None:
        first, _first_executor, _ = self._execute(clock=lambda: NOW)
        inode = first.completion_receipt_path.stat().st_ino
        second, _second_executor, _ = self._execute(clock=lambda: NOW)
        self.assertEqual(first.completion_receipt_path, second.completion_receipt_path)
        self.assertEqual(first.completion_receipt_sha256, second.completion_receipt_sha256)
        self.assertEqual(inode, second.completion_receipt_path.stat().st_ino)

    def test_module_has_no_legacy_host_capture_or_effectful_client_imports(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_modules.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        forbidden = (
            "subprocess",
            "socket",
            "requests",
            "boto3",
            "paramiko",
            "docker",
            "physical_wa_fi_postgres_base_backup_capture_command",
        )
        self.assertFalse(
            any(
                module == name or module.endswith("." + name)
                for module in imported_modules
                for name in forbidden
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
