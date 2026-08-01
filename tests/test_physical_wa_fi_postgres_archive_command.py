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

from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
import core.physical_wa_fi_postgres_archive_command as adapter
from core.physical_wal_archive_spool import (
    PhysicalWalArchiveManifestBinding,
    PhysicalWalArchiveUploadReceipt,
    authorize_physical_wal_archive_binding,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
SEGMENT_SIZE = 16 * 1024 * 1024
SEGMENT_NAME = "000000010000000000000001"
RECIPIENT = "age1" + "a" * 30


def public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _NeverInvokeFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("factory must not run in adapter-boundary tests")


class _RecordingUploader:
    constructed: list["_RecordingUploader"] = []

    def __init__(
        self,
        *,
        config,
        age_encryptor_factory,
        client_factory,
    ) -> None:
        self.config = config
        self.age_encryptor_factory = age_encryptor_factory
        self.client_factory = client_factory
        self.uploads: list[tuple[Path, bytes, str]] = []
        self.__class__.constructed.append(self)

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        self.uploads.append((snapshot_path, descriptor_bytes, descriptor_sha256))
        descriptor = json.loads(descriptor_bytes.decode("ascii"))
        return PhysicalWalArchiveUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=descriptor["object_key"],
            version_id="version-20260731-01",
            ciphertext_sha256="c" * 64,
            ciphertext_bytes=snapshot_path.stat().st_size + 128,
            encryption="age-v1",
            age_recipient=descriptor["destination_age_recipient"],
            immutability="versioned_create_only_readback_v1",
        )


@unittest.skipUnless(os.geteuid() == 0, "root-only archive-command tests require root")
class PhysicalWaFiPostgresArchiveCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        _RecordingUploader.constructed = []
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-pg-archive-command-")
        self.root = Path(self.temporary.name).resolve()
        self.config_dir = self._directory("config", mode=0o700)
        self.source_root = self._directory("pg_wal", mode=0o700)
        self.spool_root = self._directory("spool", mode=0o700)
        self.workspace = self._directory("workspace", mode=0o700)
        self.config_path = self.config_dir / "wal-spool.json"
        self.wal_path = self.source_root / SEGMENT_NAME
        self.wal_path.write_bytes(b"W" * SEGMENT_SIZE)
        os.chmod(self.wal_path, 0o600)
        self.witness = Ed25519PrivateKey.generate()
        self.age_factory = _NeverInvokeFactory()
        self.s3_factory = _NeverInvokeFactory()
        self._write_config(self._runtime_config())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _directory(self, name: str, *, mode: int) -> Path:
        value = self.root / name
        value.mkdir(mode=mode)
        os.chmod(value, mode)
        return value.resolve()

    def _term_proof(
        self,
        *,
        issued_at: datetime = NOW - timedelta(seconds=10),
        expires_at: datetime = NOW + timedelta(seconds=50),
    ) -> dict[str, object]:
        return build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witness_transition_id="witness-transition-41",
            issued_at=issued_at,
            expires_at=expires_at,
            witness_signer=self.witness,
        )

    @staticmethod
    def _manifest_mapping() -> dict[str, object]:
        return {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "campaign_id": "wal-command-20260731",
            "release_sha": "3138d0c2a8d20a84042c3a438fbc88db7a4db498",
            "stream_generation_id": "fi-ir-command-stream-20260731",
            "baseline_generation_id": "fi-ir-command-base-20260731",
            "baseline_manifest_sha256": "a" * 64,
            "baseline_wal_lsn": "0/1800000",
            "wal_chain_start_lsn": "0/1000000",
            "archive_manifest_sha256": "b" * 64,
            "database_system_identifier": "7392847193847192834",
            "timeline_id": 1,
            "destination_age_recipient": RECIPIENT,
        }

    def _runtime_config(
        self,
        *,
        proof: dict[str, object] | None = None,
        enabled: bool = True,
        validation_now: datetime = NOW,
    ) -> dict[str, object]:
        proof = proof or self._term_proof()
        term = verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public_key(self.witness),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=validation_now,
        )
        manifest = self._manifest_mapping()
        verified_binding = authorize_physical_wal_archive_binding(
            manifest_binding=PhysicalWalArchiveManifestBinding(**manifest),
            witnessed_term=term,
            now=validation_now,
        )
        payload: dict[str, object] = {
            "schema": adapter.PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA,
            "version": 1,
            "enabled": enabled,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "archive_spool": {
                "wal_source_root": str(self.source_root),
                "spool_root": str(self.spool_root),
                "wal_segment_size_bytes": SEGMENT_SIZE,
            },
            "manifest_binding": manifest,
            "route_binding_sha256": verified_binding.route_binding_sha256,
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
                "bucket": "physical-wal-test",
                "region": "ir-thr-at1",
                "destination_age_recipient": RECIPIENT,
                "enabled": True,
                "maximum_plaintext_bytes": SEGMENT_SIZE,
                "direct_site_control": "forbidden",
                "destination_object_ingest": "pull-only",
            },
        }
        payload["configuration_sha256"] = hashlib.sha256(
            canonical_json_bytes(payload)
        ).hexdigest()
        return payload

    def _write_config(self, payload: dict[str, object]) -> None:
        self.config_path.write_bytes(canonical_json_bytes(payload))
        os.chmod(self.config_path, 0o600)

    @staticmethod
    def _re_pin_config(payload: dict[str, object]) -> None:
        unpinned = dict(payload)
        del unpinned["configuration_sha256"]
        payload["configuration_sha256"] = hashlib.sha256(
            canonical_json_bytes(unpinned)
        ).hexdigest()

    def _arguments(self, *, config_path: Path | None = None, wal_path: Path | None = None) -> list[str]:
        return [
            "--config",
            str(config_path or self.config_path),
            "--wal-file",
            SEGMENT_NAME,
            "--wal-path",
            str(wal_path or self.wal_path),
        ]

    def _execute(self, arguments: list[str] | None = None, *, clock=None):
        with (
            patch.object(adapter, "FIXED_WA_FI_POSTGRES_ARCHIVE_COMMAND_CONFIG", self.config_path),
            patch.object(adapter, "PhysicalWalObjectStorageUploader", _RecordingUploader),
        ):
            return adapter.execute_wa_fi_postgres_archive_command(
                arguments or self._arguments(),
                now=NOW,
                term_recheck_clock=clock or (lambda: NOW),
                age_encryptor_factory=self.age_factory,
                object_storage_client_factory=self.s3_factory,
            )

    def _assert_nothing_external_constructed(self) -> None:
        self.assertEqual([], _RecordingUploader.constructed)
        self.assertEqual(0, self.age_factory.calls)
        self.assertEqual(0, self.s3_factory.calls)

    def test_exact_fixed_cli_wires_spool_to_uploader_with_explicit_factories(self) -> None:
        result = self._execute()

        self.assertEqual(SEGMENT_NAME, result.wal_segment_name)
        self.assertEqual(1, len(_RecordingUploader.constructed))
        uploader = _RecordingUploader.constructed[0]
        self.assertIs(self.age_factory, uploader.age_encryptor_factory)
        self.assertIs(self.s3_factory, uploader.client_factory)
        self.assertEqual("webapp_fi", uploader.config.source_site)
        self.assertEqual("webapp_ir", uploader.config.destination_site)
        self.assertEqual(1, len(uploader.uploads))
        self.assertEqual(0, self.age_factory.calls)
        self.assertEqual(0, self.s3_factory.calls)
        rendered = adapter.render_wa_fi_postgres_archive_command_result(result)
        self.assertNotIn(str(self.source_root).encode(), rendered)
        self.assertNotIn(str(self.spool_root).encode(), rendered)
        self.assertNotIn(RECIPIENT.encode(), rendered)
        self.assertNotIn(b"physical-wal-test", rendered)

    def test_invalid_config_path_and_term_never_construct_or_invoke_uploader_factories(self) -> None:
        pin_mismatch = self._runtime_config()
        pin_mismatch["configuration_sha256"] = "f" * 64
        self._write_config(pin_mismatch)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_RUNTIME_CONFIG_PIN_INVALID",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

        disabled = self._runtime_config(enabled=False)
        self._write_config(disabled)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_RUNTIME_DISABLED",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

        invalid_uploader_policy = self._runtime_config()
        invalid_uploader_policy["object_storage_uploader"]["direct_site_control"] = "allowed"
        self._re_pin_config(invalid_uploader_policy)
        self._write_config(invalid_uploader_policy)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_UPLOADER_CONFIG_INVALID",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

        expired = self._term_proof(
            issued_at=NOW - timedelta(seconds=20),
            expires_at=NOW - timedelta(seconds=1),
        )
        self._write_config(
            self._runtime_config(
                proof=expired,
                validation_now=NOW - timedelta(seconds=10),
            )
        )
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_WITNESS_OR_BINDING_INVALID",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

        forged = self._runtime_config()
        forged_witness = dict(forged["witness_term"])
        forged_proof = dict(forged_witness["proof"])
        signature = forged_proof["signature"]
        forged_proof["signature"] = (
            ("A" if signature[0] != "A" else "B") + signature[1:]
        )
        forged_witness["proof"] = forged_proof
        forged["witness_term"] = forged_witness
        self._re_pin_config(forged)
        self._write_config(forged)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_WITNESS_OR_BINDING_INVALID",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

    def test_wrong_or_unsafe_wal_path_never_constructs_the_uploader(self) -> None:
        outside = self.root / SEGMENT_NAME
        outside.write_bytes(b"W" * SEGMENT_SIZE)
        os.chmod(outside, 0o600)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_WAL_PATH_INVALID",
        ):
            self._execute(self._arguments(wal_path=outside))
        self._assert_nothing_external_constructed()

        self.wal_path.unlink()
        os.symlink(outside, self.wal_path)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_WAL_PATH_INVALID",
        ):
            self._execute()
        self._assert_nothing_external_constructed()

    def test_cli_cannot_select_an_alternate_config_location_and_error_is_redacted(self) -> None:
        alternate = self.config_dir / "alternate.json"
        alternate.write_bytes(self.config_path.read_bytes())
        os.chmod(alternate, 0o600)
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_CLI_SHAPE_INVALID",
        ) as caught:
            self._execute(self._arguments(config_path=alternate))
        self._assert_nothing_external_constructed()
        output = adapter.render_wa_fi_postgres_archive_command_error(caught.exception)
        self.assertNotIn(str(alternate).encode(), output)
        self.assertNotIn(str(self.config_path).encode(), output)
        self.assertNotIn(b"secret", output.lower())
        self.assertEqual(
            {
                "error": "ARCHIVE_CLI_SHAPE_INVALID",
                "schema": adapter.PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_REPORT_SCHEMA,
                "status": "blocked",
            },
            json.loads(output.decode("ascii")),
        )

    def test_live_term_is_rechecked_after_uploader_before_completed_manifest(self) -> None:
        timestamps = iter((NOW, NOW + timedelta(seconds=47)))
        with self.assertRaisesRegex(
            adapter.PhysicalWaFiPostgresArchiveCommandError,
            "ARCHIVE_HANDOFF_FAILED",
        ):
            self._execute(clock=lambda: next(timestamps))
        self.assertEqual(1, len(_RecordingUploader.constructed))
        self.assertEqual(1, len(_RecordingUploader.constructed[0].uploads))
        self.assertEqual(0, self.age_factory.calls)
        self.assertEqual(0, self.s3_factory.calls)
        manifests = self.spool_root / "manifests"
        self.assertTrue(manifests.is_dir())
        self.assertEqual([], list(manifests.iterdir()))


if __name__ == "__main__":
    unittest.main()
