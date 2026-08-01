"""Injected tests for the FI-only PostgreSQL Object-Storage handoff runtime.

Every S3-shaped dependency is in-memory and injected.  These tests never
import boto3, contact Arvan, open a network socket, run age, Docker, SSH, or
PostgreSQL.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from core import physical_arvan_immutability_preflight as preflight
from core import physical_arvan_s3_fi_publisher_role_factory as fi_role_factory
from core import physical_arvan_s3_role_local_credential_reader as credential_reader
from core import physical_wa_fi_postgres_object_storage_handoff_runtime as runtime
from core.physical_age_v1_adapter import PhysicalAgeV1EncryptorConfig
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_archive_spool import PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA
from core.physical_wal_base_backup_spool import PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA
from core.physical_wal_base_backup_spool import PhysicalWalBaseBackupSpoolResult
from core.physical_wal_object_manifest import PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "a" * 30
WAL_BYTES = 16 * 1024 * 1024
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wa_fi_postgres_object_storage_handoff_runtime.py"
)


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _identity(access_key: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00" + access_key.encode("ascii")
    ).hexdigest()


_FI_ACCESS = "FI-ONLY-PUBLISHER-ACCESS-20260731"
_FI_SECRET = "FI-ONLY-PUBLISHER-SECRET-20260731"
_IR_ACCESS = "IR-RECEIVER-ACCESS-20260731"


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def close(self) -> None:
        self.closed = True


class _MemoryRecoveryS3:
    """Minimal raw S3 double backing the factory's private FI wrapper."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.record: dict[str, object] | None = None
        self.last_body: _Body | None = None

    def get_bucket_versioning(self, **request: object) -> dict[str, object]:
        self.calls.append(("get_bucket_versioning", dict(request)))
        return {"Status": "Enabled"}

    def get_bucket_acl(self, **request: object) -> dict[str, object]:
        self.calls.append(("get_bucket_acl", dict(request)))
        return {
            "Owner": {"ID": "canonical-owner"},
            "Grants": [
                {
                    "Grantee": {"Type": "CanonicalUser", "ID": "canonical-owner"},
                    "Permission": "FULL_CONTROL",
                }
            ],
        }

    def list_object_versions(self, **request: object) -> dict[str, object]:
        self.calls.append(("list_object_versions", dict(request)))
        prefix = request.get("Prefix")
        if self.record is None:
            versions: list[dict[str, object]] = []
        else:
            versions = [
                {
                    "Key": prefix,
                    "VersionId": self.record["version_id"],
                    "IsLatest": True,
                }
            ]
        return {"Versions": versions, "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object) -> dict[str, object]:
        self.calls.append(("put_object", dict(request)))
        if self.record is not None:
            raise AssertionError("the test double only permits one create")
        body = request.get("Body")
        if not callable(getattr(body, "read", None)):
            raise AssertionError("Object body is not readable")
        payload = body.read()
        if not isinstance(payload, bytes):
            raise AssertionError("Object body is not bytes")
        self.record = {
            "key": request["Key"],
            "version_id": "recovery-version-20260731-01",
            "payload": payload,
            "metadata": dict(request["Metadata"]),
        }
        return {"VersionId": self.record["version_id"]}

    def head_object(self, **request: object) -> dict[str, object]:
        self.calls.append(("head_object", dict(request)))
        assert self.record is not None
        return {
            "VersionId": self.record["version_id"],
            "ContentLength": len(self.record["payload"]),
            "Metadata": dict(self.record["metadata"]),
        }

    def get_object(self, **request: object) -> dict[str, object]:
        self.calls.append(("get_object", dict(request)))
        assert self.record is not None
        self.last_body = _Body(self.record["payload"])
        return {
            "VersionId": self.record["version_id"],
            "Metadata": dict(self.record["metadata"]),
            "Body": self.last_body,
        }


class _FakeAgeEncryptor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, Path]] = []

    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None:
        self.calls.append((recipient, plaintext_path, ciphertext_path))
        ciphertext_path.write_bytes(
            b"age-encryption.org/v1\n" + hashlib.sha256(plaintext_path.read_bytes()).digest()
        )
        os.chmod(ciphertext_path, 0o600)


def _binding(**changes: object) -> preflight.PhysicalArvanImmutabilityPreflightBinding:
    fields: dict[str, object] = {
        "campaign_id": "fi-object-handoff-20260731",
        "release_sha": RELEASE,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "a" * 64,
        "endpoint": ENDPOINT,
        "region": REGION,
        "bucket": BUCKET,
        "minimum_retention_days": 90,
    }
    fields.update(changes)
    return preflight.PhysicalArvanImmutabilityPreflightBinding(**fields)


def _denied(*operations: str) -> tuple[preflight.PhysicalArvanDeniedOperationObservation, ...]:
    return tuple(
        preflight.PhysicalArvanDeniedOperationObservation(
            operation=operation,
            outcome=preflight.ARVAN_DISPOSABLE_DELETE_DENIED,
        )
        for operation in operations
    )


def _verified_preflight(
    *,
    binding: preflight.PhysicalArvanImmutabilityPreflightBinding | None = None,
):
    supplied = _binding() if binding is None else binding
    restrictions = (
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="fi-publisher",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256=_identity(_FI_ACCESS),
            allowed_operations=(
                "GetBucketAcl",
                "GetBucketVersioning",
                "GetObjectLockConfiguration",
                "PutObject:create-only",
                "ListObjectVersions:exact-key",
                "GetObjectRetention:exact-version",
                "GetObject:exact-version",
                "HeadObject:exact-version",
            ),
            denied_operations=_denied(
                "DeleteObject", "DeleteObjectVersion", "PutObject:overwrite"
            ),
        ),
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="ir-receiver",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256=_identity(_IR_ACCESS),
            allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
            denied_operations=_denied(
                "DeleteObject",
                "DeleteObjectVersion",
                "ListBucket",
                "ListObjectVersions",
                "PutObject",
            ),
        ),
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="witness-controller",
            credential_posture="no-object-storage-credential-issued",
            credential_identity_sha256=None,
            allowed_operations=(),
            denied_operations=(),
        ),
    )
    disposable = preflight.PhysicalArvanDisposableImmutabilityProbe(
        object_key=(
            f"physical-preflight/{supplied.campaign_id}/arvan-immutability/"
            "nonce-20260731.age"
        ),
        version_id="preflight-version-20260731",
        ciphertext_sha256="d" * 64,
        ciphertext_bytes=427,
        delete_version_outcome="access-denied",
        delete_marker_outcome="access-denied",
        exact_version_get_outcome="exact-version-get-succeeded",
        retrieved_version_id="preflight-version-20260731",
        retrieved_ciphertext_sha256="d" * 64,
        retrieved_ciphertext_bytes=427,
    )
    observation = preflight.build_physical_arvan_immutability_preflight_observation(
        binding=supplied,
        versioning_status="Enabled",
        acl_posture="private-canonical-owner-only-v1",
        retention_mode="provider-verified-immutable-retention-v1",
        retention_policy_evidence_sha256="e" * 64,
        retention_days=180,
        credential_restrictions=restrictions,
        disposable_probe=disposable,
        observed_at=NOW,
    )
    return preflight.verify_physical_arvan_immutability_preflight(
        observation,
        binding=supplied,
        now=NOW,
    )


@unittest.skipUnless(os.geteuid() == 0, "FI Object-Storage runtime is root-only")
class PhysicalWaFiPostgresObjectStorageHandoffRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-object-handoff-")
        self.root = Path(self.temporary.name).resolve()
        self.age_workspace = self.root / "age"
        self.uploader_workspace = self.root / "uploader"
        self.spool_root = self.root / "spool"
        for path in (self.age_workspace, self.uploader_workspace, self.spool_root):
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        self.preflight = _verified_preflight()
        route_policy = ArvanS3RoleLocalRoutePolicy(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
            enabled=True,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        )
        self.fi_publisher_factory = fi_role_factory.RootOwnedArvanS3FiPublisherRoleFactory(
            fi_role_factory.RootOwnedArvanS3FiPublisherRoleFactoryConfig(
                route_policy=route_policy,
                enabled=True,
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _config(
        self,
        *,
        enabled: bool = True,
        wal_policy: runtime.RootOwnedWaFiPostgresObjectStorageUploaderPolicy | None = None,
        base_backup_policy: runtime.RootOwnedWaFiPostgresObjectStorageUploaderPolicy | None = None,
        preflight_value=None,
    ) -> runtime.RootOwnedWaFiPostgresObjectStorageHandoffConfig:
        policies = [policy for policy in (wal_policy, base_backup_policy) if policy is not None]
        if not policies:
            wal_policy = runtime.RootOwnedWaFiPostgresObjectStorageUploaderPolicy(
                workspace=self.uploader_workspace,
                spool_root=self.spool_root,
                destination_age_recipient=RECIPIENT,
                maximum_plaintext_bytes=WAL_BYTES,
            )
        return runtime.RootOwnedWaFiPostgresObjectStorageHandoffConfig(
            fi_publisher_factory=self.fi_publisher_factory,
            preflight=self.preflight if preflight_value is None else preflight_value,
            age_encryptor_config=PhysicalAgeV1EncryptorConfig(
                workspace_root=self.age_workspace,
                recipient=RECIPIENT,
                enabled=True,
                maximum_plaintext_bytes=WAL_BYTES,
                maximum_ciphertext_bytes=WAL_BYTES + 1024 * 1024,
            ),
            wal_policy=wal_policy,
            base_backup_policy=base_backup_policy,
            base_backup_spool_reserve_bytes=1,
            enabled=enabled,
        )

    def _owner(self, **changes: object) -> runtime.RootOwnedWaFiPostgresObjectStorageHandoff:
        config = self._config(**changes)
        return runtime.RootOwnedWaFiPostgresObjectStorageHandoff(
            config,
            clock=lambda: NOW,
            age_encryptor_factory=_FakeAgeEncryptor,
        )

    def _fi_credential(self):
        return (
            credential_reader.ArvanS3RoleLocalRouteFacts(
                endpoint=ENDPOINT,
                region=REGION,
                bucket=BUCKET,
            ),
            credential_reader.ArvanS3RoleLocalCredentialFacts(
                access_key=_FI_ACCESS,
                secret_key=_FI_SECRET,
                identity_sha256=_identity(_FI_ACCESS),
                device=1,
                inode=2,
            ),
        )

    def _snapshot(self, *, suffix: str, payload: bytes) -> Path:
        digest = _sha(payload)
        directory = self.spool_root / "snapshots" / digest[:2]
        directory.mkdir(parents=True, mode=0o700)
        os.chmod(directory, 0o700)
        path = directory / f"{digest}.{suffix}"
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        return path

    def _wal_descriptor(self, *, snapshot: Path, payload: bytes) -> bytes:
        digest = _sha(payload)
        return _canonical(
            {
                "schema": PHYSICAL_WAL_ARCHIVE_SPOOL_DESCRIPTOR_SCHEMA,
                "kind": "physical_wal_segment_handoff",
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "campaign_id": "fi-object-handoff-20260731",
                "release_sha": RELEASE,
                "stream_generation_id": "fi-object-stream-20260731",
                "baseline_generation_id": "fi-object-baseline-20260731",
                "baseline_manifest_sha256": "a" * 64,
                "baseline_wal_lsn": "0/1800000",
                "wal_chain_start_lsn": "0/1000000",
                "archive_manifest_sha256": "b" * 64,
                "route_binding_sha256": "c" * 64,
                "object_storage_namespace": "physical-wal",
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": WAL_BYTES,
                "destination_age_recipient": RECIPIENT,
                "writer_term": {
                    "holder_site": "webapp_fi",
                    "writer_epoch": 73,
                    "writer_lease_id": "writer-lease-73",
                    "witnessed_term_proof_sha256": "d" * 64,
                },
                "wal_segment_name": "000000010000000000000001",
                "segment_ordinal": 1,
                "start_lsn": "0/1000000",
                "end_lsn": "0/2000000",
                "snapshot_sha256": digest,
                "snapshot_bytes": len(payload),
                "object_key": "/".join(
                    (
                        "physical-wal",
                        "fi-object-handoff-20260731",
                        RELEASE,
                        "fi-object-baseline-20260731",
                        "webapp_fi-to-webapp_ir",
                        "timeline-00000001",
                        "000000010000000000000001",
                        f"{digest}.age",
                    )
                ),
            }
        )

    def _base_descriptor(self, *, snapshot: Path, payload: bytes) -> bytes:
        digest = _sha(payload)
        return _canonical(
            {
                "schema": PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
                "kind": "physical_postgresql_base_backup_handoff",
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "campaign_id": "fi-object-handoff-20260731",
                "release_sha": RELEASE,
                "baseline_generation_id": "fi-object-baseline-20260731",
                "route_binding_sha256": "e" * 64,
                "object_storage_namespace": "physical-wal",
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": WAL_BYTES,
                "baseline_wal_lsn": "0/1800000",
                "wal_chain_start_lsn": "0/1000000",
                "base_backup_end_lsn": "0/2800000",
                "destination_age_recipient": RECIPIENT,
                "writer_term": {
                    "holder_site": "webapp_fi",
                    "epoch": 73,
                    "lease_id": "writer-lease-73",
                    "witness_transition_id": "witness-transition-73",
                    "witnessed_term_proof_sha256": "f" * 64,
                },
                "completed_source_artifact": {
                    "artifact_name": "base-backup-20260731.tar",
                    "plaintext_sha256": digest,
                    "plaintext_bytes": len(payload),
                    "completion_attestation_sha256": "1" * 64,
                },
                "snapshot_path_name": snapshot.name,
                "snapshot_sha256": digest,
                "snapshot_bytes": len(payload),
                "object_key": "/".join(
                    (
                        "physical-wal",
                        "fi-object-handoff-20260731",
                        RELEASE,
                        "fi-object-baseline-20260731",
                        "webapp_fi-to-webapp_ir",
                        "timeline-00000001",
                        "base-backup",
                        f"{digest}.age",
                    )
                ),
                "not_a_remote_apply_proof": True,
                "not_a_strict_acknowledgement_proof": True,
            }
        )

    def _factory_patches(self, raw: _MemoryRecoveryS3):
        return (
            mock.patch.object(
                fi_role_factory._credential_reader,
                "load_root_owned_arvan_s3_role_local_credential",
                return_value=self._fi_credential(),
            ),
            mock.patch.object(
                fi_role_factory._client_support,
                "load_role_local_boto_sdk",
                return_value=(object(), object()),
            ),
            mock.patch.object(
                fi_role_factory._client_support,
                "create_role_local_raw_s3_client",
                return_value=raw,
            ),
        )

    def test_wal_upload_is_fi_only_create_only_and_exact_readback(self) -> None:
        payload = b"w" * WAL_BYTES
        snapshot = self._snapshot(suffix="wal", payload=payload)
        descriptor = self._wal_descriptor(snapshot=snapshot, payload=payload)
        raw = _MemoryRecoveryS3()
        owner = self._owner()
        fi_patch, sdk_patch, raw_patch = self._factory_patches(raw)
        with (
            fi_patch,
            sdk_patch,
            raw_patch,
        ):
            receipt = owner.wal_uploader().upload(
                snapshot_path=snapshot,
                descriptor_bytes=descriptor,
                descriptor_sha256=_sha(descriptor),
            )
        self.assertEqual("age-v1", receipt.encryption)
        self.assertEqual("versioned_create_only_readback_v1", receipt.immutability)
        self.assertEqual(RECIPIENT, receipt.age_recipient)
        self.assertIsNotNone(raw.record)
        methods = [name for name, _request in raw.calls]
        self.assertEqual(
            [
                "get_bucket_versioning",
                "get_bucket_acl",
                "list_object_versions",
                "put_object",
                "list_object_versions",
                "head_object",
                "get_object",
            ],
            methods,
        )
        put_request = next(request for name, request in raw.calls if name == "put_object")
        self.assertEqual(BUCKET, put_request["Bucket"])
        self.assertEqual("*", put_request["IfNoneMatch"])
        self.assertNotIn("ServerSideEncryption", put_request)
        self.assertTrue(raw.last_body is not None and raw.last_body.closed)

    def test_base_backup_path_uses_same_fi_boundary_with_no_caller_bucket_or_key(self) -> None:
        payload = b"base-backup" * 128
        snapshot = self._snapshot(suffix="basebackup", payload=payload)
        descriptor = self._base_descriptor(snapshot=snapshot, payload=payload)
        base_policy = runtime.RootOwnedWaFiPostgresObjectStorageUploaderPolicy(
            workspace=self.uploader_workspace,
            spool_root=self.spool_root,
            destination_age_recipient=RECIPIENT,
            maximum_plaintext_bytes=len(payload),
        )
        raw = _MemoryRecoveryS3()
        owner = self._owner(wal_policy=None, base_backup_policy=base_policy)
        fi_patch, sdk_patch, raw_patch = self._factory_patches(raw)
        with fi_patch, sdk_patch, raw_patch:
            receipt = owner.base_backup_uploader().upload(
                snapshot_path=snapshot,
                descriptor_bytes=descriptor,
                descriptor_sha256=_sha(descriptor),
            )
        self.assertEqual("versioned_create_only_readback_v1", receipt.immutability)
        self.assertTrue(receipt.object_key.startswith("physical-wal/"))
        self.assertEqual(BUCKET, next(request for name, request in raw.calls if name == "put_object")["Bucket"])

    def test_disabled_or_mismatched_preflight_stops_before_fi_credential_or_sdk(self) -> None:
        payload = b"w" * WAL_BYTES
        snapshot = self._snapshot(suffix="wal", payload=payload)
        descriptor = self._wal_descriptor(snapshot=snapshot, payload=payload)
        disabled = self._owner(enabled=False)
        wrong_preflight = _verified_preflight(binding=_binding(bucket="another-private-bucket"))
        mismatch = self._owner(preflight_value=wrong_preflight)
        for owner in (disabled, mismatch):
            with self.subTest(owner=owner):
                with mock.patch.object(
                    fi_role_factory._credential_reader,
                    "load_root_owned_arvan_s3_role_local_credential",
                ) as fi_load, mock.patch.object(
                    fi_role_factory._client_support,
                    "load_role_local_boto_sdk",
                ) as sdk:
                    with self.assertRaises(runtime.PhysicalWaFiPostgresObjectStorageHandoffError):
                        owner.wal_uploader().upload(
                            snapshot_path=snapshot,
                            descriptor_bytes=descriptor,
                            descriptor_sha256=_sha(descriptor),
                        )
                fi_load.assert_not_called()
                sdk.assert_not_called()

    def test_factory_rejects_direct_escape_hatches_before_raw_client_call(self) -> None:
        admission = self.fi_publisher_factory.admit_fi_publisher_recovery_handoff(
            preflight=self.preflight,
            now=NOW,
        )
        raw = _MemoryRecoveryS3()
        fi_patch, sdk_patch, raw_patch = self._factory_patches(raw)
        with fi_patch, sdk_patch, raw_patch:
            with self.assertRaisesRegex(
                fi_role_factory.ArvanS3FiPublisherRoleFactoryError,
                "OPERATION_FAILED",
            ):
                self.fi_publisher_factory.execute_fi_publisher_recovery_handoff(
                    admission=admission,
                    now=NOW,
                    operation=lambda client, route: getattr(client, "delete_object")(),
                )
        self.assertEqual([], raw.calls)

    def test_typed_helper_handoff_is_the_only_base_backup_source(self) -> None:
        source_root = self.root / "typed-helper-source"
        source_root.mkdir(mode=0o700)
        base_policy = runtime.RootOwnedWaFiPostgresObjectStorageUploaderPolicy(
            workspace=self.uploader_workspace,
            spool_root=self.spool_root,
            destination_age_recipient=RECIPIENT,
            maximum_plaintext_bytes=1024,
        )
        owner = self._owner(wal_policy=None, base_backup_policy=base_policy)
        typed = SimpleNamespace(
            capture_source_root=source_root,
            verified_base_backup_binding=object(),
        )
        expected = PhysicalWalBaseBackupSpoolResult(
            snapshot_path=self.spool_root / "snapshots" / "a.basebackup",
            snapshot_sha256="a" * 64,
            snapshot_bytes=1,
            handoff_descriptor_path=self.spool_root / "descriptors" / "a.json",
            handoff_descriptor_sha256="b" * 64,
            completed_record_path=self.spool_root / "completed" / "a.json",
            completed_record_sha256="c" * 64,
            object_key="physical-wal/typed-helper/object.age",
            object_version_id="version-20260731-01",
            ciphertext_sha256="d" * 64,
            ciphertext_bytes=2,
        )

        def capture(**kwargs: object) -> PhysicalWalBaseBackupSpoolResult:
            config = kwargs["config"]
            self.assertEqual(source_root, config.source_root)
            self.assertEqual(self.spool_root, config.spool_root)
            self.assertIs(typed.verified_base_backup_binding, kwargs["verified_binding"])
            self.assertTrue(callable(getattr(kwargs["uploader"], "upload", None)))
            return expected

        with mock.patch.object(
            runtime._helper_bridge,
            "require_physical_wa_fi_postgres_helper_capture_bridge_handoff",
            return_value=typed,
        ) as require_typed, mock.patch.object(
            runtime,
            "capture_physical_wal_base_backup",
            side_effect=capture,
        ) as capture_base:
            result = owner.publish_helper_base_backup(
                handoff=typed,  # type: ignore[arg-type]
                now=NOW,
                term_recheck_clock=lambda: NOW,
            )
        self.assertIs(expected, result)
        self.assertEqual(2, require_typed.call_count)
        capture_base.assert_called_once()

    def test_constructor_is_inert_and_source_exposes_no_direct_external_surface(self) -> None:
        with mock.patch.object(
            fi_role_factory._client_support,
            "load_role_local_boto_sdk",
        ) as sdk, mock.patch.object(
            fi_role_factory._credential_reader,
            "load_root_owned_arvan_s3_role_local_credential",
        ) as fi_load:
            owner = self._owner()
        self.assertEqual({"_age_encryptor_factory", "_clock", "_config"}, set(vars(owner)))
        sdk.assert_not_called()
        fi_load.assert_not_called()
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            imports & {"boto3", "botocore", "socket", "subprocess", "requests", "paramiko"}
        )
        config_fields = set(runtime.RootOwnedWaFiPostgresObjectStorageHandoffConfig.__dataclass_fields__)
        self.assertFalse({"endpoint", "bucket", "object_key", "access_key", "secret_key"} & config_fields)
        self.assertNotIn("promote", source.lower())
        self.assertNotIn("full_matrix_authorized=True", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
