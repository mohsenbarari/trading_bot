from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_wal_object_manifest import build_physical_wal_segment_manifest
from core.physical_wal_archive_spool import (
    PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA,
    PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA,
    PhysicalWalArchiveManifestBinding,
    PhysicalWalArchiveSpoolConfig,
    PhysicalWalArchiveSpoolError,
    PhysicalWalArchiveUploadReceipt,
    archive_physical_wal_segment,
    authorize_physical_wal_archive_binding,
    parse_postgresql_wal_segment_name,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
SEGMENT_SIZE = 16 * 1024 * 1024
SEGMENT_NAME = "000000010000000000000001"


def sha(character: str) -> str:
    return character * 64


def public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def witnessed_term(*, holder_site: str = "webapp_fi", now: datetime = NOW):
    signer = Ed25519PrivateKey.generate()
    proof = build_object_delta_role_matrix_witnessed_term_proof(
        holder_site=holder_site,
        writer_epoch=41,
        writer_lease_id="writer-lease-41",
        witness_transition_id="witness-transition-41",
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


def manifest_binding() -> PhysicalWalArchiveManifestBinding:
    return PhysicalWalArchiveManifestBinding(
        source_site="webapp_fi",
        destination_site="webapp_ir",
        campaign_id="wal-spool-20260731",
        release_sha="3138d0c2a8d20a84042c3a438fbc88db7a4db498",
        stream_generation_id="fi-ir-wal-stream-20260731",
        baseline_generation_id="fi-ir-baseline-20260731",
        baseline_manifest_sha256=sha("a"),
        baseline_wal_lsn="0/1800000",
        wal_chain_start_lsn="0/1000000",
        archive_manifest_sha256=sha("b"),
        database_system_identifier="7392847193847192834",
        timeline_id=1,
        destination_age_recipient="age1" + "a" * 30,
    )


class RecordingUploader:
    def __init__(self, *, fail: bool = False, bad_receipt: bool = False) -> None:
        self.fail = fail
        self.bad_receipt = bad_receipt
        self.calls: list[tuple[Path, bytes, str]] = []

    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        self.calls.append((snapshot_path, descriptor_bytes, descriptor_sha256))
        if self.fail:
            raise RuntimeError("synthetic crash after an external side effect")
        descriptor = json.loads(descriptor_bytes)
        return PhysicalWalArchiveUploadReceipt(
            descriptor_sha256=sha("f") if self.bad_receipt else descriptor_sha256,
            object_key=descriptor["object_key"],
            version_id="version-20260731-01",
            ciphertext_sha256=sha("c"),
            ciphertext_bytes=snapshot_path.stat().st_size + 128,
            encryption="age-v1",
            age_recipient=descriptor["destination_age_recipient"],
            immutability="versioned_create_only_readback_v1",
        )


class MutatingUploader(RecordingUploader):
    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        receipt = super().upload(
            snapshot_path=snapshot_path,
            descriptor_bytes=descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
        )
        snapshot_path.write_bytes(b"M" * snapshot_path.stat().st_size)
        return receipt


class WrongRecipientUploader(RecordingUploader):
    def upload(
        self,
        *,
        snapshot_path: Path,
        descriptor_bytes: bytes,
        descriptor_sha256: str,
    ) -> PhysicalWalArchiveUploadReceipt:
        receipt = super().upload(
            snapshot_path=snapshot_path,
            descriptor_bytes=descriptor_bytes,
            descriptor_sha256=descriptor_sha256,
        )
        return PhysicalWalArchiveUploadReceipt(
            descriptor_sha256=receipt.descriptor_sha256,
            object_key=receipt.object_key,
            version_id=receipt.version_id,
            ciphertext_sha256=receipt.ciphertext_sha256,
            ciphertext_bytes=receipt.ciphertext_bytes,
            encryption=receipt.encryption,
            age_recipient="age1" + "c" * 30,
            immutability=receipt.immutability,
        )


class PhysicalWalArchiveSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-wal-archive-spool-")
        self.root = Path(self.temporary.name).resolve()
        self.source_root = self.root / "pg_wal"
        self.spool_root = self.root / "spool"
        self.source_root.mkdir()
        self.spool_root.mkdir()
        self.source_root.chmod(0o700)
        self.spool_root.chmod(0o700)
        self.source_path = self.source_root / SEGMENT_NAME
        self.source_path.write_bytes(b"W" * SEGMENT_SIZE)
        self.config = PhysicalWalArchiveSpoolConfig(
            wal_source_root=self.source_root,
            spool_root=self.spool_root,
            wal_segment_size_bytes=SEGMENT_SIZE,
        )
        self.term = witnessed_term()
        self.binding = authorize_physical_wal_archive_binding(
            manifest_binding=manifest_binding(),
            witnessed_term=self.term,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def archive(self, uploader: RecordingUploader | None, *, clock=None):
        return archive_physical_wal_segment(
            segment_name=SEGMENT_NAME,
            config=self.config,
            verified_binding=self.binding,
            uploader=uploader,
            now=NOW,
            term_recheck_clock=clock or (lambda: NOW),
        )

    def test_captures_immutable_snapshot_and_returns_canonical_bound_artifacts(self) -> None:
        uploader = RecordingUploader()

        result = self.archive(uploader)

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual(SEGMENT_SIZE, result.snapshot_bytes)
        self.assertEqual(hashlib.sha256(b"W" * SEGMENT_SIZE).hexdigest(), result.snapshot_sha256)
        self.assertEqual(b"W" * SEGMENT_SIZE, result.snapshot_path.read_bytes())
        descriptor_bytes = result.handoff_descriptor_path.read_bytes()
        descriptor = json.loads(descriptor_bytes)
        self.assertEqual(PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA, descriptor["schema"])
        self.assertEqual(SEGMENT_NAME, descriptor["wal_segment_name"])
        self.assertEqual("0/1000000", descriptor["start_lsn"])
        self.assertEqual("0/2000000", descriptor["end_lsn"])
        self.assertEqual("webapp_fi", descriptor["writer_term"]["holder_site"])
        self.assertEqual(
            json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
            descriptor_bytes,
        )
        manifest_bytes = result.upload_manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        self.assertEqual(PHYSICAL_WAL_ARCHIVE_SPOOL_MANIFEST_SCHEMA, manifest["schema"])
        self.assertEqual(result.handoff_descriptor_sha256, manifest["handoff_descriptor_sha256"])
        self.assertEqual(result.object_key, manifest["object"]["object_key"])
        self.assertEqual("age-v1", manifest["object"]["encryption"])
        self.assertNotIn("https://", manifest_bytes.decode("ascii"))

    def test_missing_uploader_fails_before_spool_mutation(self) -> None:
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "uploader is required"):
            self.archive(None)

        self.assertEqual([], list(self.spool_root.iterdir()))

    def test_expired_or_wrong_holder_term_fails_before_source_copy(self) -> None:
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "not live"):
            archive_physical_wal_segment(
                segment_name=SEGMENT_NAME,
                config=self.config,
                verified_binding=self.binding,
                uploader=RecordingUploader(),
                now=NOW + timedelta(seconds=47),
                term_recheck_clock=lambda: NOW + timedelta(seconds=47),
            )
        self.assertEqual([], list(self.spool_root.iterdir()))

        wrong_holder = witnessed_term(holder_site="webapp_ir")
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "does not hold"):
            authorize_physical_wal_archive_binding(
                manifest_binding=manifest_binding(),
                witnessed_term=wrong_holder,
                now=NOW,
            )

    def test_reverse_ir_to_fi_route_is_bound_to_the_new_ir_writer_term(self) -> None:
        reverse_manifest = replace(
            manifest_binding(),
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-wal-stream-20260731",
            baseline_generation_id="ir-fi-baseline-20260731",
            destination_age_recipient="age1" + "c" * 30,
            object_storage_namespace="physical-failback",
        )

        verified = authorize_physical_wal_archive_binding(
            manifest_binding=reverse_manifest,
            witnessed_term=witnessed_term(holder_site="webapp_ir"),
            now=NOW,
        )

        self.assertEqual("webapp_ir", verified.manifest_binding.source_site)
        self.assertEqual("webapp_fi", verified.manifest_binding.destination_site)
        self.assertEqual("physical-failback", verified.manifest_binding.object_storage_namespace)
        self.assertEqual("webapp_ir", verified.witnessed_term.holder_site)

        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "distinct WA"):
            authorize_physical_wal_archive_binding(
                manifest_binding=replace(reverse_manifest, destination_site="webapp_ir"),
                witnessed_term=witnessed_term(holder_site="webapp_ir"),
                now=NOW,
            )

    def test_reverse_ir_to_fi_handoff_preserves_the_pinned_destination_recipient(self) -> None:
        reverse_manifest = replace(
            manifest_binding(),
            source_site="webapp_ir",
            destination_site="webapp_fi",
            stream_generation_id="ir-fi-wal-stream-20260731",
            baseline_generation_id="ir-fi-baseline-20260731",
            destination_age_recipient="age1" + "c" * 30,
            object_storage_namespace="physical-failback",
        )
        reverse_binding = authorize_physical_wal_archive_binding(
            manifest_binding=reverse_manifest,
            witnessed_term=witnessed_term(holder_site="webapp_ir"),
            now=NOW,
        )

        result = archive_physical_wal_segment(
            segment_name=SEGMENT_NAME,
            config=self.config,
            verified_binding=reverse_binding,
            uploader=RecordingUploader(),
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        descriptor = json.loads(result.handoff_descriptor_path.read_bytes())

        self.assertEqual("webapp_ir", descriptor["source_site"])
        self.assertEqual("webapp_fi", descriptor["destination_site"])
        self.assertEqual("age1" + "c" * 30, descriptor["destination_age_recipient"])
        self.assertEqual("physical-failback", descriptor["object_storage_namespace"])
        self.assertTrue(result.object_key.startswith("physical-failback/"))

    def test_reverse_route_rejects_the_normal_object_storage_namespace(self) -> None:
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "namespace.*pinned route"):
            authorize_physical_wal_archive_binding(
                manifest_binding=replace(
                    manifest_binding(),
                    source_site="webapp_ir",
                    destination_site="webapp_fi",
                ),
                witnessed_term=witnessed_term(holder_site="webapp_ir"),
                now=NOW,
            )

    def test_rejects_noncanonical_path_symlink_and_wrong_size_source(self) -> None:
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "segment name"):
            archive_physical_wal_segment(
                segment_name="../" + SEGMENT_NAME,
                config=self.config,
                verified_binding=self.binding,
                uploader=RecordingUploader(),
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )

        self.source_path.unlink()
        target = self.root / "outside-wal"
        target.write_bytes(b"W" * SEGMENT_SIZE)
        os.symlink(target, self.source_path)
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "cannot be opened safely"):
            self.archive(RecordingUploader())

        self.source_path.unlink()
        self.source_path.write_bytes(b"W" * (SEGMENT_SIZE - 1))
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "bounded"):
            self.archive(RecordingUploader())

    def test_failed_handoff_is_not_success_and_retry_reuses_same_snapshot_and_descriptor(self) -> None:
        failing = RecordingUploader(fail=True)
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "uploader failed"):
            self.archive(failing)
        self.assertEqual(1, len(failing.calls))
        first_snapshot, first_descriptor, first_hash = failing.calls[0]
        self.assertTrue(first_snapshot.is_file())
        descriptor_path = self.spool_root / "descriptors" / f"{first_hash}.json"
        self.assertEqual(first_descriptor, descriptor_path.read_bytes())
        self.assertEqual([], list((self.spool_root / "manifests").iterdir()))

        succeeding = RecordingUploader()
        result = self.archive(succeeding)
        self.assertEqual(first_snapshot, result.snapshot_path)
        self.assertEqual(first_hash, result.handoff_descriptor_sha256)
        self.assertEqual(first_descriptor, succeeding.calls[0][1])
        self.assertTrue(result.upload_manifest_path.is_file())

    def test_tampered_retained_snapshot_blocks_retry_without_uploading(self) -> None:
        failing = RecordingUploader(fail=True)
        with self.assertRaises(PhysicalWalArchiveSpoolError):
            self.archive(failing)
        snapshot_path = failing.calls[0][0]
        snapshot_path.write_bytes(b"T" * SEGMENT_SIZE)

        succeeding = RecordingUploader()
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "snapshot was tampered"):
            self.archive(succeeding)
        self.assertEqual([], succeeding.calls)

    def test_uploader_cannot_substitute_a_different_destination_recipient(self) -> None:
        uploader = WrongRecipientUploader()

        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "destination age recipient"):
            self.archive(uploader)

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual([], list((self.spool_root / "manifests").iterdir()))

    def test_mutating_uploader_cannot_obtain_success_after_snapshot_changes(self) -> None:
        uploader = MutatingUploader()

        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "snapshot changed during uploader"):
            self.archive(uploader)

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual([], list((self.spool_root / "manifests").iterdir()))

    def test_term_is_revalidated_after_upload_before_completed_manifest(self) -> None:
        timestamps = iter((NOW, NOW + timedelta(seconds=47)))
        uploader = RecordingUploader()

        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "not live"):
            self.archive(uploader, clock=lambda: next(timestamps))

        self.assertEqual(1, len(uploader.calls))
        self.assertEqual([], list((self.spool_root / "manifests").iterdir()))

    def test_completed_descriptor_is_idempotent_without_a_second_uploader_call(self) -> None:
        first_uploader = RecordingUploader()
        first = self.archive(first_uploader)
        second_uploader = RecordingUploader()

        second = self.archive(second_uploader)

        self.assertEqual(1, len(first_uploader.calls))
        self.assertEqual([], second_uploader.calls)
        self.assertEqual(first.handoff_descriptor_sha256, second.handoff_descriptor_sha256)
        self.assertEqual(first.upload_manifest_path, second.upload_manifest_path)
        self.assertEqual(first.object_version_id, second.object_version_id)
        self.assertEqual([first.upload_manifest_path], list((self.spool_root / "manifests").iterdir()))

    def test_completed_object_descriptor_feeds_the_independent_wal_chain_manifest(self) -> None:
        result = self.archive(RecordingUploader())
        local_manifest = json.loads(result.upload_manifest_path.read_bytes())
        signer = Ed25519PrivateKey.generate()

        chain_manifest = build_physical_wal_segment_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id="wal-spool-20260731",
            release_sha="3138d0c2a8d20a84042c3a438fbc88db7a4db498",
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witnessed_term_proof_sha256=self.term.proof_sha256,
            baseline_generation_id="fi-ir-baseline-20260731",
            baseline_manifest_sha256=sha("a"),
            database_system_identifier="7392847193847192834",
            timeline_id=1,
            wal_segment_size_bytes=SEGMENT_SIZE,
            previous_manifest_sha256="0" * 64,
            previous_end_lsn="0/1000000",
            previous_segment_ordinal=0,
            segments=[
                {
                    "ordinal": local_manifest["segment_ordinal"],
                    "wal_segment_name": local_manifest["wal_segment_name"],
                    "timeline_id": local_manifest["timeline_id"],
                    "start_lsn": local_manifest["start_lsn"],
                    "end_lsn": local_manifest["end_lsn"],
                    "object": local_manifest["object"],
                }
            ],
            source_signer=signer,
        )

        self.assertEqual("postgresql_wal_segment_chain", chain_manifest["kind"])
        self.assertEqual(local_manifest["object"], chain_manifest["segments"][0]["object"])

    def test_bad_upload_receipt_never_creates_completed_manifest(self) -> None:
        bad = RecordingUploader(bad_receipt=True)
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "different descriptor"):
            self.archive(bad)

        self.assertTrue((self.spool_root / "descriptors").is_dir())
        self.assertEqual([], list((self.spool_root / "manifests").iterdir()))

    def test_only_signed_manifest_geometry_is_accepted(self) -> None:
        unsupported_config = PhysicalWalArchiveSpoolConfig(
            wal_source_root=self.source_root,
            spool_root=self.spool_root,
            wal_segment_size_bytes=1024 * 1024,
        )
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "segment size"):
            archive_physical_wal_segment(
                segment_name=SEGMENT_NAME,
                config=unsupported_config,
                verified_binding=self.binding,
                uploader=RecordingUploader(),
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )

    def test_spool_root_must_be_private_and_source_root_cannot_be_group_writable(self) -> None:
        self.spool_root.chmod(0o755)
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "mode 0700"):
            self.archive(RecordingUploader())
        self.spool_root.chmod(0o700)
        self.source_root.chmod(0o720)
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "group/world writable"):
            self.archive(RecordingUploader())

    def test_wal_name_parser_derives_canonical_timeline_ordinal_and_lsn(self) -> None:
        timeline, ordinal, start, end, size = parse_postgresql_wal_segment_name(
            SEGMENT_NAME, wal_segment_size_bytes=SEGMENT_SIZE
        )
        self.assertEqual((1, 1, "0/1000000", "0/2000000", SEGMENT_SIZE), (timeline, ordinal, start, end, size))
        with self.assertRaisesRegex(PhysicalWalArchiveSpoolError, "segment name"):
            parse_postgresql_wal_segment_name("00000001.history", wal_segment_size_bytes=SEGMENT_SIZE)


if __name__ == "__main__":
    unittest.main()
