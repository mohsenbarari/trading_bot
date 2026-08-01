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

import core.physical_blob_receiver_inventory_mapping as receiver_mapping
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
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
from core.physical_wal_object_manifest import build_physical_wal_blob_frontier_manifest


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

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        result = self._value[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        return None


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


class FakeObjectStorageClient:
    """In-memory S3-shaped double; it never opens a network connection."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.records: dict[str, dict[str, object]] = {}
        self.versioning_status = "Enabled"
        self.acl_owner_id: object = "canonical-owner-20260731"
        self.acl_grants: object = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": self.acl_owner_id},
                "Permission": "FULL_CONTROL",
            }
        ]

    def get_bucket_versioning(self, *, Bucket: str):
        self.calls.append(("get_bucket_versioning", {"Bucket": Bucket}))
        return {"Status": self.versioning_status}

    def get_bucket_acl(self, *, Bucket: str):
        self.calls.append(("get_bucket_acl", {"Bucket": Bucket}))
        return {"Owner": {"ID": self.acl_owner_id}, "Grants": self.acl_grants}

    def list_object_versions(self, **request: object):
        self.calls.append(("list_object_versions", dict(request)))
        key = request["Prefix"]
        if not isinstance(key, str):
            raise AssertionError("invalid test key")
        record = self.records.get(key)
        versions: list[dict[str, object]] = []
        if record is not None:
            versions.append({"Key": key, "VersionId": record["version_id"], "IsLatest": True})
        return {"Versions": versions, "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object):
        self.calls.append(("put_object", dict(request)))
        key = request["Key"]
        if not isinstance(key, str) or request.get("IfNoneMatch") != "*":
            raise AssertionError("conditional create-only PUT was not requested")
        if key in self.records:
            raise RuntimeError("synthetic precondition conflict")
        body = request["Body"]
        if not callable(getattr(body, "read", None)):
            raise AssertionError("invalid test body")
        ciphertext = body.read()
        if not isinstance(ciphertext, bytes):
            raise AssertionError("invalid test ciphertext")
        record = {
            "version_id": f"version-20260731-{len(self.records) + 1:02d}",
            "ciphertext": ciphertext,
            "metadata": dict(request["Metadata"]),
        }
        self.records[key] = record
        return {"VersionId": record["version_id"]}

    def head_object(self, **request: object):
        self.calls.append(("head_object", dict(request)))
        key = request["Key"]
        if not isinstance(key, str) or key not in self.records:
            raise AssertionError("head before put")
        record = self.records[key]
        return {
            "VersionId": record["version_id"],
            "ContentLength": len(record["ciphertext"]),
            "Metadata": dict(record["metadata"]),
        }

    def get_object(self, **request: object):
        self.calls.append(("get_object", dict(request)))
        key = request["Key"]
        if not isinstance(key, str) or key not in self.records:
            raise AssertionError("get before put")
        record = self.records[key]
        return {
            "VersionId": record["version_id"],
            "Metadata": dict(record["metadata"]),
            "Body": FakeBody(record["ciphertext"]),
        }


@unittest.skipUnless(os.geteuid() == 0, "receiver mapping contract explicitly requires root")
class PhysicalBlobReceiverInventoryMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-blob-receiver-mapping-")
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "root-only-workspace"
        self.uploads_root = self.root / "protected-uploads"
        self.spool_root = self.root / "root-only-blob-spool"
        for directory in (self.workspace, self.uploads_root, self.spool_root):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        records = self.uploads_root / "records"
        records.mkdir(mode=0o700)
        os.chmod(records, 0o700)
        self.contents = (
            b"frozen-finalized-blob-one" * 256,
            b"frozen-finalized-blob-two" * 256,
        )
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
        self.blob_receipt_private_key = Ed25519PrivateKey.generate()
        self.blob_receipt_public_key = public_key(self.blob_receipt_private_key)
        self.mapping_private_key = Ed25519PrivateKey.generate()
        self.mapping_public_key = public_key(self.mapping_private_key)
        self.spool_config = PhysicalBlobArtifactSpoolConfig(
            uploads_root=self.uploads_root,
            spool_root=self.spool_root,
            enabled=True,
            maximum_blob_bytes=1024 * 1024,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def descriptor(self, ordinal: int) -> PhysicalBlobFrozenDescriptor:
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

    def spool(self):
        return spool_finalized_physical_blob_artifacts(
            config=self.spool_config,
            verified_binding=self.artifact_binding,
            frozen_descriptors=[self.descriptor(1), self.descriptor(2)],
            inventory_shard_ordinal=1,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )

    def storage_config(self, **overrides: object) -> PhysicalBlobObjectStorageUploaderConfig:
        values: dict[str, object] = {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "workspace": self.workspace,
            "spool_root": self.spool_root,
            "bucket": "private-physical-blobs",
            "region": "ir-thr-at1",
            "destination_age_recipient": IR_RECIPIENT,
            "receipt_signer_public_key": self.blob_receipt_public_key,
            "enabled": True,
            "maximum_blob_plaintext_bytes": 1024 * 1024,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return PhysicalBlobObjectStorageUploaderConfig(**values)

    def uploader(self, client: FakeObjectStorageClient) -> PhysicalBlobObjectStorageUploader:
        return PhysicalBlobObjectStorageUploader(
            config=self.storage_config(),
            age_encryptor_factory=FakeAgeEncryptor,
            client_factory=lambda: client,
            receipt_signer_factory=lambda: self.blob_receipt_private_key,
        )

    def mapping_config(self, **overrides: object) -> receiver_mapping.PhysicalBlobReceiverInventoryMappingConfig:
        values: dict[str, object] = {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "workspace": self.workspace,
            "spool_root": self.spool_root,
            "bucket": "private-physical-blobs",
            "region": "ir-thr-at1",
            "destination_age_recipient": IR_RECIPIENT,
            "mapping_signer_public_key": self.mapping_public_key,
            "blob_receipt_signer_public_key": self.blob_receipt_public_key,
            "enabled": True,
            "maximum_blob_plaintext_bytes": 1024 * 1024,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return receiver_mapping.PhysicalBlobReceiverInventoryMappingConfig(**values)

    def mapping_publisher(
        self, client: FakeObjectStorageClient, **overrides: object
    ) -> receiver_mapping.PhysicalBlobReceiverInventoryMappingPublisher:
        return receiver_mapping.PhysicalBlobReceiverInventoryMappingPublisher(
            config=self.mapping_config(**overrides),
            age_encryptor_factory=FakeAgeEncryptor,
            client_factory=lambda: client,
            mapping_signer_factory=lambda: self.mapping_private_key,
        )

    def uploaded_inputs(self, client: FakeObjectStorageClient):
        result = self.spool()
        uploader = self.uploader(client)
        blob_receipts = [
            uploader.upload_blob(
                artifact=artifact,
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
            for artifact in result.artifacts
        ]
        inventory_receipt = uploader.upload_inventory_shard(
            inventory_shard=result.inventory_shard,
            blob_receipts=blob_receipts,
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        return result, blob_receipts, inventory_receipt

    def verified_mapping(self):
        client = FakeObjectStorageClient()
        result, blob_receipts, inventory_receipt = self.uploaded_inputs(client)
        publisher = self.mapping_publisher(client)
        artifact = publisher.build_artifact(
            inventory_shard=result.inventory_shard,
            v1_inventory_receipt=inventory_receipt,
            blob_receipts=blob_receipts,
            verified_binding=self.storage_binding,
            now=NOW,
        )
        receipt = publisher.publish_artifact(
            artifact=artifact,
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        verified = receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
            mapping_plaintext=artifact.canonical_plaintext,
            mapping_receipt=receipt,
            original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
            mapping_signer_public_key=self.mapping_public_key,
            blob_receipt_signer_public_key=self.blob_receipt_public_key,
            verified_binding=self.storage_binding,
            now=NOW,
        )
        return client, result, blob_receipts, inventory_receipt, publisher, artifact, receipt, verified

    def test_mapping_is_separate_signed_term_timeline_pinned_and_bridges_to_frontier(self) -> None:
        client, result, _, inventory_receipt, _, artifact, receipt, verified = self.verified_mapping()

        self.assertIn("physical-blob-receiver-mappings-v2/", artifact.object_key)
        self.assertIn("timeline-00000007", artifact.object_key)
        self.assertIn("term-00000000000000000041", artifact.object_key)
        self.assertIn(result.inventory_shard.plaintext_sha256, artifact.object_key)
        self.assertNotEqual(inventory_receipt.object_key, artifact.object_key)
        self.assertEqual(2, verified.entry_count)
        self.assertEqual(artifact.object_key, receipt.object_key)
        self.assertEqual(
            receiver_mapping.PHYSICAL_BLOB_RECEIVER_INVENTORY_MAPPING_SCHEMA,
            client.records[receipt.object_key]["metadata"]["transport-schema"],
        )
        bridge = receiver_mapping.build_physical_wal_blob_inventory_shard_from_receiver_mapping(
            verified_mapping=verified,
            mapping_signer_public_key=self.mapping_public_key,
            blob_receipt_signer_public_key=self.blob_receipt_public_key,
            verified_binding=self.storage_binding,
            now=NOW,
        )
        self.assertEqual(1, bridge["ordinal"])
        self.assertEqual("blob_inventory_shard", bridge["object"]["object_kind"])
        self.assertEqual(IR_RECIPIENT, bridge["object"]["age_recipient"])
        self.assertEqual(receipt.object_key, bridge["object"]["object_key"])
        frontier = build_physical_wal_blob_frontier_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=self.manifest.campaign_id,
            release_sha=RELEASE_SHA,
            writer_epoch=41,
            writer_lease_id="writer-lease-41",
            witnessed_term_proof_sha256=self.witnessed_term.proof_sha256,
            baseline_generation_id=self.manifest.baseline_generation_id,
            baseline_manifest_sha256=self.manifest.baseline_manifest_sha256,
            database_system_identifier="7392847193847192834",
            timeline_id=7,
            wal_segment_size_bytes=16 * 1024 * 1024,
            previous_manifest_sha256="b" * 64,
            previous_frontier_wal_lsn="0/1800000",
            blob_object_frontier_wal_lsn="0/1800000",
            inventory_shards=[bridge],
            source_signer=self.blob_receipt_private_key,
        )
        self.assertEqual("blob_inventory_frontier", frontier["kind"])

    def test_receiver_rejects_a_validly_resigned_reordered_or_altered_mapping(self) -> None:
        _, result, blob_receipts, inventory_receipt, publisher, artifact, receipt, _ = self.verified_mapping()
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "receipt set"):
            publisher.build_artifact(
                inventory_shard=result.inventory_shard,
                v1_inventory_receipt=inventory_receipt,
                blob_receipts=list(reversed(blob_receipts)),
                verified_binding=self.storage_binding,
                now=NOW,
            )
        raw = json.loads(artifact.canonical_plaintext)
        raw["entries"] = list(reversed(raw["entries"]))
        for ordinal, entry in enumerate(raw["entries"], start=1):
            entry["ordinal"] = ordinal
        forged = receiver_mapping._sign_canonical(
            value={
                key: value
                for key, value in raw.items()
                if key != "source_mapping_signature"
            },
            signature_field="source_mapping_signature",
            signature_domain=receiver_mapping._MAPPING_SIGNATURE_DOMAIN,
            signer_factory=lambda: self.mapping_private_key,
            expected_public_key=self.mapping_public_key,
            label="test forged receiver mapping",
        )
        with self.assertRaisesRegex(
            receiver_mapping.PhysicalBlobReceiverInventoryMappingError,
            "receipt set|omits|reorders|alters",
        ):
            receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
                mapping_plaintext=forged,
                mapping_receipt=receipt,
                original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )

        altered = json.loads(artifact.canonical_plaintext)
        altered["entries"][0]["final_object"]["version_id"] = "forged-version"
        forged_descriptor = receiver_mapping._sign_canonical(
            value={
                key: value
                for key, value in altered.items()
                if key != "source_mapping_signature"
            },
            signature_field="source_mapping_signature",
            signature_domain=receiver_mapping._MAPPING_SIGNATURE_DOMAIN,
            signer_factory=lambda: self.mapping_private_key,
            expected_public_key=self.mapping_public_key,
            label="test forged receiver mapping",
        )
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "descriptor"):
            receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
                mapping_plaintext=forged_descriptor,
                mapping_receipt=receipt,
                original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )

        altered_receipt = json.loads(artifact.canonical_plaintext)
        altered_receipt["entries"][0]["blob_receipt"]["plaintext"]["sha256"] = "f" * 64
        forged_receipt = receiver_mapping._sign_canonical(
            value={
                key: value
                for key, value in altered_receipt.items()
                if key != "source_mapping_signature"
            },
            signature_field="source_mapping_signature",
            signature_domain=receiver_mapping._MAPPING_SIGNATURE_DOMAIN,
            signer_factory=lambda: self.mapping_private_key,
            expected_public_key=self.mapping_public_key,
            label="test forged receiver mapping",
        )
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "Blob receipt"):
            receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
                mapping_plaintext=forged_receipt,
                mapping_receipt=receipt,
                original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )

    def test_receiver_rejects_stale_binding_and_pinned_receipt_mismatch(self) -> None:
        _, result, _, _, _, artifact, receipt, verified = self.verified_mapping()
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "not live|authorized"):
            receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
                mapping_plaintext=artifact.canonical_plaintext,
                mapping_receipt=receipt,
                original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW + timedelta(seconds=46),
            )
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "not live|authorized"):
            receiver_mapping.build_physical_wal_blob_inventory_shard_from_receiver_mapping(
                verified_mapping=verified,
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW + timedelta(seconds=46),
            )
        tampered_receipt = replace(receipt, mapping_plaintext_sha256="f" * 64)
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "wrapper was tampered"):
            receiver_mapping.verify_physical_blob_receiver_inventory_mapping_plaintext(
                mapping_plaintext=artifact.canonical_plaintext,
                mapping_receipt=tampered_receipt,
                original_v1_inventory_plaintext=result.inventory_shard.plaintext_path.read_bytes(),
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )

    def test_default_disabled_rejects_before_client_factory(self) -> None:
        client = FakeObjectStorageClient()
        result, blob_receipts, inventory_receipt = self.uploaded_inputs(client)
        calls = {"client": 0}
        publisher = receiver_mapping.PhysicalBlobReceiverInventoryMappingPublisher(
            config=self.mapping_config(enabled=False),
            age_encryptor_factory=FakeAgeEncryptor,
            client_factory=lambda: calls.__setitem__("client", calls["client"] + 1) or client,
            mapping_signer_factory=lambda: self.mapping_private_key,
        )
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "disabled"):
            publisher.build_artifact(
                inventory_shard=result.inventory_shard,
                v1_inventory_receipt=inventory_receipt,
                blob_receipts=blob_receipts,
                verified_binding=self.storage_binding,
                now=NOW,
            )
        self.assertEqual(0, calls["client"])

    def test_bool_in_verified_mapping_projection_cannot_reach_frontier_bridge(self) -> None:
        _client, _result, _blob_receipts, _inventory_receipt, _publisher, _artifact, _receipt, verified = (
            self.verified_mapping()
        )
        object.__setattr__(verified, "shard_ordinal", True)
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "shard ordinal"):
            receiver_mapping.build_physical_wal_blob_inventory_shard_from_receiver_mapping(
                verified_mapping=verified,
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )

    def test_bool_in_verified_mapping_receipt_wrapper_cannot_reach_frontier_bridge(self) -> None:
        _client, _result, _blob_receipts, _inventory_receipt, _publisher, _artifact, _receipt, verified = (
            self.verified_mapping()
        )
        object.__setattr__(verified.mapping_receipt, "shard_ordinal", True)
        with self.assertRaisesRegex(receiver_mapping.PhysicalBlobReceiverInventoryMappingError, "receipt"):
            receiver_mapping.build_physical_wal_blob_inventory_shard_from_receiver_mapping(
                verified_mapping=verified,
                mapping_signer_public_key=self.mapping_public_key,
                blob_receipt_signer_public_key=self.blob_receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
