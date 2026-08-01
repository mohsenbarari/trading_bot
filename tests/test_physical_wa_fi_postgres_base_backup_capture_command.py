from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.physical_wa_fi_postgres_base_backup_capture_command as capture_command
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
)
from core.physical_wal_base_backup_spool import PhysicalWalBaseBackupUploadReceipt


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "a" * 30
ARTIFACT = b"physical base backup payload" * 256
ATTESTATION = b"trusted local pg_basebackup completion attestation"


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_key(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class RecordingRunner:
    def __init__(self, *, exit_code: int = 0, completion_mode: str = "valid") -> None:
        self.exit_code = exit_code
        self.completion_mode = completion_mode
        self.calls: list[capture_command.PhysicalWaFiPostgresBaseBackupInvocation] = []

    def run(
        self,
        *,
        invocation: capture_command.PhysicalWaFiPostgresBaseBackupInvocation,
    ) -> capture_command.PhysicalWaFiPostgresBaseBackupRunnerResult:
        self.calls.append(invocation)
        if self.exit_code != 0:
            return capture_command.PhysicalWaFiPostgresBaseBackupRunnerResult(self.exit_code)
        if self.completion_mode == "missing":
            return capture_command.PhysicalWaFiPostgresBaseBackupRunnerResult(0)
        artifact = invocation.output_directory / "base.tar"
        attestation = invocation.output_directory / "completion.attestation"
        completion = invocation.output_directory / "completion.json"
        artifact.write_bytes(ARTIFACT)
        attestation.write_bytes(ATTESTATION)
        os.chmod(artifact, 0o600)
        os.chmod(attestation, 0o600)
        payload = {
            "schema": capture_command.PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_COMPLETION_SCHEMA,
            "version": 1,
            "status": "completed",
            "configuration_sha256": invocation.configuration_sha256,
            "command_sha256": invocation.command_sha256,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "artifact_filename": "base.tar",
            "plaintext_sha256": sha(ARTIFACT),
            "plaintext_bytes": len(ARTIFACT),
            "completion_attestation_sha256": sha(ATTESTATION),
            "writer_epoch": invocation.writer_epoch,
            "writer_lease_id": invocation.writer_lease_id,
            "witness_transition_id": invocation.witness_transition_id,
            "witnessed_term_proof_sha256": invocation.witnessed_term_proof_sha256,
        }
        if self.completion_mode == "corrupt":
            payload["plaintext_sha256"] = "f" * 64
        completion.write_bytes(canonical_json_bytes(payload))
        os.chmod(completion, 0o600)
        return capture_command.PhysicalWaFiPostgresBaseBackupRunnerResult(0)


class RecordingUploader:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bytes, str]] = []

    def upload(self, *, snapshot_path: Path, descriptor_bytes: bytes, descriptor_sha256: str):
        self.calls.append((snapshot_path, descriptor_bytes, descriptor_sha256))
        descriptor = json.loads(descriptor_bytes)
        return PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=descriptor["object_key"],
            version_id="base-version-20260731-0001",
            ciphertext_sha256=sha(b"synthetic ciphertext"),
            ciphertext_bytes=snapshot_path.stat().st_size + 64,
            encryption="age-v1",
            age_recipient=descriptor["destination_age_recipient"],
            immutability="versioned_create_only_readback_v1",
        )


class RecordingUploaderFactory:
    def __init__(self, uploader: RecordingUploader) -> None:
        self.uploader = uploader
        self.calls: list[tuple[object, object, object]] = []

    def __call__(self, *, config, age_encryptor_factory, object_storage_client_factory):
        self.calls.append((config, age_encryptor_factory, object_storage_client_factory))
        return self.uploader


@unittest.skipUnless(os.geteuid() == 0, "capture boundary requires root-owned fixtures")
class PhysicalWaFiPostgresBaseBackupCaptureCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-base-backup-command-")
        self.root = Path(self.temporary.name).resolve()
        self.capture_root = self.root / "capture"
        self.source_root = self.root / "completed-source"
        self.spool_root = self.root / "spool"
        self.workspace = self.root / "workspace"
        self.socket_directory = self.root / "postgres-socket"
        for directory in (
            self.capture_root,
            self.source_root,
            self.spool_root,
            self.workspace,
            self.socket_directory,
        ):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        self.command = self.root / "pg_basebackup"
        self.command.write_bytes(b"fixed synthetic postgresql 15 pg_basebackup")
        os.chmod(self.command, 0o755)
        self.config_path = self.root / "base-backup-capture.json"
        self.witness = Ed25519PrivateKey.generate()
        self.config = self._runtime_config()
        self._write_config(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _runtime_config(self, *, expires_at: datetime | None = None) -> dict:
        if expires_at is None:
            expires_at = NOW + timedelta(seconds=50)
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=73,
            writer_lease_id="writer-lease-73",
            witness_transition_id="witness-transition-73",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=expires_at,
            witness_signer=self.witness,
        )
        result = {
            "schema": capture_command.PHYSICAL_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_RUNTIME_SCHEMA,
            "version": 1,
            "enabled": True,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
            "capture": {
                "source_socket_transport": "unix-socket-only",
                "source_socket_directory": str(self.socket_directory),
                "source_port": 5432,
                "source_role": "replication",
                "password_prompt": "forbidden",
                "capture_root": str(self.capture_root),
                "completed_source_root": str(self.source_root),
                "completed_artifact_name": "wa-fi-basebackup-20260731.tar",
                "spool_root": str(self.spool_root),
                "maximum_base_backup_bytes": len(ARTIFACT),
                "spool_reserve_bytes": 1,
                "pg_basebackup_sha256": sha(self.command.read_bytes()),
            },
            "manifest_binding": {
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "campaign_id": "physical-base-command-20260731",
                "release_sha": RELEASE,
                "baseline_generation_id": "physical-base-command-generation-20260731",
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": 16 * 1024 * 1024,
                "baseline_wal_lsn": "0/1800000",
                "wal_chain_start_lsn": "0/1000000",
                "base_backup_end_lsn": "0/2800000",
                "destination_age_recipient": RECIPIENT,
            },
            "witness_term": {
                "public_key_base64": base64.b64encode(public_key(self.witness)).decode("ascii"),
                "maximum_lease_duration_seconds": 90,
                "safety_margin_seconds": 5,
                "proof": proof,
            },
            "object_storage_uploader": {
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "workspace": str(self.workspace),
                "spool_root": str(self.spool_root),
                "spool_owner_uid": 0,
                "bucket": "private-physical-backups",
                "region": "ir-thr-at1",
                "destination_age_recipient": RECIPIENT,
                "enabled": True,
                "maximum_plaintext_bytes": len(ARTIFACT),
                "direct_site_control": "forbidden",
                "destination_object_ingest": "pull-only",
            },
        }
        result["configuration_sha256"] = sha(canonical_json_bytes(result))
        return result

    def _write_config(self, value: dict) -> None:
        self.config_path.write_bytes(canonical_json_bytes(value))
        os.chmod(self.config_path, 0o600)

    def _execute(
        self,
        *,
        runner: RecordingRunner,
        factory: RecordingUploaderFactory,
        now: datetime = NOW,
        clock=lambda: NOW,
        arguments: object = (),
        age_factory=None,
        object_storage_factory=None,
    ):
        if age_factory is None:
            age_factory = lambda: (_ for _ in ()).throw(AssertionError("age should remain injected"))
        if object_storage_factory is None:
            object_storage_factory = lambda: (_ for _ in ()).throw(AssertionError("S3 should remain injected"))
        with (
            patch.object(capture_command, "FIXED_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG", self.config_path),
            patch.object(capture_command, "FIXED_WA_FI_PG_BASEBACKUP_COMMAND", self.command),
        ):
            return capture_command.execute_wa_fi_postgres_base_backup_capture_command(
                arguments,
                now=now,
                term_recheck_clock=clock,
                runner=runner,
                age_encryptor_factory=age_factory,
                object_storage_client_factory=object_storage_factory,
                uploader_factory=factory,
            )

    def test_fixed_unix_socket_invocation_reaches_completed_artifact_spool(self) -> None:
        runner = RecordingRunner()
        uploader = RecordingUploader()
        factory = RecordingUploaderFactory(uploader)
        age_factory = lambda: None
        object_storage_factory = lambda: None

        result = self._execute(
            runner=runner,
            factory=factory,
            age_factory=age_factory,
            object_storage_factory=object_storage_factory,
        )

        self.assertEqual(1, len(runner.calls))
        invocation = runner.calls[0]
        self.assertEqual(self.command, invocation.command_path)
        self.assertEqual((), invocation.environment)
        self.assertEqual(
            (
                str(self.command),
                "--host=" + str(self.socket_directory),
                "--port=5432",
                "--username=replication",
                "--no-password",
                "--format=tar",
                "--wal-method=none",
                "--checkpoint=fast",
                "--pgdata=" + str(invocation.output_directory),
            ),
            invocation.arguments,
        )
        self.assertEqual(1, len(factory.calls))
        self.assertIs(age_factory, factory.calls[0][1])
        self.assertIs(object_storage_factory, factory.calls[0][2])
        self.assertEqual(1, len(uploader.calls))
        self.assertEqual(sha(ARTIFACT), result.completed_artifact_sha256)
        self.assertEqual(len(ARTIFACT), result.completed_artifact_bytes)
        self.assertEqual(sha(ATTESTATION), result.completion_attestation_sha256)
        self.assertEqual(ARTIFACT, (self.source_root / "wa-fi-basebackup-20260731.tar").read_bytes())
        self.assertEqual("base-version-20260731-0001", result.object_version_id)

    def test_malformed_or_symlink_runtime_config_never_touches_runner_or_uploader(self) -> None:
        runner = RecordingRunner()
        uploader = RecordingUploader()
        factory = RecordingUploaderFactory(uploader)
        self.config_path.write_bytes(b"{}")
        os.chmod(self.config_path, 0o600)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "CONFIG"):
            self._execute(runner=runner, factory=factory)
        self.assertEqual([], runner.calls)
        self.assertEqual([], factory.calls)
        self._write_config(self.config)
        link = self.root / "config-link.json"
        link.symlink_to(self.config_path)
        with (
            patch.object(capture_command, "FIXED_WA_FI_POSTGRES_BASE_BACKUP_CAPTURE_CONFIG", link),
            patch.object(capture_command, "FIXED_WA_FI_PG_BASEBACKUP_COMMAND", self.command),
        ):
            with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "UNSAFE"):
                capture_command.execute_wa_fi_postgres_base_backup_capture_command(
                    (),
                    now=NOW,
                    term_recheck_clock=lambda: NOW,
                    runner=runner,
                    age_encryptor_factory=lambda: None,  # type: ignore[return-value]
                    object_storage_client_factory=lambda: None,  # type: ignore[return-value]
                    uploader_factory=factory,
                )
        self.assertEqual([], runner.calls)
        self.assertEqual([], factory.calls)

    def test_wrong_direction_direct_policy_and_unsafe_source_are_rejected_before_runner(self) -> None:
        runner = RecordingRunner()
        uploader = RecordingUploader()
        factory = RecordingUploaderFactory(uploader)
        invalid_direction = self._runtime_config()
        invalid_direction["destination_site"] = "webapp_fi"
        invalid_direction["configuration_sha256"] = sha(canonical_json_bytes({k: v for k, v in invalid_direction.items() if k != "configuration_sha256"}))
        self._write_config(invalid_direction)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "DIRECTION"):
            self._execute(runner=runner, factory=factory)
        invalid_direct = self._runtime_config()
        invalid_direct["direct_site_control"] = "allowed"
        invalid_direct["configuration_sha256"] = sha(canonical_json_bytes({k: v for k, v in invalid_direct.items() if k != "configuration_sha256"}))
        self._write_config(invalid_direct)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "DIRECTION"):
            self._execute(runner=runner, factory=factory)
        unsafe_source = self.root / "unsafe-source"
        unsafe_source.symlink_to(self.source_root, target_is_directory=True)
        invalid_source = self._runtime_config()
        invalid_source["capture"]["completed_source_root"] = str(unsafe_source)
        invalid_source["configuration_sha256"] = sha(canonical_json_bytes({k: v for k, v in invalid_source.items() if k != "configuration_sha256"}))
        self._write_config(invalid_source)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "SOURCE_ROOT_UNSAFE"):
            self._execute(runner=runner, factory=factory)
        self.assertEqual([], runner.calls)
        self.assertEqual([], factory.calls)

    def test_nonzero_runner_and_missing_or_corrupt_completion_never_touch_uploader(self) -> None:
        for runner in (
            RecordingRunner(exit_code=17),
            RecordingRunner(completion_mode="missing"),
            RecordingRunner(completion_mode="corrupt"),
        ):
            with self.subTest(mode=runner.completion_mode, exit_code=runner.exit_code):
                uploader = RecordingUploader()
                factory = RecordingUploaderFactory(uploader)
                with self.assertRaises(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError):
                    self._execute(runner=runner, factory=factory)
                self.assertEqual(1, len(runner.calls))
                self.assertEqual([], factory.calls)
                self.assertEqual([], uploader.calls)

    def test_term_expiry_during_runner_is_fail_closed_before_uploader(self) -> None:
        self.config = self._runtime_config(expires_at=NOW + timedelta(seconds=8))
        self._write_config(self.config)
        runner = RecordingRunner()
        uploader = RecordingUploader()
        factory = RecordingUploaderFactory(uploader)

        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "WITNESS"):
            self._execute(
                runner=runner,
                factory=factory,
                clock=lambda: NOW + timedelta(seconds=20),
            )
        self.assertEqual(1, len(runner.calls))
        self.assertEqual([], factory.calls)
        self.assertEqual([], uploader.calls)

    def test_arguments_and_dependency_validation_precede_runner(self) -> None:
        runner = RecordingRunner()
        uploader = RecordingUploader()
        factory = RecordingUploaderFactory(uploader)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "ARGUMENTS"):
            self._execute(runner=runner, factory=factory, arguments=("--host=example",))
        self.assertEqual([], runner.calls)
        self.assertEqual([], factory.calls)
        disabled = self._runtime_config()
        disabled["enabled"] = False
        disabled["configuration_sha256"] = sha(
            canonical_json_bytes({key: value for key, value in disabled.items() if key != "configuration_sha256"})
        )
        self._write_config(disabled)
        with self.assertRaisesRegex(capture_command.PhysicalWaFiPostgresBaseBackupCaptureCommandError, "DISABLED"):
            self._execute(runner=runner, factory=factory)
        self.assertEqual([], runner.calls)
        self.assertEqual([], factory.calls)


if __name__ == "__main__":
    unittest.main()
