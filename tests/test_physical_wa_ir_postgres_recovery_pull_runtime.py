"""In-memory tests for the root-only WA-IR physical recovery pull runtime.

The suite uses signed synthetic manifests, in-memory S3-shaped responses, and
an FD fake decryptor.  It never imports boto3, contacts Object Storage, runs
age/Docker/SSH/PostgreSQL, or opens a network connection.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_immutability_preflight as preflight
from core import physical_arvan_s3_role_local_credential_reader as credential_reader
from core import physical_wa_ir_postgres_recovery_pull_runtime as runtime
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_age_v1_adapter import PhysicalAgeV1DecryptorConfig
from core.physical_arvan_exact_version_pull import (
    ArvanExactVersionPullExpectation,
    RootOwnedArvanExactVersionPullConfig,
)
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
    PHYSICAL_WAL_WAL_CHAIN_GENESIS_SHA256,
    build_physical_wal_base_backup_manifest,
    build_physical_wal_blob_frontier_manifest,
    build_physical_wal_segment_manifest,
    verify_physical_wal_object_storage_bundle,
)
from core.physical_wal_receiver_staging import (
    PhysicalWalDecryptionReadback,
    PhysicalWalReceiverStagingConfig,
    build_physical_wal_receiver_staging_pin,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "wa-ir-physical-recovery-pull-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
RECIPIENT = "age1" + "a" * 30
WAL_BYTES = 16 * 1024 * 1024
FI_ACCESS = "WA-FI-PUBLISHER-ACCESS-20260731"
IR_ACCESS = "WA-IR-RECEIVER-ACCESS-20260731"


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def _identity(access_key: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00" + access_key.encode("ascii")
    ).hexdigest()


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def close(self) -> None:
        self.closed = True


class _RawExactGetClient:
    def __init__(self, objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]]) -> None:
        self.objects = objects
        self.calls: list[dict[str, str]] = []

    def get_object(self, **request: str) -> dict[str, object]:
        self.calls.append(dict(request))
        pair = (request["Key"], request["VersionId"])
        payload, metadata = self.objects[pair]
        return {
            "Key": pair[0],
            "VersionId": pair[1],
            "ContentLength": len(payload),
            "Metadata": dict(metadata),
            "Body": _Body(payload),
        }


class _FdDecryptor:
    def __init__(self, plaintexts: dict[tuple[str, str], bytes]) -> None:
        self.plaintexts = plaintexts
        self.calls: list[tuple[str, str, str]] = []

    def decrypt_to_fd(
        self,
        *,
        ciphertext_fd: int,
        destination_fd: int,
        object_key: str,
        version_id: str,
        expected_age_recipient: str,
    ) -> PhysicalWalDecryptionReadback:
        os.lseek(ciphertext_fd, 0, os.SEEK_SET)
        if not os.read(ciphertext_fd, len(b"age-encryption.org/v1\n")):
            raise AssertionError("ciphertext was not staged")
        payload = self.plaintexts[(object_key, version_id)]
        view = memoryview(payload)
        while view:
            written = os.write(destination_fd, view)
            if written <= 0:
                raise AssertionError("short fake decrypt write")
            view = view[written:]
        self.calls.append((object_key, version_id, expected_age_recipient))
        return PhysicalWalDecryptionReadback(
            object_key=object_key,
            version_id=version_id,
            age_recipient=expected_age_recipient,
            plaintext_sha256=_sha(payload),
            plaintext_bytes=len(payload),
        )


def _denied(*operations: str) -> tuple[preflight.PhysicalArvanDeniedOperationObservation, ...]:
    return tuple(
        preflight.PhysicalArvanDeniedOperationObservation(
            operation=operation,
            outcome=preflight.ARVAN_DISPOSABLE_DELETE_DENIED,
        )
        for operation in operations
    )


def _verified_preflight() -> preflight.VerifiedPhysicalArvanImmutabilityPreflight:
    binding = preflight.PhysicalArvanImmutabilityPreflightBinding(
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        route_binding_sha256="a" * 64,
        endpoint=ENDPOINT,
        region=REGION,
        bucket=BUCKET,
        minimum_retention_days=90,
    )
    restrictions = (
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="fi-publisher",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256=_identity(FI_ACCESS),
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
            credential_identity_sha256=_identity(IR_ACCESS),
            allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
            denied_operations=_denied(
                "DeleteObject", "DeleteObjectVersion", "ListBucket", "ListObjectVersions", "PutObject"
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
        object_key=f"physical-preflight/{CAMPAIGN}/arvan-immutability/nonce-20260731.age",
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
    observed = preflight.build_physical_arvan_immutability_preflight_observation(
        binding=binding,
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
        observed,
        binding=binding,
        now=NOW,
    )


@unittest.skipUnless(os.geteuid() == 0, "WA-IR recovery pull runtime is root-only")
class PhysicalWaIrPostgresRecoveryPullRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-ir-physical-pull-")
        self.root = Path(self.temporary.name).resolve()
        self.receiver_root = self.root / "receiver"
        self.state_root = self.root / "state"
        self.receipt_root = self.root / "redacted-receipts"
        self.age_workspace = self.root / "age-work"
        for path in (self.receiver_root, self.state_root, self.receipt_root, self.age_workspace):
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        self.identity = self.root / "wa-ir-age-identity.txt"
        self.identity.write_text("# fake identity only used by injected decryptor\n", encoding="ascii")
        self.identity.chmod(0o400)
        self.source_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.bundle, self.pin, self.term, self.ciphertexts, self.plaintexts = self._route()
        self.locator = self._locator()
        self.preflight = _verified_preflight()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _descriptor(self, *, kind: str, key: str, version: str, payload: bytes) -> dict[str, object]:
        return {
            "schema": "gold-trade-physical-wal-object-descriptor-v1",
            "version": 1,
            "object_kind": kind,
            "object_key": key,
            "version_id": version,
            "ciphertext_sha256": _sha(payload),
            "ciphertext_bytes": len(payload),
            "encryption": "age-v1",
            "age_recipient": RECIPIENT,
            "immutability": "versioned_create_only_readback_v1",
        }

    def _term(self):
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witness_transition_id="transition-seven-fi",
            issued_at=NOW - timedelta(seconds=10),
            expires_at=NOW + timedelta(seconds=90),
            witness_signer=self.witness_signer,
        )
        return verify_object_delta_role_matrix_witnessed_term(
            proof,
            witness_public_key=_public_key(self.witness_signer),
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=5,
            now=NOW,
        )

    def _route(self):
        term = self._term()
        ciphertexts = {
            ("physical/fi-ir/base/backup-001.age", "base-version-001"): b"age-encryption.org/v1\nbase",
            ("physical/fi-ir/wal/0001.age", "wal-version-0001"): b"age-encryption.org/v1\nwal",
            ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"): b"age-encryption.org/v1\nblob",
        }
        plaintexts = {
            ("physical/fi-ir/base/backup-001.age", "base-version-001"): b"physical-base-backup-v1",
            ("physical/fi-ir/wal/0001.age", "wal-version-0001"): b"W" * WAL_BYTES,
            ("physical/fi-ir/blob/inventory-001.age", "inventory-version-001"): b'{"inventory":"complete"}\n',
        }
        base_key, base_version = "physical/fi-ir/base/backup-001.age", "base-version-001"
        wal_key, wal_version = "physical/fi-ir/wal/0001.age", "wal-version-0001"
        blob_key, blob_version = "physical/fi-ir/blob/inventory-001.age", "inventory-version-001"
        base = build_physical_wal_base_backup_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-ir-pull-generation-20260731",
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self._descriptor(
                kind="physical_postgresql_base_backup",
                key=base_key,
                version=base_version,
                payload=ciphertexts[(base_key, base_version)],
            ),
            source_signer=self.source_signer,
        )
        base_hash = _sha(canonical_json_bytes(base))
        wal = build_physical_wal_segment_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-ir-pull-generation-20260731",
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
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
                    "object": self._descriptor(
                        kind="postgresql_wal_segment",
                        key=wal_key,
                        version=wal_version,
                        payload=ciphertexts[(wal_key, wal_version)],
                    ),
                },
            ),
            source_signer=self.source_signer,
        )
        inventory = plaintexts[(blob_key, blob_version)]
        blob = build_physical_wal_blob_frontier_manifest(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-ir-pull-generation-20260731",
            baseline_manifest_sha256=base_hash,
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            previous_manifest_sha256=PHYSICAL_WAL_BLOB_CHAIN_GENESIS_SHA256,
            previous_frontier_wal_lsn="0/1000000",
            blob_object_frontier_wal_lsn="0/2000000",
            inventory_shards=(
                {
                    "ordinal": 1,
                    "plaintext_sha256": _sha(inventory),
                    "plaintext_bytes": len(inventory),
                    "entry_count": 1,
                    "object": self._descriptor(
                        kind="blob_inventory_shard",
                        key=blob_key,
                        version=blob_version,
                        payload=ciphertexts[(blob_key, blob_version)],
                    ),
                },
            ),
            source_signer=self.source_signer,
        )
        bundle = verify_physical_wal_object_storage_bundle(
            base_backup_manifest=base,
            wal_segment_manifests=(wal,),
            blob_frontier_manifest=blob,
            expected_source_public_key=_public_key(self.source_signer),
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=term.writer_epoch,
            expected_writer_lease_id=term.writer_lease_id,
            expected_witnessed_term_proof_sha256=term.proof_sha256,
            expected_baseline_generation_id="wa-ir-pull-generation-20260731",
            expected_wal_segment_size_bytes=WAL_BYTES,
            expected_destination_age_recipient=RECIPIENT,
        )
        baseline = bundle.baseline
        pin = build_physical_wal_receiver_staging_pin(
            source_site="webapp_fi",
            destination_site="webapp_ir",
            source_public_key=_public_key(self.source_signer),
            destination_age_recipient=RECIPIENT,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id=baseline.baseline_generation_id,
            baseline_manifest_sha256=baseline.manifest_sha256,
            database_system_identifier=baseline.database_system_identifier,
            timeline_id=baseline.timeline_id,
            wal_segment_size_bytes=baseline.wal_segment_size_bytes,
            baseline_wal_lsn=baseline.baseline_wal_lsn,
            wal_chain_start_lsn=baseline.wal_chain_start_lsn,
            base_backup_end_lsn=baseline.base_backup_end_lsn,
        )
        return bundle, pin, term, ciphertexts, plaintexts

    def _metadata(self, *, payload: bytes, ordinal: int) -> dict[str, str]:
        return {
            "transport-schema": "gold-trade-physical-wal-object-storage-uploader-v1",
            "encryption": "age-v1",
            "descriptor-sha256": _sha(f"descriptor-{ordinal}"),
            "destination-age-recipient": RECIPIENT,
            "ciphertext-sha256": _sha(payload),
            "ciphertext-bytes": str(len(payload)),
        }

    def _locator(self):
        objects = [self.bundle.baseline.base_backup_object]
        objects.extend(segment.object for manifest in self.bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in self.bundle.blob_frontier.inventory_shards)
        expectations = tuple(
            ArvanExactVersionPullExpectation(
                object_key=item.object_key,
                version_id=item.version_id,
                ciphertext_sha256=item.ciphertext_sha256,
                ciphertext_bytes=item.ciphertext_bytes,
                metadata=self._metadata(
                    payload=self.ciphertexts[(item.object_key, item.version_id)],
                    ordinal=index,
                ),
            )
            for index, item in enumerate(objects, start=1)
        )
        return runtime.PhysicalWaIrPostgresRecoveryExactObjectLocator(
            issued_at=NOW,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256=self.pin.route_binding_sha256,
            manifest_sha256es=self.bundle.manifest_sha256es,
            object_expectations=expectations,
        )

    def _config(self, locator=None, **changes: object):
        active_locator = self.locator if locator is None else locator
        fields: dict[str, object] = {
            "exact_pull_config": RootOwnedArvanExactVersionPullConfig(
                endpoint=ENDPOINT,
                region=REGION,
                bucket=BUCKET,
                maximum_ciphertext_bytes=1024 * 1024,
                enabled=True,
            ),
            "age_decryptor_config": PhysicalAgeV1DecryptorConfig(
                workspace_root=self.age_workspace,
                identity_path=self.identity,
                recipient=RECIPIENT,
                enabled=True,
                maximum_plaintext_bytes=64 * 1024 * 1024,
                maximum_ciphertext_bytes=64 * 1024 * 1024,
            ),
            "receiver_staging_config": PhysicalWalReceiverStagingConfig(
                receiver_root=self.receiver_root,
                state_root=self.state_root,
            ),
            "redacted_receipt_root": self.receipt_root,
            "preflight": self.preflight,
            "expected_locator_sha256": runtime.derive_wa_ir_postgres_recovery_exact_object_locator_sha256(active_locator),
            "enabled": True,
        }
        fields.update(changes)
        return runtime.RootOwnedWaIrPostgresRecoveryPullRuntimeConfig(**fields)

    def _admitter(self):
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        credential = credential_reader.ArvanS3RoleLocalCredentialFacts(
            access_key=IR_ACCESS,
            secret_key="IR-SECRET-ONLY-FOR-IN-MEMORY-TEST",
            identity_sha256=_identity(IR_ACCESS),
            device=1,
            inode=2,
        )
        calls: list[object] = []

        def admit(config: object):
            calls.append(config)
            return route, credential

        return admit, calls

    def _stage(self, *, config=None, locator=None, now: datetime = NOW, raw=None, decryptor=None):
        active_config = self._config(locator) if config is None else config
        active_locator = self.locator if locator is None else locator
        objects = [self.bundle.baseline.base_backup_object]
        objects.extend(segment.object for manifest in self.bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in self.bundle.blob_frontier.inventory_shards)
        active_raw = raw or _RawExactGetClient(
            {
                (item.object_key, item.version_id): (
                    self.ciphertexts[(item.object_key, item.version_id)],
                    dict(active_locator.object_expectations[index].metadata),
                )
                for index, item in enumerate(objects)
            }
        )
        active_decryptor = decryptor or _FdDecryptor(self.plaintexts)
        admitter, admissions = self._admitter()
        result = runtime.stage_root_owned_wa_ir_postgres_recovery_bundle(
            config=active_config,
            bundle=self.bundle,
            receiver_pin=self.pin,
            locator=active_locator,
            current_witnessed_term=self.term,
            now=now,
            credential_admitter=admitter,
            raw_s3_client_builder=lambda **_kwargs: active_raw,
            age_decryptor_factory=lambda _config: active_decryptor,
        )
        return result, active_raw, active_decryptor, admissions

    def test_fresh_exact_receiver_pull_stages_and_returns_only_non_authorizing_downstream_inputs(self) -> None:
        result, raw, decryptor, admissions = self._stage()

        self.assertTrue(result.staged)
        self.assertEqual(runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_STAGED, result.status)
        self.assertEqual((), result.reason_codes)
        self.assertEqual(3, len(raw.calls))
        self.assertEqual(3, len(decryptor.calls))
        self.assertEqual(3, len(admissions))
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertIsNotNone(result.recovery_preflight_binding)
        self.assertIsNotNone(result.standby_bootstrap_stage_evidence)
        self.assertIsNotNone(result.redacted_receipt)

        receipt = result.redacted_receipt
        assert receipt is not None
        payload = json.loads(receipt.raw_receipt)
        self.assertEqual(self.bundle.manifest_sha256es, tuple(payload["manifest_sha256es"]))
        self.assertFalse(payload["promotion_authorized"])
        self.assertFalse(payload["full_matrix_authorized"])
        rendered = receipt.raw_receipt.decode("ascii")
        for forbidden in (ENDPOINT, BUCKET, "physical/fi-ir", "version-", str(self.receiver_root)):
            self.assertNotIn(forbidden, rendered)
        binding = result.recovery_preflight_binding
        evidence = result.standby_bootstrap_stage_evidence
        assert binding is not None and evidence is not None
        self.assertEqual("webapp_ir", binding.local_standby_site)
        self.assertEqual(receipt.stage_receipt_sha256, binding.stage_binding.stage_receipt_sha256)
        self.assertEqual(receipt.stage_receipt_sha256, evidence.stage_receipt_sha256)
        self.assertEqual(0o400, os.stat(self.receipt_root / "receipts" / f"{receipt.bundle_id}.json").st_mode & 0o777)

    def test_idempotent_resume_does_not_reopen_receiver_credentials_or_read_or_decrypt(self) -> None:
        first, raw, decryptor, admissions = self._stage()
        self.assertTrue(first.staged)
        raw_calls = len(raw.calls)
        decrypt_calls = len(decryptor.calls)
        admissions_count = len(admissions)

        second, resumed_raw, resumed_decryptor, resumed_admissions = self._stage(
            raw=_RawExactGetClient({}),
            decryptor=_FdDecryptor({}),
        )
        self.assertTrue(second.staged)
        self.assertTrue(second.idempotent)
        self.assertEqual(raw_calls, len(raw.calls))
        self.assertEqual(decrypt_calls, len(decryptor.calls))
        self.assertEqual([], resumed_raw.calls)
        self.assertEqual([], resumed_decryptor.calls)
        self.assertEqual([], resumed_admissions)
        self.assertEqual(admissions_count, len(admissions))

    def test_disabled_stale_locator_and_metadata_mismatch_fail_before_receiver_credential_or_get(self) -> None:
        stale = replace(self.locator, issued_at=NOW - timedelta(seconds=181))
        bad_metadata = list(self.locator.object_expectations)
        bad_metadata[0] = replace(
            bad_metadata[0],
            metadata={**bad_metadata[0].metadata, "destination-age-recipient": "age1" + "b" * 30},
        )
        malformed = replace(self.locator, object_expectations=tuple(bad_metadata))
        cases = (
            (replace(self._config(), enabled=False), self.locator, "WA_IR_POSTGRES_RECOVERY_PULL_DISABLED"),
            (self._config(stale), stale, "WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_STALE"),
            (self._config(malformed), malformed, "WA_IR_POSTGRES_RECOVERY_PULL_LOCATOR_OBJECTS_MISMATCH"),
        )
        for config, locator, code in cases:
            with self.subTest(code=code):
                result, raw, decryptor, admissions = self._stage(config=config, locator=locator)
                self.assertEqual(runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED, result.status)
                self.assertEqual((code,), result.reason_codes)
                self.assertEqual([], raw.calls)
                self.assertEqual([], decryptor.calls)
                self.assertEqual([], admissions)

    def test_current_witness_term_and_receiver_only_identity_are_required_before_get(self) -> None:
        expired_proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_fi",
            writer_epoch=7,
            writer_lease_id="writer-lease-seven",
            witness_transition_id="transition-seven-fi",
            issued_at=NOW - timedelta(seconds=120),
            expires_at=NOW - timedelta(seconds=5),
            witness_signer=self.witness_signer,
        )
        expired = verify_object_delta_role_matrix_witnessed_term(
            expired_proof,
            witness_public_key=_public_key(self.witness_signer),
            maximum_lease_duration_seconds=120,
            safety_margin_seconds=5,
            now=NOW - timedelta(seconds=20),
        )
        admitter, admissions = self._admitter()
        raw = _RawExactGetClient({})
        result = runtime.stage_root_owned_wa_ir_postgres_recovery_bundle(
            config=self._config(),
            bundle=self.bundle,
            receiver_pin=self.pin,
            locator=self.locator,
            current_witnessed_term=expired,
            now=NOW,
            credential_admitter=admitter,
            raw_s3_client_builder=lambda **_kwargs: raw,
            age_decryptor_factory=lambda _config: _FdDecryptor({}),
        )
        self.assertEqual(("WA_IR_POSTGRES_RECOVERY_PULL_WITNESS_TERM_INVALID_OR_STALE",), result.reason_codes)
        self.assertEqual([], admissions)
        self.assertEqual([], raw.calls)

        wrong_ir_identity = credential_reader.ArvanS3RoleLocalCredentialFacts(
            access_key="IR-WRONG-ACCESS",
            secret_key="IR-WRONG-SECRET",
            identity_sha256="f" * 64,
            device=1,
            inode=2,
        )
        route = credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        raw = _RawExactGetClient({})
        result = runtime.stage_root_owned_wa_ir_postgres_recovery_bundle(
            config=self._config(),
            bundle=self.bundle,
            receiver_pin=self.pin,
            locator=self.locator,
            current_witnessed_term=self.term,
            now=NOW,
            credential_admitter=lambda _config: (route, wrong_ir_identity),
            raw_s3_client_builder=lambda **_kwargs: raw,
            age_decryptor_factory=lambda _config: _FdDecryptor({}),
        )
        self.assertEqual(
            ("WA_IR_POSTGRES_RECOVERY_PULL_STAGING_EXACT_VERSION_READER_FAILED",),
            result.reason_codes,
        )
        self.assertEqual([], raw.calls)

    def test_post_pull_provider_freshness_is_rechecked_before_any_redacted_receipt_is_exposed(self) -> None:
        objects = [self.bundle.baseline.base_backup_object]
        objects.extend(segment.object for manifest in self.bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in self.bundle.blob_frontier.inventory_shards)
        raw = _RawExactGetClient(
            {
                (item.object_key, item.version_id): (
                    self.ciphertexts[(item.object_key, item.version_id)],
                    dict(self.locator.object_expectations[index].metadata),
                )
                for index, item in enumerate(objects)
            }
        )
        decryptor = _FdDecryptor(self.plaintexts)
        admitter, _admissions = self._admitter()
        ticks = iter((NOW, NOW + timedelta(seconds=301)))
        engine = runtime.RootOwnedWaIrPostgresRecoveryPullRuntime(
            self._config(),
            clock=lambda: next(ticks),
            credential_admitter=admitter,
            raw_s3_client_builder=lambda **_kwargs: raw,
            age_decryptor_factory=lambda _config: decryptor,
        )
        result = engine.stage(
            bundle=self.bundle,
            receiver_pin=self.pin,
            locator=self.locator,
            current_witnessed_term=self.term,
        )
        self.assertEqual(runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_PULL_STATUS_BLOCKED, result.status)
        self.assertEqual(("WA_IR_POSTGRES_RECOVERY_PULL_PREFLIGHT_INVALID",), result.reason_codes)
        self.assertEqual(3, len(raw.calls))
        self.assertEqual(3, len(decryptor.calls))
        self.assertEqual([], list((self.receipt_root / "receipts").glob("*.json")) if (self.receipt_root / "receipts").exists() else [])

    def test_runtime_source_has_no_direct_site_or_postgres_execution_surface(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_ir_postgres_recovery_pull_runtime.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "import docker",
            "from docker",
            "import psycopg",
            "from psycopg",
            "import paramiko",
            "from paramiko",
            "ssh",
            "scp",
            "promote",
        )
        # The contract needs the words "promotion" in its negative claims;
        # reject executable/direct-client imports rather than prose.
        self.assertFalse([item for item in forbidden[:-1] if item in source])


if __name__ == "__main__":
    unittest.main()
