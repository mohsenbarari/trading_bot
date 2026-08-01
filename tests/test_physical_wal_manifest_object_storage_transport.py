from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from core.physical_wal_manifest_object_storage_transport import (
    PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS,
    PhysicalWalManifestObjectStoragePublisher,
    PhysicalWalManifestObjectStoragePublishConfig,
    PhysicalWalManifestObjectStorageReceiver,
    PhysicalWalManifestObjectStorageReceiverConfig,
    PhysicalWalManifestObjectStorageTransportBinding,
    PhysicalWalManifestObjectStorageTransportError,
    PhysicalWalManifestReceiverPin,
    derive_physical_wal_manifest_object_key,
    parse_physical_wal_manifest_publication_receipt,
    require_verified_physical_wal_manifest_object_storage_stage,
    verify_physical_wal_manifest_object_storage_package,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)


CAMPAIGN = "physical-wal-manifest-fi-ir-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
BASE_GENERATION = "fi-ir-manifest-base-20260731"
SYSTEM_IDENTIFIER = "7234567890123456789"
TERM_PROOF = "a" * 64
ROUTE_BINDING = "b" * 64
RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
WAL_SEGMENT_SIZE = 16 * 1024 * 1024


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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


class FakeObjectStorageClient:
    """S3-shaped local test double with intentionally no listing method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.versioning = "Enabled"
        self.owner = "canonical-owner-20260731"
        self.grants: object = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": self.owner},
                "Permission": "FULL_CONTROL",
            }
        ]
        self.record: dict[str, object] | None = None
        self.mutate_get_body: bytes | None = None

    def get_bucket_versioning(self, *, Bucket: str):
        self.calls.append(("get_bucket_versioning", {"Bucket": Bucket}))
        return {"Status": self.versioning}

    def get_bucket_acl(self, *, Bucket: str):
        self.calls.append(("get_bucket_acl", {"Bucket": Bucket}))
        return {"Owner": {"ID": self.owner}, "Grants": self.grants}

    def put_object(self, **request: object):
        self.calls.append(("put_object", dict(request)))
        if request.get("IfNoneMatch") != "*":
            raise AssertionError("transport omitted conditional create-only precondition")
        if self.record is not None:
            raise RuntimeError("synthetic conditional conflict")
        body = request.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise AssertionError("invalid upload body")
        ciphertext = body.read()
        if not isinstance(ciphertext, bytes):
            raise AssertionError("invalid upload body bytes")
        self.record = {
            "key": request["Key"],
            "version_id": "manifest-version-20260731-01",
            "ciphertext": ciphertext,
            "metadata": dict(request["Metadata"]),
        }
        return {"VersionId": self.record["version_id"]}

    def head_object(self, **request: object):
        self.calls.append(("head_object", dict(request)))
        if self.record is None:
            raise AssertionError("head before put")
        return {
            "VersionId": self.record["version_id"],
            "ContentLength": len(self.record["ciphertext"]),
            "Metadata": dict(self.record["metadata"]),
        }

    def get_object(self, **request: object):
        self.calls.append(("get_object", dict(request)))
        if self.record is None:
            raise AssertionError("get before put")
        if request.get("Key") != self.record["key"] or request.get("VersionId") != self.record["version_id"]:
            raise AssertionError("transport did not read the pinned exact version")
        ciphertext = self.mutate_get_body
        if ciphertext is None:
            ciphertext = self.record["ciphertext"]
        return {
            "VersionId": self.record["version_id"],
            "ContentLength": len(ciphertext),
            "Metadata": dict(self.record["metadata"]),
            "Body": FakeBody(ciphertext),
        }


class FakeAgeEncryptor:
    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        if recipient != RECIPIENT:
            raise AssertionError("unexpected recipient")
        ciphertext_path.write_bytes(b"age-encryption.org/v1\n" + plaintext_path.read_bytes())
        os.chmod(ciphertext_path, 0o600)


class FakeAgeDecryptor:
    def decrypt(
        self,
        *,
        expected_recipient: str,
        ciphertext_path: Path,
        plaintext_path: Path,
    ) -> None:
        if expected_recipient != RECIPIENT:
            raise AssertionError("unexpected recipient")
        ciphertext = ciphertext_path.read_bytes()
        header = b"age-encryption.org/v1\n"
        if not ciphertext.startswith(header):
            raise RuntimeError("synthetic invalid age payload")
        plaintext_path.write_bytes(ciphertext[len(header) :])
        os.chmod(plaintext_path, 0o600)


class Ed25519Signer:
    def __init__(self, private: Ed25519PrivateKey) -> None:
        self._private = private

    def public_key_bytes(self) -> bytes:
        return self._private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def sign(self, *, message: bytes) -> bytes:
        return self._private.sign(message)


class Ed25519Verifier:
    def verify(self, *, public_key: bytes, message: bytes, signature: bytes) -> None:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)


@unittest.skipUnless(os.geteuid() == 0, "transport contract explicitly requires root")
class PhysicalWalManifestObjectStorageTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-wal-manifest-transport-")
        self.root = Path(self.temporary.name).resolve()
        self.source_workspace = self.root / "source-workspace"
        self.receiver_workspace = self.root / "receiver-workspace"
        self.staging_root = self.root / "receiver-stage"
        for directory in (self.source_workspace, self.receiver_workspace, self.staging_root):
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self.signer = Ed25519Signer(self.private)
        self.verifier = Ed25519Verifier()
        self.client = FakeObjectStorageClient()
        self.binding = self.make_binding()
        self.bundle = self.make_bundle()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def descriptor(self, kind: str, key: str, *, version: str, marker: str) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": marker * 64,
            "ciphertext_bytes": 4096,
            "encryption": "age-v1",
            "age_recipient": RECIPIENT,
            "immutability": "versioned_create_only_readback_v1",
        }

    def make_bundle(self):
        base = build_physical_wal_base_backup_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=9,
            writer_lease_id="writer-lease-nine",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self.descriptor(
                "physical_postgresql_base_backup",
                "physical-wal/fi-ir/base/backup-001.age",
                version="base-version-001",
                marker="c",
            ),
            source_signer=self.private,
        )
        base_sha = sha(canonical(base))
        wal = build_physical_wal_segment_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=9,
            writer_lease_id="writer-lease-nine",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=base_sha,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
            previous_end_lsn="0/1000000",
            previous_segment_ordinal=0,
            segments=(
                {
                    "ordinal": 1,
                    "wal_segment_name": "000000010000000000000001",
                    "timeline_id": 1,
                    "start_lsn": "0/1000000",
                    "end_lsn": "0/2000000",
                    "object": self.descriptor(
                        "postgresql_wal_segment",
                        "physical-wal/fi-ir/wal/00000001.age",
                        version="wal-version-001",
                        marker="d",
                    ),
                },
            ),
            source_signer=self.private,
        )
        blob = build_physical_wal_blob_frontier_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=9,
            writer_lease_id="writer-lease-nine",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256=base_sha,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1000000",
            blob_object_frontier_wal_lsn="0/2000000",
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": "e" * 64,
                    "plaintext_bytes": 99,
                    "entry_count": 1,
                    "object": self.descriptor(
                        "blob_inventory_shard",
                        "physical-wal/fi-ir/blob/inventory-001.age",
                        version="blob-version-001",
                        marker="f",
                    ),
                },
            ),
            source_signer=self.private,
        )
        return verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base,
            wal_segment_manifests=(wal,),
            blob_frontier_manifest=blob,
            expected_source_public_key=self.public,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=9,
            expected_writer_lease_id="writer-lease-nine",
            expected_witnessed_term_proof_sha256=TERM_PROOF,
            expected_baseline_generation_id=BASE_GENERATION,
            expected_wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            expected_destination_age_recipient=RECIPIENT,
        )

    def make_binding(self) -> PhysicalWalManifestObjectStorageTransportBinding:
        # The base-manifest hash is deterministic from the exact same manifest
        # reconstructed in make_bundle, so set it after bundle creation there.
        # This placeholder is replaced by binding_for_bundle before publishing.
        return PhysicalWalManifestObjectStorageTransportBinding(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            source_public_key=self.public,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=9,
            writer_lease_id="writer-lease-nine",
            witnessed_term_proof_sha256=TERM_PROOF,
            baseline_generation_id=BASE_GENERATION,
            baseline_manifest_sha256="1" * 64,
            database_system_identifier=SYSTEM_IDENTIFIER,
            timeline_id=1,
            wal_segment_size_bytes=WAL_SEGMENT_SIZE,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            destination_age_recipient=RECIPIENT,
            route_binding_sha256=ROUTE_BINDING,
        )

    def binding_for_bundle(self) -> PhysicalWalManifestObjectStorageTransportBinding:
        base = self.bundle.baseline
        return PhysicalWalManifestObjectStorageTransportBinding(
            source_site=base.source_site,
            destination_site=base.destination_site,
            source_public_key=base.source_public_key,
            campaign_id=base.campaign_id,
            release_sha=base.release_sha,
            writer_epoch=base.writer_term.epoch,
            writer_lease_id=base.writer_term.lease_id,
            witnessed_term_proof_sha256=base.writer_term.witnessed_term_proof_sha256,
            baseline_generation_id=base.baseline_generation_id,
            baseline_manifest_sha256=base.manifest_sha256,
            database_system_identifier=base.database_system_identifier,
            timeline_id=base.timeline_id,
            wal_segment_size_bytes=base.wal_segment_size_bytes,
            baseline_wal_lsn=base.baseline_wal_lsn,
            wal_chain_start_lsn=base.wal_chain_start_lsn,
            base_backup_end_lsn=base.base_backup_end_lsn,
            destination_age_recipient=base.base_backup_object.age_recipient,
            route_binding_sha256=ROUTE_BINDING,
        )

    def source_config(self, *, enabled: bool = True) -> PhysicalWalManifestObjectStoragePublishConfig:
        return PhysicalWalManifestObjectStoragePublishConfig(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            workspace=self.source_workspace,
            bucket="private-manifest-bucket",
            region="ir-thr-at1",
            destination_age_recipient=RECIPIENT,
            enabled=enabled,
        )

    def receiver_config(self) -> PhysicalWalManifestObjectStorageReceiverConfig:
        return PhysicalWalManifestObjectStorageReceiverConfig(
            receiver_site="webapp_ir",
            workspace=self.receiver_workspace,
            staging_root=self.staging_root,
            bucket="private-manifest-bucket",
            region="ir-thr-at1",
            enabled=True,
        )

    def publisher(self, *, enabled: bool = True) -> PhysicalWalManifestObjectStoragePublisher:
        return PhysicalWalManifestObjectStoragePublisher(
            config=self.source_config(enabled=enabled),
            age_encryptor_factory=lambda: FakeAgeEncryptor(),
            client_factory=lambda: self.client,
            signer=self.signer,
            verifier=self.verifier,
        )

    def publish(self):
        self.binding = self.binding_for_bundle()
        return self.publisher().publish(verified_bundle=self.bundle, binding=self.binding)

    def pin(self, receipt) -> PhysicalWalManifestReceiverPin:
        return PhysicalWalManifestReceiverPin(
            binding=self.binding,
            expected_receipt_sha256=receipt.receipt_sha256,
            expected_object_key=receipt.object_key,
            expected_version_id=receipt.version_id,
            expected_bundle_manifest_sha256=receipt.bundle_manifest_sha256,
            expected_package_sha256=receipt.package_sha256,
        )

    def test_publish_exact_readback_and_pinned_receiver_stage(self) -> None:
        receipt = self.publish()

        self.assertEqual("published-readback-verified", json.loads(receipt.canonical_receipt)["status"])
        self.assertIn("route-" + ROUTE_BINDING, receipt.object_key)
        self.assertIn("baseline-" + BASE_GENERATION, receipt.object_key)
        self.assertEqual(
            receipt.object_key,
            derive_physical_wal_manifest_object_key(
                binding=self.binding,
                bundle_manifest_sha256=receipt.bundle_manifest_sha256,
            ),
        )
        self.assertFalse(any(name == "list_object_versions" for name, _request in self.client.calls))
        put_request = next(request for name, request in self.client.calls if name == "put_object")
        self.assertEqual("*", put_request["IfNoneMatch"])
        self.assertNotIn("latest", receipt.object_key)

        receiver = PhysicalWalManifestObjectStorageReceiver(
            config=self.receiver_config(),
            client_factory=lambda: self.client,
            age_decryptor_factory=lambda: FakeAgeDecryptor(),
            verifier=self.verifier,
        )
        result = receiver.stage(receipt_bytes=receipt.canonical_receipt, pin=self.pin(receipt))

        self.assertEqual(PHYSICAL_WAL_MANIFEST_OBJECT_STORAGE_STAGE_STATUS, result.status)
        self.assertFalse(result.idempotent)
        self.assertEqual(self.bundle, result.verified_bundle)
        self.assertEqual(receipt.package_sha256, sha(result.package_path.read_bytes()))
        self.assertEqual(0o600, result.package_path.stat().st_mode & 0o777)
        self.assertEqual(result, require_verified_physical_wal_manifest_object_storage_stage(result))
        self.assertTrue(
            all(
                request.get("VersionId") == receipt.version_id
                for name, request in self.client.calls
                if name in {"head_object", "get_object"}
            )
        )

    def test_receiver_rejects_unpinned_version_and_ciphertext_mutation(self) -> None:
        receipt = self.publish()
        receiver = PhysicalWalManifestObjectStorageReceiver(
            config=self.receiver_config(),
            client_factory=lambda: self.client,
            age_decryptor_factory=lambda: FakeAgeDecryptor(),
            verifier=self.verifier,
        )
        wrong_pin = PhysicalWalManifestReceiverPin(
            binding=self.binding,
            expected_receipt_sha256=receipt.receipt_sha256,
            expected_object_key=receipt.object_key,
            expected_version_id="other-version",
            expected_bundle_manifest_sha256=receipt.bundle_manifest_sha256,
            expected_package_sha256=receipt.package_sha256,
        )
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "PIN_MISMATCH"):
            receiver.stage(receipt_bytes=receipt.canonical_receipt, pin=wrong_pin)

        assert self.client.record is not None
        self.client.mutate_get_body = bytes(self.client.record["ciphertext"]) + b"tampered"
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "GET_IDENTITY_MISMATCH"):
            receiver.stage(receipt_bytes=receipt.canonical_receipt, pin=self.pin(receipt))

    def test_disabled_publisher_and_non_integer_receipt_size_fail_closed(self) -> None:
        self.binding = self.binding_for_bundle()
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "PUBLISH_DISABLED"):
            self.publisher(enabled=False).publish(verified_bundle=self.bundle, binding=self.binding)

        receipt = self.publish()
        altered = json.loads(receipt.canonical_receipt)
        altered["plaintext_bytes"] = True
        unsigned = {key: value for key, value in altered.items() if key != "receipt_sha256"}
        altered["receipt_sha256"] = sha(canonical(unsigned))
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "RECEIPT_INVALID"):
            parse_physical_wal_manifest_publication_receipt(canonical(altered))

    def test_owner_only_acl_and_outer_signature_are_required(self) -> None:
        self.binding = self.binding_for_bundle()
        self.client.grants = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": "foreign-owner"},
                "Permission": "READ",
            }
        ]
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "ACL_NOT_OWNER_ONLY"):
            self.publisher().publish(verified_bundle=self.bundle, binding=self.binding)

        self.client.grants = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": self.client.owner},
                "Permission": "FULL_CONTROL",
            }
        ]
        receipt = self.publish()
        assert self.client.record is not None
        encrypted = bytes(self.client.record["ciphertext"])
        raw = encrypted[len(b"age-encryption.org/v1\n") :]
        package = json.loads(raw)
        package["transport_signature"]["signature_base64"] = "A" * 88
        with self.assertRaisesRegex(PhysicalWalManifestObjectStorageTransportError, "SIGNATURE_INVALID"):
            verify_physical_wal_manifest_object_storage_package(
                package_bytes=canonical(package),
                binding=self.binding,
                verifier=self.verifier,
            )


if __name__ == "__main__":
    unittest.main()
