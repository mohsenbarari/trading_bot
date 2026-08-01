from __future__ import annotations

import ast
from dataclasses import replace
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

import core.physical_blob_artifact_spool as blob_spool
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_blob_artifact_spool import (
    PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA,
    PHYSICAL_BLOB_ARTIFACT_SPOOL_DEFAULT_ENABLED,
    PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA,
    PhysicalBlobArtifactManifestBinding,
    PhysicalBlobArtifactSpoolConfig,
    PhysicalBlobArtifactSpoolError,
    PhysicalBlobFrozenDescriptor,
    authorize_physical_blob_artifact_binding,
    spool_finalized_physical_blob_artifacts,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE_SHA = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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


@unittest.skipUnless(os.geteuid() == 0, "blob spool contract explicitly requires root")
class PhysicalBlobArtifactSpoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-blob-artifact-spool-")
        self.root = Path(self.temporary.name).resolve()
        self.uploads_root = self.root / "protected-uploads"
        self.spool_root = self.root / "private-blob-spool"
        self.uploads_root.mkdir(mode=0o700)
        self.spool_root.mkdir(mode=0o700)
        self.records_directory = self.uploads_root / "records"
        self.records_directory.mkdir(mode=0o700)
        for path in (self.uploads_root, self.spool_root, self.records_directory):
            os.chmod(path, 0o700)
        self.uploads_root_identity_sha256 = blob_spool.derive_physical_blob_uploads_root_identity(
            uploads_root=self.uploads_root
        )
        self.first_content = b"finalized-upload-one" * 128
        self.second_content = b"finalized-upload-two" * 64
        self.first_path = self.write_source("records/blob-one.bin", self.first_content)
        self.second_path = self.write_source("records/blob-two.bin", self.second_content)
        self.config = PhysicalBlobArtifactSpoolConfig(
            uploads_root=self.uploads_root,
            spool_root=self.spool_root,
            enabled=True,
            maximum_blob_bytes=1024 * 1024,
        )
        self.term = witnessed_term()
        self.manifest = self.manifest_binding()
        self.binding = authorize_physical_blob_artifact_binding(
            manifest_binding=self.manifest,
            witnessed_term=self.term,
            now=NOW,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_source(self, relative: str, content: bytes) -> Path:
        path = self.uploads_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        for parent in (path.parent,):
            os.chmod(parent, 0o700)
        path.write_bytes(content)
        os.chmod(path, 0o600)
        return path

    def manifest_binding(
        self,
        *,
        source: str = "webapp_fi",
        destination: str = "webapp_ir",
        recipient: str = IR_RECIPIENT,
        baseline_lsn: str = "0/1800000",
    ) -> PhysicalBlobArtifactManifestBinding:
        return PhysicalBlobArtifactManifestBinding(
            source_site=source,
            destination_site=destination,
            campaign_id="physical-blob-20260731",
            release_sha=RELEASE_SHA,
            baseline_generation_id="physical-blob-baseline-20260731",
            baseline_manifest_sha256="a" * 64,
            baseline_wal_lsn=baseline_lsn,
            destination_age_recipient=recipient,
        )

    def descriptor(
        self,
        *,
        record_id: str = "blob-record-0001",
        relative_path: str = "records/blob-one.bin",
        content: bytes | None = None,
        source: str = "webapp_fi",
        destination: str = "webapp_ir",
        recipient: str = IR_RECIPIENT,
        baseline_lsn: str = "0/1800000",
    ) -> PhysicalBlobFrozenDescriptor:
        content = self.first_content if content is None else content
        return PhysicalBlobFrozenDescriptor(
            source_site=source,
            destination_site=destination,
            campaign_id="physical-blob-20260731",
            release_sha=RELEASE_SHA,
            baseline_generation_id="physical-blob-baseline-20260731",
            baseline_manifest_sha256="a" * 64,
            baseline_wal_lsn=baseline_lsn,
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witnessed_term_proof_sha256=self.term.proof_sha256,
            destination_age_recipient=recipient,
            source_record_id=record_id,
            source_relative_path=relative_path,
            uploads_root_identity_sha256=self.uploads_root_identity_sha256,
            declared_content_sha256=digest(content),
            declared_content_bytes=len(content),
        )

    def spool(
        self,
        descriptors: list[PhysicalBlobFrozenDescriptor],
        *,
        binding=None,
        config=None,
        shard_ordinal: int = 1,
        clock=lambda: NOW,
    ):
        return spool_finalized_physical_blob_artifacts(
            config=config or self.config,
            verified_binding=binding or self.binding,
            frozen_descriptors=descriptors,
            inventory_shard_ordinal=shard_ordinal,
            now=NOW,
            term_recheck_clock=clock,
        )

    def test_snapshots_finalized_blobs_and_emits_canonical_handoffs_and_inventory(self) -> None:
        first = self.descriptor(record_id="blob-record-0001")
        second = self.descriptor(
            record_id="blob-record-0002",
            relative_path="records/blob-two.bin",
            content=self.second_content,
        )
        first_source_before = self.first_path.stat()

        result = self.spool([second, first])

        self.assertEqual(["blob-record-0001", "blob-record-0002"], [
            item.source_record_id for item in result.artifacts
        ])
        self.assertEqual(self.first_content, result.artifacts[0].snapshot_path.read_bytes())
        self.assertEqual(self.second_content, result.artifacts[1].snapshot_path.read_bytes())
        self.assertNotEqual(first_source_before.st_ino, result.artifacts[0].snapshot_path.stat().st_ino)
        self.assertEqual(self.first_content, self.first_path.read_bytes())
        self.assertEqual(1, self.first_path.stat().st_nlink)
        handoff_raw = result.artifacts[0].handoff_descriptor_path.read_bytes()
        handoff = json.loads(handoff_raw)
        self.assertEqual(PHYSICAL_BLOB_ARTIFACT_HANDOFF_SCHEMA, handoff["schema"])
        self.assertEqual("blob-record-0001", handoff["source_record"]["record_id"])
        self.assertTrue(handoff["not_a_database_snapshot_consistency_proof"])
        self.assertTrue(handoff["not_a_blob_frontier_manifest"])
        self.assertEqual(canonical(handoff), handoff_raw)
        self.assertNotIn("source_relative_path", handoff_raw.decode("ascii"))
        inventory_raw = result.inventory_shard.plaintext_path.read_bytes()
        inventory = json.loads(inventory_raw)
        self.assertEqual(PHYSICAL_BLOB_INVENTORY_SHARD_PLAINTEXT_SCHEMA, inventory["schema"])
        self.assertEqual(2, result.inventory_shard.entry_count)
        self.assertEqual(
            ["blob-record-0001", "blob-record-0002"],
            [entry["source_record_id"] for entry in inventory["entries"]],
        )
        self.assertEqual(canonical(inventory), inventory_raw)
        self.assertEqual(digest(inventory_raw), result.inventory_shard.plaintext_sha256)
        self.assertTrue(inventory["not_a_blob_frontier_manifest"])
        self.assertTrue(inventory["not_a_database_snapshot_consistency_proof"])

    def test_reverse_ir_to_fi_route_is_bound_to_ir_writer_and_fi_recipient(self) -> None:
        term = witnessed_term(holder_site="webapp_ir")
        manifest = self.manifest_binding(
            source="webapp_ir", destination="webapp_fi", recipient=FI_RECIPIENT
        )
        binding = authorize_physical_blob_artifact_binding(
            manifest_binding=manifest,
            witnessed_term=term,
            now=NOW,
        )
        descriptor = replace(
            self.descriptor(),
            source_site="webapp_ir",
            destination_site="webapp_fi",
            destination_age_recipient=FI_RECIPIENT,
            witnessed_term_proof_sha256=term.proof_sha256,
        )

        result = self.spool([descriptor], binding=binding)

        self.assertIn("webapp_ir-to-webapp_fi", result.artifacts[0].object_key)
        handoff = json.loads(result.artifacts[0].handoff_descriptor_path.read_bytes())
        self.assertEqual("webapp_ir", handoff["writer_term"]["holder_site"])
        self.assertEqual(FI_RECIPIENT, handoff["destination_age_recipient"])

    def test_wrong_active_term_recipient_or_baseline_lsn_fails_before_spool_mutation(self) -> None:
        wrong_term = replace(self.descriptor(), writer_epoch=42)
        wrong_recipient = replace(self.descriptor(), destination_age_recipient=FI_RECIPIENT)
        wrong_lsn = replace(self.descriptor(), baseline_wal_lsn="0/1900000")
        wrong_root = replace(self.descriptor(), uploads_root_identity_sha256="b" * 64)

        for item in (wrong_term, wrong_recipient, wrong_lsn, wrong_root):
            with self.subTest(item=item):
                with self.assertRaises(PhysicalBlobArtifactSpoolError):
                    self.spool([item])
                self.assertEqual([], list(self.spool_root.iterdir()))

    def test_wrong_route_holder_cannot_authorize_reverse_artifact_binding(self) -> None:
        reverse = self.manifest_binding(
            source="webapp_ir", destination="webapp_fi", recipient=FI_RECIPIENT
        )
        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "does not hold"):
            authorize_physical_blob_artifact_binding(
                manifest_binding=reverse,
                witnessed_term=self.term,
                now=NOW,
            )

    def test_temporary_inflight_unfinalized_and_temp_paths_are_rejected(self) -> None:
        temp_path = self.write_source("records/blob-three.tmp", b"temporary-content")
        descriptors = (
            replace(self.descriptor(), temporary=True),
            replace(self.descriptor(), inflight=True),
            replace(self.descriptor(), finalization_state="in_progress"),
            replace(
                self.descriptor(),
                source_record_id="blob-record-0003",
                source_relative_path="records/blob-three.tmp",
                declared_content_sha256=digest(temp_path.read_bytes()),
                declared_content_bytes=temp_path.stat().st_size,
            ),
        )
        for item in descriptors:
            with self.subTest(item=item):
                with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "temporary|in-flight|unfinalized"):
                    self.spool([item])
                self.assertEqual([], list(self.spool_root.iterdir()))

    def test_path_escape_symlink_hardlink_and_hash_mismatch_are_fail_closed(self) -> None:
        outside = self.root / "outside.bin"
        outside.write_bytes(self.first_content)
        os.chmod(outside, 0o600)
        symlink_path = self.records_directory / "symlink.bin"
        symlink_path.symlink_to(outside)
        linked_path = self.records_directory / "hardlink.bin"
        os.link(self.first_path, linked_path)
        os.chmod(linked_path, 0o600)
        cases = (
            replace(self.descriptor(), source_relative_path="../outside.bin"),
            replace(self.descriptor(), source_relative_path="records/symlink.bin"),
            replace(self.descriptor(), source_relative_path="records/hardlink.bin"),
            replace(self.descriptor(), declared_content_sha256="b" * 64),
        )
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(PhysicalBlobArtifactSpoolError):
                    self.spool([item])
                snapshots = self.spool_root / "snapshots"
                self.assertFalse(snapshots.exists() and any(snapshots.rglob("*.blob")))

    def test_public_source_mode_is_rejected_before_snapshot(self) -> None:
        os.chmod(self.first_path, 0o644)
        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "exact protected"):
            self.spool([self.descriptor()])
        snapshots = self.spool_root / "snapshots"
        self.assertFalse(snapshots.exists() and any(snapshots.rglob("*.blob")))

    def test_source_metadata_race_is_detected_without_source_modification_by_the_spool(self) -> None:
        source_inode = self.first_path.stat().st_ino
        original_fstat = os.fstat
        source_calls = 0

        def racing_fstat(fd: int):
            nonlocal source_calls
            value = original_fstat(fd)
            if value.st_ino == source_inode:
                source_calls += 1
                if source_calls == 2:
                    os.utime(self.first_path, None)
                    return original_fstat(fd)
            return value

        with patch.object(blob_spool.os, "fstat", side_effect=racing_fstat):
            with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "changed during immutable snapshot"):
                self.spool([self.descriptor()])

        self.assertEqual(self.first_content, self.first_path.read_bytes())
        snapshots = self.spool_root / "snapshots"
        self.assertFalse(snapshots.exists() and any(snapshots.rglob("*.blob")))

    def test_duplicate_input_replay_and_exact_retry_are_fail_closed_or_idempotent(self) -> None:
        descriptor = self.descriptor()
        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "replay"):
            self.spool([descriptor, descriptor])
        self.assertEqual([], list(self.spool_root.iterdir()))

        first = self.spool([descriptor])
        second = self.spool([descriptor])
        self.assertEqual(first.artifacts[0].handoff_descriptor_path, second.artifacts[0].handoff_descriptor_path)
        self.assertEqual(first.inventory_shard.plaintext_path, second.inventory_shard.plaintext_path)
        self.assertEqual(1, len(list((self.spool_root / "handoffs").iterdir())))
        self.assertEqual(1, len(list((self.spool_root / "source-record-index").iterdir())))

        changed = b"finalized-upload-one-changed"
        self.first_path.write_bytes(changed)
        os.chmod(self.first_path, 0o600)
        replay = replace(
            descriptor,
            declared_content_sha256=digest(changed),
            declared_content_bytes=len(changed),
        )
        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "replayed"):
            self.spool([replay])

    def test_disabled_config_and_expired_term_do_not_create_local_artifacts(self) -> None:
        disabled = replace(self.config, enabled=False)
        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "disabled"):
            self.spool([self.descriptor()], config=disabled)
        self.assertEqual([], list(self.spool_root.iterdir()))

        with self.assertRaisesRegex(PhysicalBlobArtifactSpoolError, "not live"):
            spool_finalized_physical_blob_artifacts(
                config=self.config,
                verified_binding=self.binding,
                frozen_descriptors=[self.descriptor()],
                inventory_shard_ordinal=1,
                now=NOW + timedelta(seconds=47),
                term_recheck_clock=lambda: NOW + timedelta(seconds=47),
            )
        self.assertEqual([], list(self.spool_root.iterdir()))

    def test_import_surface_is_local_only_and_default_off(self) -> None:
        source = Path(blob_spool.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_top_levels = {
            alias.name.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_top_levels.update(
            node.module.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertFalse(
            {"boto3", "botocore", "requests", "socket", "subprocess", "urllib", "sqlalchemy"}
            & imported_top_levels
        )
        self.assertFalse(PHYSICAL_BLOB_ARTIFACT_SPOOL_DEFAULT_ENABLED)
        self.assertNotIn("pg_basebackup", source)
        self.assertNotIn("restore", source.lower().split("does not query")[0])


if __name__ == "__main__":
    unittest.main()
