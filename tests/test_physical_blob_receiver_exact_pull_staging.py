from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import core.physical_blob_receiver_exact_pull_staging as staging
import core.physical_blob_receiver_inventory_mapping as receiver_mapping
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_arvan_exact_version_pull import RootOwnedArvanExactVersionPullConfig
from core.physical_blob_artifact_spool import (
    PhysicalBlobArtifactManifestBinding,
    PhysicalBlobArtifactSpoolConfig,
    PhysicalBlobFrozenDescriptor,
    authorize_physical_blob_artifact_binding,
    derive_physical_blob_uploads_root_identity,
    spool_finalized_physical_blob_artifacts,
)
from core.physical_blob_object_storage_uploader import (
    PhysicalBlobObjectStorageUploader,
    PhysicalBlobObjectStorageUploaderConfig,
    authorize_physical_blob_object_storage_binding,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE_SHA = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
IR_RECIPIENT = "age1" + "c" * 30


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class FakeBody:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self._offset = 0
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        result = self._value[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class FakeAgeEncryptor:
    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        del recipient
        ciphertext_path.write_bytes(
            b"age-encryption.org/v1\n" + hashlib.sha256(plaintext_path.read_bytes()).digest()
        )
        os.chmod(ciphertext_path, 0o600)


class FakePublisherStorage:
    """In-memory publisher double.  Its methods are not used by receiver pull."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def get_bucket_versioning(self, *, Bucket: str):
        del Bucket
        return {"Status": "Enabled"}

    def get_bucket_acl(self, *, Bucket: str):
        del Bucket
        return {
            "Owner": {"ID": "canonical-owner-20260731"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "canonical-owner-20260731"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }

    def list_object_versions(self, **request: object):
        key = request["Prefix"]
        assert isinstance(key, str)
        record = self.records.get(key)
        versions = []
        if record is not None:
            versions.append({"Key": key, "VersionId": record["version_id"], "IsLatest": True})
        return {"Versions": versions, "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object):
        key = request["Key"]
        assert isinstance(key, str)
        assert request["IfNoneMatch"] == "*"
        if key in self.records:
            raise RuntimeError("precondition failed")
        body = request["Body"]
        ciphertext = body.read()
        assert isinstance(ciphertext, bytes)
        result = {
            "version_id": f"version-20260731-{len(self.records) + 1:02d}",
            "ciphertext": ciphertext,
            "metadata": dict(request["Metadata"]),
        }
        self.records[key] = result
        return {"VersionId": result["version_id"]}

    def head_object(self, **request: object):
        key = request["Key"]
        assert isinstance(key, str)
        record = self.records[key]
        return {
            "VersionId": record["version_id"],
            "ContentLength": len(record["ciphertext"]),
            "Metadata": dict(record["metadata"]),
        }

    def get_object(self, **request: object):
        key = request["Key"]
        assert isinstance(key, str)
        record = self.records[key]
        return {
            "VersionId": record["version_id"],
            "ContentLength": len(record["ciphertext"]),
            "Metadata": dict(record["metadata"]),
            "Body": FakeBody(record["ciphertext"]),
        }


class FakeExactArvanClient:
    """Only the allowed exact GET surface exists; there is no listing method."""

    def __init__(self, records: dict[str, dict[str, object]]) -> None:
        self.records = records
        self.calls: list[dict[str, object]] = []
        self.tamper_metadata: dict[str, str] | None = None

    def get_object(self, *, Bucket: str, Key: str, VersionId: str):
        self.calls.append({"Bucket": Bucket, "Key": Key, "VersionId": VersionId})
        record = self.records[Key]
        if VersionId != record["version_id"]:
            raise RuntimeError("wrong immutable selector")
        metadata = dict(record["metadata"])
        if self.tamper_metadata is not None:
            metadata = dict(self.tamper_metadata)
        ciphertext = record["ciphertext"]
        assert isinstance(ciphertext, bytes)
        return {
            "Key": Key,
            "VersionId": VersionId,
            "ContentLength": len(ciphertext),
            "Metadata": metadata,
            "Body": FakeBody(ciphertext),
        }


@unittest.skipUnless(os.geteuid() == 0, "receiver exact pull contract explicitly requires root")
class PhysicalBlobReceiverExactPullStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-blob-exact-pull-")
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.uploads_root = self.root / "uploads"
        self.spool_root = self.root / "spool"
        self.stage_root = self.root / "receiver-stage"
        for directory in (self.workspace, self.uploads_root, self.spool_root, self.stage_root):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        records = self.uploads_root / "records"
        records.mkdir(mode=0o700)
        os.chmod(records, 0o700)
        self.contents = (b"finalized-blob-one" * 128, b"finalized-blob-two" * 128)
        for ordinal, content in enumerate(self.contents, start=1):
            path = records / f"blob-{ordinal}.bin"
            path.write_bytes(content)
            os.chmod(path, 0o600)
        self.witness_private_key = Ed25519PrivateKey.generate()
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witness_transition_id="witness-transition-41",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=50),
            witness_signer=self.witness_private_key,
        )
        self.witnessed_term = verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=public_key(self.witness_private_key),
            maximum_lease_duration_seconds=90,
            safety_margin_seconds=5,
            now=NOW,
        )
        self.manifest = PhysicalBlobArtifactManifestBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id="physical-blob-20260731",
            release_sha=RELEASE_SHA,
            baseline_generation_id="physical-blob-baseline-20260731",
            baseline_manifest_sha256="a" * 64,
            baseline_wal_lsn="0/1800000",
            destination_age_recipient=IR_RECIPIENT,
        )
        self.artifact_binding = authorize_physical_blob_artifact_binding(
            manifest_binding=self.manifest,
            witnessed_term=self.witnessed_term,
            now=NOW,
        )
        self.storage_binding = authorize_physical_blob_object_storage_binding(
            artifact_binding=self.artifact_binding,
            timeline_id=7,
            now=NOW,
        )
        self.blob_private_key = Ed25519PrivateKey.generate()
        self.blob_public_key = public_key(self.blob_private_key)
        self.mapping_private_key = Ed25519PrivateKey.generate()
        self.mapping_public_key = public_key(self.mapping_private_key)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _descriptor(self, ordinal: int) -> PhysicalBlobFrozenDescriptor:
        content = self.contents[ordinal - 1]
        return PhysicalBlobFrozenDescriptor(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=self.manifest.campaign_id,
            release_sha=RELEASE_SHA,
            baseline_generation_id=self.manifest.baseline_generation_id,
            baseline_manifest_sha256=self.manifest.baseline_manifest_sha256,
            baseline_wal_lsn=self.manifest.baseline_wal_lsn,
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witnessed_term_proof_sha256=self.witnessed_term.proof_sha256,
            destination_age_recipient=IR_RECIPIENT,
            source_record_id=f"blob-record-{ordinal:04d}",
            source_relative_path=f"records/blob-{ordinal}.bin",
            uploads_root_identity_sha256=derive_physical_blob_uploads_root_identity(
                uploads_root=self.uploads_root
            ),
            declared_content_sha256=digest(content),
            declared_content_bytes=len(content),
        )

    def _storage_config(self) -> PhysicalBlobObjectStorageUploaderConfig:
        return PhysicalBlobObjectStorageUploaderConfig(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            workspace=self.workspace,
            spool_root=self.spool_root,
            bucket="private-physical-blobs",
            region="ir-thr-at1",
            destination_age_recipient=IR_RECIPIENT,
            receipt_signer_public_key=self.blob_public_key,
            enabled=True,
            maximum_blob_plaintext_bytes=1024 * 1024,
            direct_site_control="forbidden",
            destination_object_ingest="pull-only",
        )

    def _mapping_config(self) -> receiver_mapping.PhysicalBlobReceiverInventoryMappingConfig:
        return receiver_mapping.PhysicalBlobReceiverInventoryMappingConfig(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            workspace=self.workspace,
            spool_root=self.spool_root,
            bucket="private-physical-blobs",
            region="ir-thr-at1",
            destination_age_recipient=IR_RECIPIENT,
            mapping_signer_public_key=self.mapping_public_key,
            blob_receipt_signer_public_key=self.blob_public_key,
            enabled=True,
            maximum_blob_plaintext_bytes=1024 * 1024,
            direct_site_control="forbidden",
            destination_object_ingest="pull-only",
        )

    def _published_inputs(self):
        publisher_storage = FakePublisherStorage()
        spool = spool_finalized_physical_blob_artifacts(
            config=PhysicalBlobArtifactSpoolConfig(
                uploads_root=self.uploads_root,
                spool_root=self.spool_root,
                enabled=True,
                maximum_blob_bytes=1024 * 1024,
            ),
            verified_binding=self.artifact_binding,
            frozen_descriptors=[self._descriptor(1), self._descriptor(2)],
            inventory_shard_ordinal=1,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        uploader = PhysicalBlobObjectStorageUploader(
            config=self._storage_config(),
            age_encryptor_factory=FakeAgeEncryptor,
            client_factory=lambda: publisher_storage,
            receipt_signer_factory=lambda: self.blob_private_key,
        )
        blob_receipts = [
            uploader.upload_blob(
                artifact=artifact,
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
            for artifact in spool.artifacts
        ]
        inventory_receipt = uploader.upload_inventory_shard(
            inventory_shard=spool.inventory_shard,
            blob_receipts=blob_receipts,
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        mapping_publisher = receiver_mapping.PhysicalBlobReceiverInventoryMappingPublisher(
            config=self._mapping_config(),
            age_encryptor_factory=FakeAgeEncryptor,
            client_factory=lambda: publisher_storage,
            mapping_signer_factory=lambda: self.mapping_private_key,
        )
        mapping_artifact = mapping_publisher.build_artifact(
            inventory_shard=spool.inventory_shard,
            v1_inventory_receipt=inventory_receipt,
            blob_receipts=blob_receipts,
            verified_binding=self.storage_binding,
            now=NOW,
        )
        mapping_receipt = mapping_publisher.publish_artifact(
            artifact=mapping_artifact,
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        return publisher_storage.records, blob_receipts, inventory_receipt, mapping_receipt

    def _stager(self, records: dict[str, dict[str, object]], exact: FakeExactArvanClient):
        return staging.PhysicalBlobReceiverExactPullStager(
            config=staging.PhysicalBlobReceiverExactPullStagingConfig(
                staging_root=self.stage_root,
                receiver_site="webapp_ir",
                receiver_age_recipient=IR_RECIPIENT,
                enabled=True,
                maximum_ciphertext_bytes=1024 * 1024,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            ),
            arvan_pull_config=RootOwnedArvanExactVersionPullConfig(
                endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
                region="ir-thr-at1",
                bucket="private-physical-blobs",
                enabled=True,
                maximum_ciphertext_bytes=1024 * 1024,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            ),
            client_factory=lambda *, endpoint, region: self._client_for_exact(
                exact, endpoint, region
            ),
            blob_receipt_signer_public_key=self.blob_public_key,
            mapping_signer_public_key=self.mapping_public_key,
        )

    @staticmethod
    def _client_for_exact(exact: FakeExactArvanClient, endpoint: str, region: str):
        if endpoint != "https://s3.ir-thr-at1.arvanstorage.ir" or region != "ir-thr-at1":
            raise AssertionError("receiver tried an unpinned endpoint or region")
        return exact

    def _require(self, observation):
        return staging.require_verified_physical_blob_receiver_exact_pull_observation(
            observation,
            config=staging.PhysicalBlobReceiverExactPullStagingConfig(
                staging_root=self.stage_root,
                receiver_site="webapp_ir",
                receiver_age_recipient=IR_RECIPIENT,
                enabled=True,
                maximum_ciphertext_bytes=1024 * 1024,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            ),
            blob_receipt_signer_public_key=self.blob_public_key,
            mapping_signer_public_key=self.mapping_public_key,
            verified_binding=self.storage_binding,
            now=NOW,
        )

    def test_exact_v2_blob_mapping_and_inventory_anchor_stage_privately(self) -> None:
        records, blob_receipts, inventory_receipt, mapping_receipt = self._published_inputs()
        exact = FakeExactArvanClient(records)
        receiver = self._stager(records, exact)

        blob = receiver.stage_blob(
            receipt=blob_receipts[0], verified_binding=self.storage_binding, now=NOW
        )
        mapping = receiver.stage_inventory_mapping(
            receipt=mapping_receipt, verified_binding=self.storage_binding, now=NOW
        )
        anchor = receiver.stage_inventory_anchor(
            inventory_receipt=inventory_receipt,
            mapping_receipt=mapping_receipt,
            verified_binding=self.storage_binding,
            now=NOW,
        )

        self.assertIs(self._require(blob), blob)
        self.assertIs(self._require(mapping), mapping)
        self.assertIs(self._require(anchor), anchor)
        self.assertEqual(blob_receipts[0].object_key, exact.calls[0]["Key"])
        self.assertEqual(blob_receipts[0].version_id, exact.calls[0]["VersionId"])
        self.assertEqual(mapping_receipt.object_key, exact.calls[1]["Key"])
        self.assertEqual(inventory_receipt.object_key, exact.calls[2]["Key"])
        self.assertEqual(3, len(exact.calls))
        for observation in (blob, mapping, anchor):
            metadata = os.stat(observation.ciphertext_path)
            self.assertEqual(0o600, stat_mode(metadata.st_mode))
            self.assertEqual(b"age-encryption.org/v1\n", observation.ciphertext_path.read_bytes()[:22])
            self.assertEqual(self.stage_root, observation.ciphertext_path.parent.parent)
        self.assertEqual(mapping_receipt.receipt_sha256, anchor.mapping_receipt_sha256)

    def test_rejects_metadata_ambiguity_raw_input_stale_term_and_local_tamper(self) -> None:
        records, blob_receipts, _, _ = self._published_inputs()
        exact = FakeExactArvanClient(records)
        receiver = self._stager(records, exact)
        with self.assertRaisesRegex(staging.PhysicalBlobReceiverExactPullStagingError, "RECEIPT_TYPE"):
            receiver.stage_blob(
                receipt=blob_receipts[0].signed_receipt,  # type: ignore[arg-type]
                verified_binding=self.storage_binding,
                now=NOW,
            )
        exact.tamper_metadata = {"encryption": "ambiguous"}
        with self.assertRaisesRegex(staging.PhysicalBlobReceiverExactPullStagingError, "TRANSPORT_READ"):
            receiver.stage_blob(
                receipt=blob_receipts[0], verified_binding=self.storage_binding, now=NOW
            )
        exact.tamper_metadata = None
        with self.assertRaisesRegex(staging.PhysicalBlobReceiverExactPullStagingError, "BINDING"):
            receiver.stage_blob(
                receipt=blob_receipts[0],
                verified_binding=self.storage_binding,
                now=NOW + timedelta(seconds=100),
            )
        observation = receiver.stage_blob(
            receipt=blob_receipts[0], verified_binding=self.storage_binding, now=NOW
        )
        observation.ciphertext_path.write_bytes(b"age-encryption.org/v1\ntampered")
        os.chmod(observation.ciphertext_path, 0o600)
        with self.assertRaisesRegex(staging.PhysicalBlobReceiverExactPullStagingError, "STAGED_FILE"):
            self._require(observation)

    def test_rejects_bool_int_wrapper_and_headerless_local_ciphertext(self) -> None:
        records, blob_receipts, _, _ = self._published_inputs()
        exact = FakeExactArvanClient(records)
        receiver = self._stager(records, exact)
        forged = replace(blob_receipts[0], ciphertext_bytes=True)
        with self.assertRaisesRegex(staging.PhysicalBlobReceiverExactPullStagingError, "RECEIPT_BINDING"):
            receiver.stage_blob(receipt=forged, verified_binding=self.storage_binding, now=NOW)
        path = self.stage_root / ".physical-blob-exact-pull-header-test" / "ciphertext.age"
        path.parent.mkdir(mode=0o700)
        os.chmod(path.parent, 0o700)
        ciphertext = b"not-age-ciphertext"
        path.write_bytes(ciphertext)
        os.chmod(path, 0o600)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with self.assertRaisesRegex(
                staging.PhysicalBlobReceiverExactPullStagingError, "ENCRYPTION_AMBIGUOUS"
            ):
                staging._verify_private_ciphertext_fd(
                    descriptor=descriptor,
                    path=path,
                    expected_sha256=digest(ciphertext),
                    expected_bytes=len(ciphertext),
                )
        finally:
            os.close(descriptor)


def stat_mode(mode: int) -> int:
    return mode & 0o777


if __name__ == "__main__":
    unittest.main()
