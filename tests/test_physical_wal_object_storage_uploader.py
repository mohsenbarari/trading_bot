from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from core.physical_wal_archive_spool import PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA
from core.physical_wal_base_backup_spool import PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA
from core.physical_wal_object_storage_uploader import (
    PhysicalWalBaseBackupObjectStorageUploader,
    PhysicalWalObjectStorageUploader,
    PhysicalWalObjectStorageUploaderConfig,
    PhysicalWalObjectStorageUploaderError,
)


SEGMENT_BYTES = 16 * 1024 * 1024
WAL_SEGMENT_NAME = "000000010000000000000001"
FI_RECIPIENT = "age1" + "a" * 30
IR_RECIPIENT = "age1" + "c" * 30
RELEASE_SHA = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FakeBody:
    def __init__(self, value: bytes, *, close_fails: bool = False) -> None:
        self._value = value
        self._offset = 0
        self.close_fails = close_fails
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._value) - self._offset
        result = self._value[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True
        if self.close_fails:
            raise OSError("synthetic close failure")


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
            raise RuntimeError("synthetic age failure")
        ciphertext_path.write_bytes(
            b"age-encryption.org/v1\n" + hashlib.sha256(plaintext_path.read_bytes()).digest()
        )
        os.chmod(ciphertext_path, 0o600)


class FakeObjectStorageClient:
    """In-memory S3-shaped double; it has no network implementation."""

    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.versioning_status = "Enabled"
        self.acl_owner_id: object = "canonical-owner-20260731"
        self.acl_grants: object = [
            {
                "Grantee": {"Type": "CanonicalUser", "ID": self.acl_owner_id},
                "Permission": "FULL_CONTROL",
            }
        ]
        self.preexisting_version = False
        self.preexisting_delete_marker = False
        self.history_version_after_put: str | None = None
        self.put_response_extra: dict[str, object] = {}
        self.head_response_extra: dict[str, object] = {}
        self.get_response_extra: dict[str, object] = {}
        self.readback_body: bytes | None = None
        self.readback_body_close_fails = False
        self.last_readback_body: FakeBody | None = None
        self._record: dict[str, object] | None = None

    def get_bucket_versioning(self, *, Bucket: str):
        self.calls.append(("get_bucket_versioning", {"Bucket": Bucket}))
        return {"Status": self.versioning_status}

    def get_bucket_acl(self, *, Bucket: str):
        self.calls.append(("get_bucket_acl", {"Bucket": Bucket}))
        return {
            "Owner": {"ID": self.acl_owner_id},
            "Grants": self.acl_grants,
        }

    def list_object_versions(self, **request: object):
        self.calls.append(("list_object_versions", dict(request)))
        key = request["Prefix"]
        if not isinstance(key, str):
            raise AssertionError("test double received an invalid key")
        versions: list[dict[str, object]] = []
        delete_markers: list[dict[str, object]] = []
        if self.preexisting_version:
            versions.append({"Key": key, "VersionId": "old-version", "IsLatest": True})
        elif self._record is not None:
            version_id = self.history_version_after_put or self._record["version_id"]
            versions.append({"Key": key, "VersionId": version_id, "IsLatest": True})
        if self.preexisting_delete_marker:
            delete_markers.append({"Key": key, "VersionId": "delete-marker", "IsLatest": True})
        return {"Versions": versions, "DeleteMarkers": delete_markers, "IsTruncated": False}

    def put_object(self, **request: object):
        self.calls.append(("put_object", dict(request)))
        self.put_calls.append(dict(request))
        body = request["Body"]
        if not callable(getattr(body, "read", None)):
            raise AssertionError("test double received an invalid body")
        ciphertext = body.read()
        if not isinstance(ciphertext, bytes):
            raise AssertionError("test double received a non-byte body")
        self._record = {
            "key": request["Key"],
            "version_id": "version-20260731-01",
            "ciphertext": ciphertext,
            "metadata": dict(request["Metadata"]),
        }
        return {"VersionId": self._record["version_id"], **self.put_response_extra}

    def head_object(self, **request: object):
        self.calls.append(("head_object", dict(request)))
        if self._record is None:
            raise AssertionError("head before put")
        response: dict[str, object] = {
            "VersionId": self._record["version_id"],
            "ContentLength": len(self._record["ciphertext"]),
            "Metadata": dict(self._record["metadata"]),
        }
        response.update(self.head_response_extra)
        return response

    def get_object(self, **request: object):
        self.calls.append(("get_object", dict(request)))
        if self._record is None:
            raise AssertionError("get before put")
        body_bytes = self.readback_body
        if body_bytes is None:
            body_bytes = self._record["ciphertext"]
        body = FakeBody(body_bytes, close_fails=self.readback_body_close_fails)
        self.last_readback_body = body
        response: dict[str, object] = {
            "VersionId": self._record["version_id"],
            "Metadata": dict(self._record["metadata"]),
            "Body": body,
        }
        response.update(self.get_response_extra)
        return response


@unittest.skipUnless(os.geteuid() == 0, "uploader contract explicitly requires root")
class PhysicalWalObjectStorageUploaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="physical-wal-object-uploader-")
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "root-only-workspace"
        self.spool_root = self.root / "root-only-spool"
        self.workspace.mkdir(mode=0o700)
        self.spool_root.mkdir(mode=0o700)
        os.chmod(self.workspace, 0o700)
        os.chmod(self.spool_root, 0o700)
        self.wal_plaintext = b"W" * SEGMENT_BYTES
        self.wal_sha256 = digest(self.wal_plaintext)
        wal_snapshot_directory = self.spool_root / "snapshots" / self.wal_sha256[:2]
        wal_snapshot_directory.mkdir(parents=True, mode=0o700)
        os.chmod(wal_snapshot_directory, 0o700)
        self.wal_snapshot = wal_snapshot_directory / f"{self.wal_sha256}.wal"
        self.wal_snapshot.write_bytes(self.wal_plaintext)
        os.chmod(self.wal_snapshot, 0o600)
        self.base_plaintext = b"trusted-completed-base-backup" * 128
        self.base_sha256 = digest(self.base_plaintext)
        base_snapshot_directory = self.spool_root / "snapshots" / self.base_sha256[:2]
        base_snapshot_directory.mkdir(parents=True, mode=0o700)
        os.chmod(base_snapshot_directory, 0o700)
        self.base_snapshot = base_snapshot_directory / f"{self.base_sha256}.basebackup"
        self.base_snapshot.write_bytes(self.base_plaintext)
        os.chmod(self.base_snapshot, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(
        self,
        *,
        source: str = "webapp_fi",
        destination: str = "webapp_ir",
        recipient: str = IR_RECIPIENT,
        object_storage_namespace: str = "physical-wal",
        maximum_plaintext_bytes: int = SEGMENT_BYTES,
        enabled: bool = True,
        bucket: str = "private-physical-recovery",
    ) -> PhysicalWalObjectStorageUploaderConfig:
        return PhysicalWalObjectStorageUploaderConfig(
            source_site=source,
            destination_site=destination,
            workspace=self.workspace,
            spool_root=self.spool_root,
            spool_owner_uid=os.geteuid(),
            bucket=bucket,
            region="ir-thr-at1",
            destination_age_recipient=recipient,
            object_storage_namespace=object_storage_namespace,
            enabled=enabled,
            maximum_plaintext_bytes=maximum_plaintext_bytes,
            direct_site_control="forbidden",
            destination_object_ingest="pull-only",
        )

    def wal_descriptor(
        self,
        *,
        source: str = "webapp_fi",
        destination: str = "webapp_ir",
        recipient: str = IR_RECIPIENT,
        object_storage_namespace: str = "physical-wal",
        wal_segment_name: str = WAL_SEGMENT_NAME,
        segment_ordinal: int = 1,
        baseline_wal_lsn: str = "0/1800000",
        wal_chain_start_lsn: str = "0/1000000",
        start_lsn: str = "0/1000000",
        end_lsn: str = "0/2000000",
    ) -> bytes:
        object_key = "/".join(
            (
                object_storage_namespace,
                "wal-spool-20260731",
                RELEASE_SHA,
                "fi-ir-wal-baseline-20260731",
                f"{source}-to-{destination}",
                "timeline-00000001",
                wal_segment_name,
                f"{self.wal_sha256}.age",
            )
        )
        return canonical(
            {
                "schema": PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA,
                "kind": "physical_wal_segment_handoff",
                "source_site": source,
                "destination_site": destination,
                "campaign_id": "wal-spool-20260731",
                "release_sha": RELEASE_SHA,
                "stream_generation_id": "fi-ir-wal-stream-20260731",
                "baseline_generation_id": "fi-ir-wal-baseline-20260731",
                "baseline_manifest_sha256": "a" * 64,
                "baseline_wal_lsn": baseline_wal_lsn,
                "wal_chain_start_lsn": wal_chain_start_lsn,
                "archive_manifest_sha256": "b" * 64,
                "route_binding_sha256": "c" * 64,
                "object_storage_namespace": object_storage_namespace,
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": SEGMENT_BYTES,
                "destination_age_recipient": recipient,
                "writer_term": {
                    "holder_site": source,
                    "writer_epoch": 41,
                    "writer_lease_id": "writer-lease-41",
                    "witnessed_term_proof_sha256": "d" * 64,
                },
                "wal_segment_name": wal_segment_name,
                "segment_ordinal": segment_ordinal,
                "start_lsn": start_lsn,
                "end_lsn": end_lsn,
                "snapshot_sha256": self.wal_sha256,
                "snapshot_bytes": SEGMENT_BYTES,
                "object_key": object_key,
            }
        )

    def base_backup_descriptor(
        self,
        *,
        source: str = "webapp_fi",
        destination: str = "webapp_ir",
        recipient: str = IR_RECIPIENT,
        object_storage_namespace: str = "physical-wal",
    ) -> bytes:
        object_key = "/".join(
            (
                object_storage_namespace,
                "physical-base-20260731",
                RELEASE_SHA,
                "physical-base-generation-20260731",
                f"{source}-to-{destination}",
                "timeline-00000001",
                "base-backup",
                f"{self.base_sha256}.age",
            )
        )
        return canonical(
            {
                "schema": PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
                "kind": "physical_postgresql_base_backup_handoff",
                "source_site": source,
                "destination_site": destination,
                "campaign_id": "physical-base-20260731",
                "release_sha": RELEASE_SHA,
                "baseline_generation_id": "physical-base-generation-20260731",
                "route_binding_sha256": "e" * 64,
                "object_storage_namespace": object_storage_namespace,
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": SEGMENT_BYTES,
                "baseline_wal_lsn": "0/1800000",
                "wal_chain_start_lsn": "0/1000000",
                "base_backup_end_lsn": "0/2800000",
                "destination_age_recipient": recipient,
                "writer_term": {
                    "holder_site": source,
                    "epoch": 42,
                    "lease_id": "writer-lease-42",
                    "witness_transition_id": "witness-transition-42",
                    "witnessed_term_proof_sha256": "f" * 64,
                },
                "completed_source_artifact": {
                    "artifact_name": "base-backup-20260731.tar",
                    "plaintext_sha256": self.base_sha256,
                    "plaintext_bytes": len(self.base_plaintext),
                    "completion_attestation_sha256": "1" * 64,
                },
                "snapshot_path_name": self.base_snapshot.name,
                "snapshot_sha256": self.base_sha256,
                "snapshot_bytes": len(self.base_plaintext),
                "object_key": object_key,
                "not_a_remote_apply_proof": True,
                "not_a_strict_acknowledgement_proof": True,
            }
        )

    def wal_uploader(
        self,
        client: FakeObjectStorageClient,
        encryptor: FakeAgeEncryptor | None = None,
        **config: object,
    ) -> PhysicalWalObjectStorageUploader:
        return PhysicalWalObjectStorageUploader(
            config=self.config(**config),
            age_encryptor_factory=lambda: encryptor or FakeAgeEncryptor(),
            client_factory=lambda: client,
        )

    def base_uploader(
        self,
        client: FakeObjectStorageClient,
        encryptor: FakeAgeEncryptor | None = None,
        **config: object,
    ) -> PhysicalWalBaseBackupObjectStorageUploader:
        return PhysicalWalBaseBackupObjectStorageUploader(
            config=self.config(
                maximum_plaintext_bytes=len(self.base_plaintext),
                **config,
            ),
            age_encryptor_factory=lambda: encryptor or FakeAgeEncryptor(),
            client_factory=lambda: client,
        )

    def upload_wal(
        self,
        client: FakeObjectStorageClient,
        *,
        uploader: PhysicalWalObjectStorageUploader | None = None,
        descriptor: bytes | None = None,
    ):
        raw = descriptor or self.wal_descriptor()
        return (uploader or self.wal_uploader(client)).upload(
            snapshot_path=self.wal_snapshot,
            descriptor_bytes=raw,
            descriptor_sha256=digest(raw),
        )

    def upload_base(
        self,
        client: FakeObjectStorageClient,
        *,
        uploader: PhysicalWalBaseBackupObjectStorageUploader | None = None,
        descriptor: bytes | None = None,
    ):
        raw = descriptor or self.base_backup_descriptor()
        return (uploader or self.base_uploader(client)).upload(
            snapshot_path=self.base_snapshot,
            descriptor_bytes=raw,
            descriptor_sha256=digest(raw),
        )

    def test_wal_upload_is_lazy_create_only_private_and_exact_readback(self) -> None:
        client = FakeObjectStorageClient()
        encryptor = FakeAgeEncryptor()
        factory_calls = {"encryptor": 0, "client": 0}
        uploader = PhysicalWalObjectStorageUploader(
            config=self.config(),
            age_encryptor_factory=lambda: factory_calls.__setitem__(
                "encryptor", factory_calls["encryptor"] + 1
            ) or encryptor,
            client_factory=lambda: factory_calls.__setitem__(
                "client", factory_calls["client"] + 1
            ) or client,
        )

        self.assertEqual({"encryptor": 0, "client": 0}, factory_calls)
        receipt = self.upload_wal(client, uploader=uploader)

        self.assertEqual(1, factory_calls["encryptor"])
        self.assertEqual(1, factory_calls["client"])
        self.assertEqual("age-v1", receipt.encryption)
        self.assertEqual("versioned_create_only_readback_v1", receipt.immutability)
        self.assertEqual(IR_RECIPIENT, receipt.age_recipient)
        self.assertEqual(1, len(client.put_calls))
        request = client.put_calls[0]
        self.assertEqual("*", request["IfNoneMatch"])
        self.assertEqual("private-physical-recovery", request["Bucket"])
        self.assertNotIn("ServerSideEncryption", request)
        self.assertNotIn("SSEKMSKeyId", request)
        self.assertEqual("age-v1", request["Metadata"]["encryption"])
        self.assertEqual(digest(self.wal_descriptor()), request["Metadata"]["descriptor-sha256"])
        self.assertTrue(client.last_readback_body is not None and client.last_readback_body.closed)
        self.assertNotIn("remote_apply", repr(receipt))
        self.assertNotIn("strict", repr(receipt))

    def test_reverse_ir_to_fi_wal_descriptor_uploads_only_under_its_pinned_route(self) -> None:
        client = FakeObjectStorageClient()
        descriptor = self.wal_descriptor(
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-failback",
        )
        uploader = self.wal_uploader(
            client,
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-failback",
        )

        receipt = self.upload_wal(client, uploader=uploader, descriptor=descriptor)

        self.assertIn("webapp_ir-to-webapp_fi", receipt.object_key)
        self.assertTrue(receipt.object_key.startswith("physical-failback/"))
        self.assertEqual(FI_RECIPIENT, receipt.age_recipient)
        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "pinned uploader route"):
            self.upload_wal(FakeObjectStorageClient(), descriptor=descriptor)

    def test_cross_namespace_substitution_fails_before_bucket_or_encryptor_access(self) -> None:
        client = FakeObjectStorageClient()
        encryptor = FakeAgeEncryptor()
        reverse_descriptor = self.wal_descriptor(
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-failback",
        )
        normal_uploader = self.wal_uploader(
            client,
            encryptor,
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-wal",
        )

        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "namespace"):
            self.upload_wal(client, uploader=normal_uploader, descriptor=reverse_descriptor)

        self.assertEqual([], client.calls)
        self.assertEqual([], encryptor.calls)

    def test_segment_zero_descriptor_keeps_the_absolute_zero_ordinal(self) -> None:
        client = FakeObjectStorageClient()
        descriptor = self.wal_descriptor(
            wal_segment_name="000000010000000000000000",
            segment_ordinal=0,
            baseline_wal_lsn="0/0",
            wal_chain_start_lsn="0/0",
            start_lsn="0/0",
            end_lsn="0/1000000",
        )

        receipt = self.upload_wal(client, descriptor=descriptor)

        self.assertIn("000000010000000000000000", receipt.object_key)
        self.assertEqual(receipt.object_key, client.put_calls[0]["Key"])

    def test_disabled_or_secret_shaped_config_fails_before_factories(self) -> None:
        calls = {"encryptor": 0, "client": 0}
        client = FakeObjectStorageClient()
        raw = self.wal_descriptor()
        for config in (
            self.config(enabled=False),
            self.config(bucket="secret-physical-recovery"),
        ):
            with self.subTest(config=config):
                uploader = PhysicalWalObjectStorageUploader(
                    config=config,
                    age_encryptor_factory=lambda: calls.__setitem__(
                        "encryptor", calls["encryptor"] + 1
                    ),
                    client_factory=lambda: calls.__setitem__("client", calls["client"] + 1) or client,
                )
                with self.assertRaises(PhysicalWalObjectStorageUploaderError):
                    uploader.upload(
                        snapshot_path=self.wal_snapshot,
                        descriptor_bytes=raw,
                        descriptor_sha256=digest(raw),
                    )
        self.assertEqual({"encryptor": 0, "client": 0}, calls)

    def test_unhashable_route_config_fails_closed(self) -> None:
        raw = self.wal_descriptor()
        config = self.config()
        object.__setattr__(config, "source_site", ["webapp_fi"])
        uploader = PhysicalWalObjectStorageUploader(
            config=config,
            age_encryptor_factory=lambda: FakeAgeEncryptor(),
            client_factory=lambda: FakeObjectStorageClient(),
        )

        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "route site"):
            uploader.upload(
                snapshot_path=self.wal_snapshot,
                descriptor_bytes=raw,
                descriptor_sha256=digest(raw),
            )

    def test_bucket_preflight_requires_versioning_and_sole_owner_acl_before_encryption(self) -> None:
        for mutate in (
            lambda client: setattr(client, "versioning_status", "Suspended"),
            lambda client: setattr(client, "acl_owner_id", ""),
            lambda client: setattr(client, "acl_grants", []),
            lambda client: setattr(
                client,
                "acl_grants",
                [
                    {
                        "Grantee": {"Type": "Group", "URI": "AllUsers"},
                        "Permission": "READ",
                    }
                ],
            ),
            lambda client: setattr(
                client,
                "acl_grants",
                [
                    {
                        "Grantee": {"Type": "CanonicalUser", "ID": "other-owner"},
                        "Permission": "FULL_CONTROL",
                    }
                ],
            ),
        ):
            with self.subTest(mutate=mutate):
                client = FakeObjectStorageClient()
                mutate(client)
                encryptor = FakeAgeEncryptor()
                with self.assertRaises(PhysicalWalObjectStorageUploaderError):
                    self.upload_wal(client, uploader=self.wal_uploader(client, encryptor))
                self.assertEqual([], encryptor.calls)
                self.assertEqual([], client.put_calls)

    def test_missing_acl_evidence_fails_closed_before_encryption(self) -> None:
        client = FakeObjectStorageClient()
        client.get_bucket_acl = None  # type: ignore[method-assign]
        encryptor = FakeAgeEncryptor()

        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "private bucket preflight"):
            self.upload_wal(client, uploader=self.wal_uploader(client, encryptor))

        self.assertEqual([], encryptor.calls)
        self.assertEqual([], client.put_calls)

    def test_preexisting_version_or_delete_marker_blocks_create_only_put(self) -> None:
        for attribute in ("preexisting_version", "preexisting_delete_marker"):
            with self.subTest(attribute=attribute):
                client = FakeObjectStorageClient()
                setattr(client, attribute, True)
                encryptor = FakeAgeEncryptor()
                with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "delete marker|reuse"):
                    self.upload_wal(client, uploader=self.wal_uploader(client, encryptor))
                self.assertEqual([], encryptor.calls)
                self.assertEqual([], client.put_calls)

    def test_provider_side_encryption_is_rejected_in_put_head_and_readback(self) -> None:
        cases = (
            ("put_response_extra", {"ServerSideEncryption": "AES256"}),
            ("head_response_extra", {"SSEKMSKeyId": "synthetic"}),
            (
                "get_response_extra",
                {"ResponseMetadata": {"HTTPHeaders": {"x-amz-server-side-encryption": "AES256"}}},
            ),
        )
        for attribute, value in cases:
            with self.subTest(attribute=attribute):
                client = FakeObjectStorageClient()
                setattr(client, attribute, value)
                with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "response is invalid"):
                    self.upload_wal(client)
                if attribute == "get_response_extra":
                    self.assertTrue(
                        client.last_readback_body is not None and client.last_readback_body.closed
                    )

    def test_exact_version_and_head_readback_mismatches_fail_closed(self) -> None:
        cases = (
            ("history_version_after_put", "different-version"),
            ("head_response_extra", {"VersionId": "different-version"}),
            ("head_response_extra", {"ContentLength": 1}),
            ("head_response_extra", {"Metadata": {"different": "metadata"}}),
        )
        for attribute, value in cases:
            with self.subTest(attribute=attribute):
                client = FakeObjectStorageClient()
                setattr(client, attribute, value)
                with self.assertRaises(PhysicalWalObjectStorageUploaderError):
                    self.upload_wal(client)

    def test_ciphertext_tamper_and_readback_close_failure_fail_closed(self) -> None:
        client = FakeObjectStorageClient()
        client.readback_body = b"tampered-ciphertext"
        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "ciphertext does not match"):
            self.upload_wal(client)
        self.assertTrue(client.last_readback_body is not None and client.last_readback_body.closed)

        client = FakeObjectStorageClient()
        client.readback_body_close_fails = True
        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "cannot be closed"):
            self.upload_wal(client)
        self.assertTrue(client.last_readback_body is not None and client.last_readback_body.closed)

    def test_encryption_failure_never_attempts_put(self) -> None:
        client = FakeObjectStorageClient()
        encryptor = FakeAgeEncryptor(fail=True)

        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "age encryption failed"):
            self.upload_wal(client, uploader=self.wal_uploader(client, encryptor))

        self.assertEqual(1, len(encryptor.calls))
        self.assertEqual([], client.put_calls)

    def test_wal_and_base_backup_grammars_are_not_dispatchable_or_confused(self) -> None:
        client = FakeObjectStorageClient()
        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "fields|schema"):
            self.upload_wal(client, descriptor=self.base_backup_descriptor())
        with self.assertRaisesRegex(PhysicalWalObjectStorageUploaderError, "fields|schema"):
            self.upload_base(client, descriptor=self.wal_descriptor())
        self.assertEqual([], client.put_calls)

    def test_base_backup_adapter_uses_distinct_descriptor_and_same_immutable_controls(self) -> None:
        client = FakeObjectStorageClient()

        receipt = self.upload_base(client)

        self.assertEqual("age-v1", receipt.encryption)
        self.assertEqual(IR_RECIPIENT, receipt.age_recipient)
        self.assertIn("/base-backup/", receipt.object_key)
        self.assertEqual("*", client.put_calls[0]["IfNoneMatch"])
        self.assertTrue(client.last_readback_body is not None and client.last_readback_body.closed)

    def test_base_backup_reverse_route_requires_its_own_recipient_and_config(self) -> None:
        client = FakeObjectStorageClient()
        descriptor = self.base_backup_descriptor(
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-failback",
        )
        uploader = self.base_uploader(
            client,
            source="webapp_ir",
            destination="webapp_fi",
            recipient=FI_RECIPIENT,
            object_storage_namespace="physical-failback",
        )

        receipt = self.upload_base(client, uploader=uploader, descriptor=descriptor)

        self.assertIn("webapp_ir-to-webapp_fi", receipt.object_key)
        self.assertTrue(receipt.object_key.startswith("physical-failback/"))
        self.assertEqual(FI_RECIPIENT, receipt.age_recipient)


if __name__ == "__main__":
    unittest.main()
