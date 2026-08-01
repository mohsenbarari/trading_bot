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
from core.physical_blob_artifact_spool import (
    PhysicalBlobArtifactManifestBinding,
    PhysicalBlobArtifactSpoolConfig,
    PhysicalBlobFrozenDescriptor,
    authorize_physical_blob_artifact_binding,
    derive_physical_blob_uploads_root_identity,
    spool_finalized_physical_blob_artifacts,
)
from core.physical_blob_object_storage_uploader import (
    PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED,
    PhysicalBlobObjectStorageUploader,
    PhysicalBlobObjectStorageUploaderConfig,
    PhysicalBlobObjectStorageUploaderError,
    authorize_physical_blob_object_storage_binding,
    build_physical_wal_blob_inventory_shard_from_receipt,
    derive_physical_blob_object_storage_key,
    verify_physical_blob_object_storage_receipt,
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
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, Path, Path]] = []

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        self.calls.append((recipient, plaintext_path, ciphertext_path))
        if self.fail:
            raise RuntimeError("synthetic encryption failure")
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
        self.tamper_readback = False

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
            versions.append(
                {"Key": key, "VersionId": record["version_id"], "IsLatest": True}
            )
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
        ciphertext = record["ciphertext"]
        if self.tamper_readback:
            ciphertext = b"tampered" + ciphertext
        return {
            "VersionId": record["version_id"],
            "Metadata": dict(record["metadata"]),
            "Body": FakeBody(ciphertext),
        }


@unittest.skipUnless(os.geteuid() == 0, "blob uploader contract explicitly requires root")
class PhysicalBlobObjectStorageUploaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-blob-object-uploader-")
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
        self.content = b"frozen-finalized-blob" * 256
        self.source_path = records / "blob-one.bin"
        self.source_path.write_bytes(self.content)
        os.chmod(self.source_path, 0o600)
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
        self.receipt_private_key = Ed25519PrivateKey.generate()
        self.receipt_public_key = public_key(self.receipt_private_key)
        self.spool_config = PhysicalBlobArtifactSpoolConfig(
            uploads_root=self.uploads_root,
            spool_root=self.spool_root,
            enabled=True,
            maximum_blob_bytes=1024 * 1024,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def frozen_descriptor(self) -> PhysicalBlobFrozenDescriptor:
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
            source_record_id="blob-record-0001",
            source_relative_path="records/blob-one.bin",
            uploads_root_identity_sha256=derive_physical_blob_uploads_root_identity(
                uploads_root=self.uploads_root
            ),
            declared_content_sha256=digest(self.content),
            declared_content_bytes=len(self.content),
        )

    def spool(self):
        return spool_finalized_physical_blob_artifacts(
            config=self.spool_config,
            verified_binding=self.artifact_binding,
            frozen_descriptors=[self.frozen_descriptor()],
            inventory_shard_ordinal=1,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )

    def config(self, **overrides: object) -> PhysicalBlobObjectStorageUploaderConfig:
        values: dict[str, object] = {
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "workspace": self.workspace,
            "spool_root": self.spool_root,
            "bucket": "private-physical-blobs",
            "region": "ir-thr-at1",
            "destination_age_recipient": IR_RECIPIENT,
            "receipt_signer_public_key": self.receipt_public_key,
            "enabled": True,
            "maximum_blob_plaintext_bytes": 1024 * 1024,
            "direct_site_control": "forbidden",
            "destination_object_ingest": "pull-only",
        }
        values.update(overrides)
        return PhysicalBlobObjectStorageUploaderConfig(**values)

    def uploader(
        self,
        client: FakeObjectStorageClient,
        *,
        encryptor: FakeAgeEncryptor | None = None,
        signer: Ed25519PrivateKey | None = None,
        **config: object,
    ) -> PhysicalBlobObjectStorageUploader:
        return PhysicalBlobObjectStorageUploader(
            config=self.config(**config),
            age_encryptor_factory=lambda: encryptor or FakeAgeEncryptor(),
            client_factory=lambda: client,
            receipt_signer_factory=lambda: signer or self.receipt_private_key,
        )

    def blob_receipt(
        self,
        client: FakeObjectStorageClient,
        *,
        uploader: PhysicalBlobObjectStorageUploader | None = None,
    ):
        return (uploader or self.uploader(client)).upload_blob(
            artifact=self.spool().artifacts[0],
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )

    def test_default_is_disabled(self) -> None:
        self.assertFalse(PHYSICAL_BLOB_OBJECT_STORAGE_UPLOADER_DEFAULT_ENABLED)
        client = FakeObjectStorageClient()
        uploader = self.uploader(client, enabled=False)
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "disabled"):
            self.blob_receipt(client, uploader=uploader)
        self.assertEqual([], client.calls)

    def test_blob_upload_is_v2_term_timeline_pinned_create_only_and_signed(self) -> None:
        client = FakeObjectStorageClient()
        result = self.spool()
        artifact = result.artifacts[0]
        uploader = self.uploader(client)

        receipt = uploader.upload_blob(
            artifact=artifact,
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )

        self.assertIn("timeline-00000007", receipt.object_key)
        self.assertIn("term-00000000000000000041", receipt.object_key)
        self.assertIn(digest(b"blob-record-0001"), receipt.object_key)
        self.assertNotEqual(artifact.object_key, receipt.object_key)
        self.assertEqual(
            receipt.object_key,
            derive_physical_blob_object_storage_key(
                verified_binding=self.storage_binding,
                source_record_id="blob-record-0001",
                plaintext_sha256=digest(self.content),
                now=NOW,
            ),
        )
        self.assertEqual("put_object", client.calls[2][0])
        self.assertEqual("*", client.calls[2][1]["IfNoneMatch"])
        self.assertEqual("list_object_versions", client.calls[3][0])
        self.assertEqual(IR_RECIPIENT, client.calls[2][1]["Metadata"]["destination-age-recipient"])
        verified = verify_physical_blob_object_storage_receipt(
            receipt=receipt,
            receipt_signer_public_key=self.receipt_public_key,
        )
        self.assertEqual(receipt, verified)
        raw = json.loads(receipt.signed_receipt)
        self.assertTrue(raw["readback_verified"])
        self.assertEqual("finalized_blob_object", raw["kind"])

    def test_inventory_receipt_directly_bridges_to_blob_frontier_input(self) -> None:
        client = FakeObjectStorageClient()
        result = self.spool()
        uploader = self.uploader(client)
        blob_receipt = uploader.upload_blob(
            artifact=result.artifacts[0],
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )

        receipt = uploader.upload_inventory_shard(
            inventory_shard=result.inventory_shard,
            blob_receipts=[blob_receipt],
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        bridge = build_physical_wal_blob_inventory_shard_from_receipt(
            receipt=receipt,
            blob_receipts=[blob_receipt],
            receipt_signer_public_key=self.receipt_public_key,
            verified_binding=self.storage_binding,
            now=NOW,
        )

        self.assertEqual(1, bridge["ordinal"])
        self.assertEqual(result.inventory_shard.plaintext_sha256, bridge["plaintext_sha256"])
        self.assertEqual("blob_inventory_shard", bridge["object"]["object_kind"])
        self.assertEqual(IR_RECIPIENT, bridge["object"]["age_recipient"])
        self.assertEqual(receipt.object_key, bridge["object"]["object_key"])
        self.assertIn("inventory", receipt.object_key)
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
            source_signer=self.receipt_private_key,
        )
        self.assertEqual("blob_inventory_frontier", frontier["kind"])

    def test_inventory_requires_exact_signed_coverage_before_its_put(self) -> None:
        client = FakeObjectStorageClient()
        result = self.spool()
        uploader = self.uploader(client)
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "cover"):
            uploader.upload_inventory_shard(
                inventory_shard=result.inventory_shard,
                blob_receipts=[],
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
        self.assertEqual([], client.calls)

    def test_tampered_snapshot_or_handoff_wrapper_fails_before_client_factory(self) -> None:
        result = self.spool()
        artifact = replace(result.artifacts[0], object_key="physical-blobs/forged.age")
        client = FakeObjectStorageClient()
        factory_calls = {"client": 0}
        uploader = PhysicalBlobObjectStorageUploader(
            config=self.config(),
            age_encryptor_factory=lambda: FakeAgeEncryptor(),
            client_factory=lambda: factory_calls.__setitem__("client", factory_calls["client"] + 1) or client,
            receipt_signer_factory=lambda: self.receipt_private_key,
        )
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "handoff result"):
            uploader.upload_blob(
                artifact=artifact,
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
        self.assertEqual(0, factory_calls["client"])

    def test_bool_inventory_ordinal_is_rejected_fail_closed(self) -> None:
        result = self.spool()
        malformed_inventory = replace(result.inventory_shard, shard_ordinal=True)
        client = FakeObjectStorageClient()
        uploader = self.uploader(client)
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "ordinal"):
            uploader.upload_inventory_shard(
                inventory_shard=malformed_inventory,
                blob_receipts=[],
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
        self.assertEqual([], client.calls)

    def test_readback_mismatch_never_mints_a_receipt(self) -> None:
        client = FakeObjectStorageClient()
        client.tamper_readback = True
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "read-back"):
            self.blob_receipt(client)
        self.assertEqual(1, len(client.records))
        self.assertFalse(any(name == "list_object_versions" for name, _ in client.calls[:3]))

    def test_wrong_receipt_signer_is_rejected_after_readback(self) -> None:
        client = FakeObjectStorageClient()
        uploader = self.uploader(client, signer=Ed25519PrivateKey.generate())
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "pinned public key"):
            self.blob_receipt(client, uploader=uploader)
        self.assertEqual(1, len(client.records))

    def test_expired_completion_term_leaves_only_an_unacknowledged_orphan(self) -> None:
        client = FakeObjectStorageClient()
        uploader = self.uploader(client)
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "not live|authorized"):
            uploader.upload_blob(
                artifact=self.spool().artifacts[0],
                verified_binding=self.storage_binding,
                now=NOW,
                term_recheck_clock=lambda: NOW + timedelta(seconds=46),
            )
        self.assertEqual(1, len(client.records))

    def test_bool_inside_typed_receipt_wrapper_is_not_equal_to_one(self) -> None:
        client = FakeObjectStorageClient()
        receipt = self.blob_receipt(client)
        malformed = replace(receipt, plaintext_bytes=True)
        with self.assertRaisesRegex(PhysicalBlobObjectStorageUploaderError, "plaintext bytes"):
            verify_physical_blob_object_storage_receipt(
                receipt=malformed,
                receipt_signer_public_key=self.receipt_public_key,
            )

    def test_tampered_signed_receipt_is_not_accepted_for_frontier_bridge(self) -> None:
        client = FakeObjectStorageClient()
        result = self.spool()
        uploader = self.uploader(client)
        blob_receipt = uploader.upload_blob(
            artifact=result.artifacts[0],
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        inventory_receipt = uploader.upload_inventory_shard(
            inventory_shard=result.inventory_shard,
            blob_receipts=[blob_receipt],
            verified_binding=self.storage_binding,
            now=NOW,
            term_recheck_clock=lambda: NOW,
        )
        tampered = replace(inventory_receipt, signed_receipt=inventory_receipt.signed_receipt + b" ")
        with self.assertRaises(PhysicalBlobObjectStorageUploaderError):
            build_physical_wal_blob_inventory_shard_from_receipt(
                receipt=tampered,
                blob_receipts=[blob_receipt],
                receipt_signer_public_key=self.receipt_public_key,
                verified_binding=self.storage_binding,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
