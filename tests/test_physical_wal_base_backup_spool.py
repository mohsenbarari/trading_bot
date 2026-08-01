from __future__ import annotations

from dataclasses import replace
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
from core.physical_wal_base_backup_spool import (
    PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA,
    PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
    PhysicalWalBaseBackupCompletedArtifact,
    PhysicalWalBaseBackupManifestBinding,
    PhysicalWalBaseBackupSpoolConfig,
    PhysicalWalBaseBackupSpoolError,
    PhysicalWalBaseBackupUploadReceipt,
    authorize_physical_wal_base_backup_binding,
    capture_physical_wal_base_backup,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RECIPIENT = "age1" + "a" * 30
OTHER_RECIPIENT = "age1" + "d" * 30
FI_RECIPIENT = "age1" + "c" * 30
ARTIFACT_NAME = "physical-base-backup-0001.tar"
ARTIFACT_BYTES = b"B" * (256 * 1024)
WAL_SEGMENT_SIZE = 16 * 1024 * 1024


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def witnessed_term(*, holder_site: str = "webapp_fi", now: datetime = NOW):
    signer = Ed25519PrivateKey.generate()
    proof = build_object_delta_role_matrix_witnessed_term_proof(
        holder_site=holder_site,
        writer_epoch=73,
        writer_lease_id="writer-lease-73",
        witness_transition_id="witness-transition-73",
        issued_at=now - timedelta(seconds=10),
        expires_at=now + timedelta(seconds=50),
        witness_signer=signer,
    )
    return verify_object_delta_role_matrix_witnessed_term(
        proof,
        witness_public_key=public_key(signer),
        maximum_lease_duration_seconds=90,
        safety_margin_seconds=5,
        now=now,
    )


def manifest_binding(
    *,
    source_site: str = "webapp_fi",
    destination_site: str = "webapp_ir",
    destination_age_recipient: str = RECIPIENT,
    object_storage_namespace: str = "physical-wal",
) -> PhysicalWalBaseBackupManifestBinding:
    return PhysicalWalBaseBackupManifestBinding(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id="physical-base-20260731",
        release_sha="3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        baseline_generation_id="physical-base-generation-20260731",
        database_system_identifier="7392847193847192834",
        timeline_id=1,
        wal_segment_size_bytes=WAL_SEGMENT_SIZE,
        baseline_wal_lsn="0/1800000",
        wal_chain_start_lsn="0/1000000",
        base_backup_end_lsn="0/2800000",
        destination_age_recipient=destination_age_recipient,
        object_storage_namespace=object_storage_namespace,
    )


def completed_artifact() -> PhysicalWalBaseBackupCompletedArtifact:
    return PhysicalWalBaseBackupCompletedArtifact(
        artifact_name=ARTIFACT_NAME,
        plaintext_sha256=sha(ARTIFACT_BYTES),
        plaintext_bytes=len(ARTIFACT_BYTES),
        completion_attestation_sha256=sha("trusted-completion-record"),
    )


class RecordingUploader:
    def __init__(
        self,
        *,
        fail: bool = False,
        wrong_recipient: bool = False,
        mutate_snapshot: bool = False,
        recipient: str = RECIPIENT,
    ) -> None:
        self.fail = fail
        self.wrong_recipient = wrong_recipient
        self.mutate_snapshot = mutate_snapshot
        self.recipient = recipient
        self.calls: list[tuple[Path, bytes, str]] = []

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalBaseBackupUploadReceipt:
        self.calls.append((snapshot_path, descriptor_bytes, descriptor_sha256))
        if self.mutate_snapshot:
            snapshot_path.write_bytes(b"M" * snapshot_path.stat().st_size)
        if self.fail:
            raise RuntimeError("synthetic crash after an external side effect")
        descriptor = json.loads(descriptor_bytes)
        return PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=descriptor_sha256,
            object_key=descriptor["object_key"],
            version_id="base-version-20260731-0001",
            ciphertext_sha256=sha("ciphertext"),
            ciphertext_bytes=snapshot_path.stat().st_size + 128,
            encryption="age-v1",
            age_recipient=OTHER_RECIPIENT if self.wrong_recipient else self.recipient,
            immutability="versioned_create_only_readback_v1",
        )


class PhysicalWalBaseBackupSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="physical-wal-base-backup-spool-")
        self.root = Path(self.temporary.name).resolve()
        self.source_root = self.root / "completed-source"
        self.spool_root = self.root / "private-spool"
        self.source_root.mkdir(mode=0o700)
        self.spool_root.mkdir(mode=0o700)
        os.chmod(self.source_root, 0o700)
        os.chmod(self.spool_root, 0o700)
        self.source_path = self.source_root / ARTIFACT_NAME
        self.source_path.write_bytes(ARTIFACT_BYTES)
        os.chmod(self.source_path, 0o600)
        self.config = PhysicalWalBaseBackupSpoolConfig(
            source_root=self.source_root,
            spool_root=self.spool_root,
            maximum_base_backup_bytes=len(ARTIFACT_BYTES),
            spool_reserve_bytes=4096,
        )
        self.term = witnessed_term()
        self.binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=manifest_binding(),
            completed_artifact=completed_artifact(),
            witnessed_term=self.term,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def capture(
        self,
        uploader: RecordingUploader | None,
        *,
        clock=lambda: NOW,
        config: PhysicalWalBaseBackupSpoolConfig | None = None,
        binding=None,
    ):
        return capture_physical_wal_base_backup(
            config=self.config if config is None else config,
            verified_binding=self.binding if binding is None else binding,
            uploader=uploader,
            now=NOW,
            term_recheck_clock=clock,
        )

    def test_captures_completed_artifact_and_emits_manifest_aligned_record(self) -> None:
        uploader = RecordingUploader()

        result = self.capture(uploader)

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual(ARTIFACT_BYTES, result.snapshot_path.read_bytes())
        self.assertEqual(sha(ARTIFACT_BYTES), result.snapshot_sha256)
        descriptor = json.loads(result.handoff_descriptor_path.read_bytes())
        self.assertEqual(PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA, descriptor["schema"])
        self.assertEqual("physical_postgresql_base_backup_handoff", descriptor["kind"])
        self.assertEqual("webapp_fi", descriptor["source_site"])
        self.assertEqual("webapp_ir", descriptor["destination_site"])
        self.assertEqual("physical-base-generation-20260731", descriptor["baseline_generation_id"])
        self.assertEqual(WAL_SEGMENT_SIZE, descriptor["wal_segment_size_bytes"])
        self.assertEqual("0/1000000", descriptor["wal_chain_start_lsn"])
        self.assertEqual("0/2800000", descriptor["base_backup_end_lsn"])
        self.assertEqual(RECIPIENT, descriptor["destination_age_recipient"])
        self.assertTrue(descriptor["not_a_remote_apply_proof"])
        self.assertTrue(descriptor["not_a_strict_acknowledgement_proof"])
        completed = json.loads(result.completed_record_path.read_bytes())
        self.assertEqual(PHYSICAL_WAL_BASE_BACKUP_SPOOL_COMPLETED_SCHEMA, completed["schema"])
        self.assertTrue(completed["not_a_remote_apply_proof"])
        self.assertEqual("physical_postgresql_base_backup", completed["object"]["object_kind"])
        self.assertEqual(RECIPIENT, completed["object"]["age_recipient"])

    def test_rejects_unsafe_or_symlink_source_before_upload(self) -> None:
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "artifact name"):
            authorize_physical_wal_base_backup_binding(
                manifest_binding=manifest_binding(),
                completed_artifact=PhysicalWalBaseBackupCompletedArtifact(
                    artifact_name="../not-a-backup",
                    plaintext_sha256=sha(ARTIFACT_BYTES),
                    plaintext_bytes=len(ARTIFACT_BYTES),
                    completion_attestation_sha256=sha("trusted-completion-record"),
                ),
                witnessed_term=self.term,
                now=NOW,
            )
        symlinked_source_root = self.root / "symlinked-completed-source"
        symlinked_source_root.symlink_to(self.source_root, target_is_directory=True)
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "source root"):
            self.capture(
                RecordingUploader(),
                config=replace(self.config, source_root=symlinked_source_root),
            )
        self.source_path.unlink()
        outside = self.root / "outside-base-backup"
        outside.write_bytes(ARTIFACT_BYTES)
        os.chmod(outside, 0o600)
        self.source_path.symlink_to(outside)
        uploader = RecordingUploader()
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "cannot be opened safely"):
            self.capture(uploader)
        self.assertEqual([], uploader.calls)

    def test_accepts_ir_to_fi_only_when_ir_holds_the_live_term(self) -> None:
        ir_term = witnessed_term(holder_site="webapp_ir")
        ir_to_fi_binding = authorize_physical_wal_base_backup_binding(
            manifest_binding=manifest_binding(
                source_site="webapp_ir",
                destination_site="webapp_fi",
                destination_age_recipient=FI_RECIPIENT,
                object_storage_namespace="physical-failback",
            ),
            completed_artifact=completed_artifact(),
            witnessed_term=ir_term,
            now=NOW,
        )

        result = self.capture(
            RecordingUploader(recipient=FI_RECIPIENT),
            binding=ir_to_fi_binding,
        )

        descriptor = json.loads(result.handoff_descriptor_path.read_bytes())
        self.assertEqual("webapp_ir", descriptor["source_site"])
        self.assertEqual("webapp_fi", descriptor["destination_site"])
        self.assertEqual("webapp_ir", descriptor["writer_term"]["holder_site"])
        self.assertEqual(FI_RECIPIENT, descriptor["destination_age_recipient"])
        self.assertIn("webapp_ir-to-webapp_fi", result.object_key)
        self.assertEqual("physical-failback", descriptor["object_storage_namespace"])
        self.assertTrue(result.object_key.startswith("physical-failback/"))

    def test_reverse_route_rejects_the_normal_object_storage_namespace(self) -> None:
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "namespace.*pinned route"):
            authorize_physical_wal_base_backup_binding(
                manifest_binding=manifest_binding(
                    source_site="webapp_ir",
                    destination_site="webapp_fi",
                    destination_age_recipient=FI_RECIPIENT,
                ),
                completed_artifact=completed_artifact(),
                witnessed_term=witnessed_term(holder_site="webapp_ir"),
                now=NOW,
            )

    def test_rejects_ir_to_fi_when_fi_holds_the_live_term(self) -> None:
        with self.assertRaisesRegex(
            PhysicalWalBaseBackupSpoolError,
            "source does not hold the live Witness term",
        ):
            authorize_physical_wal_base_backup_binding(
                manifest_binding=manifest_binding(
                    source_site="webapp_ir",
                    destination_site="webapp_fi",
                    destination_age_recipient=FI_RECIPIENT,
                    object_storage_namespace="physical-failback",
                ),
                completed_artifact=completed_artifact(),
                witnessed_term=self.term,
                now=NOW,
            )

    def test_rejects_same_site_and_foreign_base_backup_routes(self) -> None:
        for source_site, destination_site in (
            ("webapp_fi", "webapp_fi"),
            ("webapp_fi", "webapp_de"),
            ("webapp_de", "webapp_fi"),
        ):
            with self.subTest(source_site=source_site, destination_site=destination_site):
                with self.assertRaisesRegex(
                    PhysicalWalBaseBackupSpoolError,
                    "ordered distinct WebApp route",
                ):
                    authorize_physical_wal_base_backup_binding(
                        manifest_binding=manifest_binding(
                            source_site=source_site,
                            destination_site=destination_site,
                        ),
                        completed_artifact=completed_artifact(),
                        witnessed_term=self.term,
                        now=NOW,
                    )

    def test_public_or_wrong_owner_source_and_public_spool_roots_are_rejected(self) -> None:
        os.chmod(self.spool_root, 0o755)
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "spool root"):
            self.capture(RecordingUploader())
        os.chmod(self.spool_root, 0o700)
        os.chmod(self.source_root, 0o755)
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "source root"):
            self.capture(RecordingUploader())
        os.chmod(self.source_root, 0o700)

        original_lstat = os.lstat

        def foreign_source_owner(path: os.PathLike[str] | str):
            metadata = original_lstat(path)
            if Path(path) == self.source_root:
                return SimpleNamespace(st_mode=metadata.st_mode, st_uid=65534)
            return metadata

        with patch("core.physical_wal_base_backup_spool.os.lstat", side_effect=foreign_source_owner):
            with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "source root"):
                self.capture(RecordingUploader())

    def test_wrong_wa_ir_recipient_never_creates_completed_record(self) -> None:
        uploader = RecordingUploader(wrong_recipient=True)

        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "different destination age recipient"):
            self.capture(uploader)

        self.assertEqual(1, len(uploader.calls))
        completed = self.spool_root / "completed"
        self.assertTrue(completed.is_dir())
        self.assertEqual([], list(completed.iterdir()))

    def test_term_expiry_during_upload_blocks_completion(self) -> None:
        uploader = RecordingUploader()

        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "not live"):
            self.capture(uploader, clock=lambda: NOW + timedelta(seconds=47))

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual([], list((self.spool_root / "completed").iterdir()))

    def test_snapshot_mutation_during_uploader_blocks_completion(self) -> None:
        uploader = RecordingUploader(mutate_snapshot=True)

        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "snapshot after uploader handoff was tampered"):
            self.capture(uploader)

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual([], list((self.spool_root / "completed").iterdir()))

    def test_crash_retry_and_successful_retry_are_idempotent(self) -> None:
        failing = RecordingUploader(fail=True)
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "uploader failed"):
            self.capture(failing)
        self.assertEqual(1, len(failing.calls))
        first_snapshot, first_descriptor, first_descriptor_sha = failing.calls[0]
        self.assertTrue(first_snapshot.is_file())
        self.assertEqual(
            first_descriptor,
            (self.spool_root / "descriptors" / f"{first_descriptor_sha}.json").read_bytes(),
        )

        successful = RecordingUploader()
        first_result = self.capture(successful)
        self.assertEqual(1, len(successful.calls))
        self.assertEqual(first_snapshot, first_result.snapshot_path)
        self.assertEqual(first_descriptor_sha, first_result.handoff_descriptor_sha256)

        self.source_path.unlink()
        must_not_upload = RecordingUploader(fail=True)
        retry_result = self.capture(must_not_upload)
        self.assertEqual([], must_not_upload.calls)
        self.assertEqual(first_result, retry_result)

    def test_completed_record_type_tampering_and_insufficient_capacity_fail_closed(self) -> None:
        result = self.capture(RecordingUploader())
        completed = json.loads(result.completed_record_path.read_bytes())
        completed["object"]["object_kind"] = "postgresql_wal_segment"
        result.completed_record_path.write_bytes(
            json.dumps(completed, sort_keys=True, separators=(",", ":")).encode("ascii")
        )
        with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "object type"):
            self.capture(RecordingUploader())

        with TemporaryDirectory(prefix="physical-wal-base-capacity-") as temporary_directory:
            root = Path(temporary_directory).resolve()
            source = root / "source"
            spool = root / "spool"
            source.mkdir(mode=0o700)
            spool.mkdir(mode=0o700)
            os.chmod(source, 0o700)
            os.chmod(spool, 0o700)
            (source / ARTIFACT_NAME).write_bytes(ARTIFACT_BYTES)
            os.chmod(source / ARTIFACT_NAME, 0o600)
            constrained = PhysicalWalBaseBackupSpoolConfig(
                source_root=source,
                spool_root=spool,
                maximum_base_backup_bytes=len(ARTIFACT_BYTES),
                spool_reserve_bytes=4096,
            )
            with patch(
                "core.physical_wal_base_backup_spool.os.statvfs",
                return_value=SimpleNamespace(f_bavail=1, f_frsize=1),
            ):
                with self.assertRaisesRegex(PhysicalWalBaseBackupSpoolError, "lacks required free capacity"):
                    capture_physical_wal_base_backup(
                        config=constrained,
                        verified_binding=self.binding,
                        uploader=RecordingUploader(),
                        now=NOW,
                        term_recheck_clock=lambda: NOW,
                    )


if __name__ == "__main__":
    unittest.main()
