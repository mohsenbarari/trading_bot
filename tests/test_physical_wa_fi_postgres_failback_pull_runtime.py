"""No-network tests for the independent IR-to-FI exact receiver boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import tempfile
import threading
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core import physical_wa_fi_postgres_failback_pull_runtime as runtime
from core.append_only_sync_delta_batch import canonical_json_bytes
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_age_v1_adapter import PhysicalAgeV1DecryptorConfig
from core.physical_arvan_exact_version_pull import ArvanExactVersionPullExpectation
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
    PhysicalWalReceiverStagingResult,
    build_physical_wal_receiver_staging_pin,
)
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "ir-fi-failback-pull-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "f" * 30
WAL_BYTES = 16 * 1024 * 1024
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-failback"


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def close(self) -> None:
        return None


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
        plaintext = self.plaintexts[(object_key, version_id)]
        view = memoryview(plaintext)
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
            plaintext_sha256=_sha(plaintext),
            plaintext_bytes=len(plaintext),
        )


class _Factory:
    def __init__(
        self,
        *,
        configuration: preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig,
        raw: _RawExactGetClient,
    ) -> None:
        self.configuration = configuration
        self.raw = raw
        self.admits = 0
        self.requires = 0
        self.executes = 0
        self.route = runtime.PhysicalWaFiFailbackExactVersionReceiverRoute(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )

    def admit_fi_receiver_failback_exact_pull(self, *, preflight, current_witnessed_term, now):
        self.admits += 1
        return runtime.build_physical_wa_fi_failback_exact_version_receiver_admission(
            preflight=preflight,
            preflight_config=self.configuration,
            current_witnessed_term=current_witnessed_term,
            now=now,
        )

    def require_fi_receiver_failback_exact_pull_admission(
        self, admission, *, preflight, current_witnessed_term, now
    ):
        self.requires += 1
        return runtime.require_physical_wa_fi_failback_exact_version_receiver_admission(
            admission,
            preflight=preflight,
            preflight_config=self.configuration,
            current_witnessed_term=current_witnessed_term,
            now=now,
        )

    def execute_fi_receiver_failback_exact_pull(self, *, admission, now, operation):
        self.executes += 1
        return operation(self.raw, self.route)


@unittest.skipUnless(os.geteuid() == 0, "FI failback pull runtime is root-only")
class PhysicalWaFiPostgresFailbackPullRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-fi-failback-pull-")
        self.root = Path(self.temporary.name).resolve()
        self.receiver_root = self.root / "receiver"
        self.state_root = self.root / "state"
        self.receipt_root = self.root / "redacted-receipts"
        self.age_workspace = self.root / "age-work"
        for path in (self.receiver_root, self.state_root, self.receipt_root, self.age_workspace):
            path.mkdir(mode=0o700)
            os.chmod(path, 0o700)
        self.identity = self.root / "wa-fi-age-identity.txt"
        self.identity.write_text("# fake identity only used by injected decryptor\n", encoding="ascii")
        self.identity.chmod(0o400)
        self.source_signer = Ed25519PrivateKey.generate()
        self.witness_signer = Ed25519PrivateKey.generate()
        self.bundle, self.pin, self.term, self.ciphertexts, self.plaintexts = self._route()
        self.locator = self._locator()
        self.four_role_fixture = make_four_role_fixture(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            fi_publisher_identity_sha256="a" * 64,
            ir_receiver_identity_sha256="b" * 64,
            ir_publisher_identity_sha256="c" * 64,
            fi_receiver_identity_sha256="d" * 64,
            endpoint=ENDPOINT,
            region=REGION,
            reverse_bucket=BUCKET,
        )
        self.preflight_binding = self.four_role_fixture.binding
        self.live_iam = make_four_role_live_iam_durable_admission_fixture(
            binding=self.preflight_binding,
            observed_at=NOW,
        )
        observation = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
            binding=self.preflight_binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            observed_at=NOW,
        )
        self.preflight = preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
            observation,
            binding=self.preflight_binding,
            four_role_projection_binding=self.four_role_fixture.verified_binding,
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
            now=NOW,
        )
        self.preflight_config = self.four_role_fixture.preflight_config(
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _term(self):
        proof = build_object_delta_role_matrix_witnessed_term_proof(
            holder_site="webapp_ir",
            writer_epoch=11,
            writer_lease_id="writer-lease-eleven",
            witness_transition_id="transition-eleven-ir",
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

    def _route(self, *, namespace: str = "physical-failback"):
        term = self._term()
        base_key = f"{namespace}/ir-fi/base/backup-001.age"
        wal_key = f"{namespace}/ir-fi/wal/0001.age"
        blob_key = f"{namespace}/ir-fi/blob/inventory-001.age"
        ciphertexts = {
            (base_key, "base-version-001"): b"age-encryption.org/v1\nbase",
            (wal_key, "wal-version-0001"): b"age-encryption.org/v1\nwal",
            (blob_key, "inventory-version-001"): b"age-encryption.org/v1\nblob",
        }
        plaintexts = {
            (base_key, "base-version-001"): b"physical-base-backup-v1",
            (wal_key, "wal-version-0001"): b"W" * WAL_BYTES,
            (blob_key, "inventory-version-001"): b'{"inventory":"complete"}\n',
        }
        base = build_physical_wal_base_backup_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-fi-failback-generation-20260731",
            database_system_identifier="7234567890123456789",
            timeline_id=1,
            wal_segment_size_bytes=WAL_BYTES,
            baseline_wal_lsn="0/1000000",
            wal_chain_start_lsn="0/1000000",
            base_backup_end_lsn="0/1800000",
            base_backup_object=self._descriptor(
                kind="physical_postgresql_base_backup",
                key=base_key,
                version="base-version-001",
                payload=ciphertexts[(base_key, "base-version-001")],
            ),
            source_signer=self.source_signer,
        )
        base_hash = _sha(canonical_json_bytes(base))
        wal = build_physical_wal_segment_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-fi-failback-generation-20260731",
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
                        version="wal-version-0001",
                        payload=ciphertexts[(wal_key, "wal-version-0001")],
                    ),
                },
            ),
            source_signer=self.source_signer,
        )
        inventory = plaintexts[(blob_key, "inventory-version-001")]
        blob = build_physical_wal_blob_frontier_manifest(
            source_site="webapp_ir",
            destination_site="webapp_fi",
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            writer_epoch=term.writer_epoch,
            writer_lease_id=term.writer_lease_id,
            witnessed_term_proof_sha256=term.proof_sha256,
            baseline_generation_id="wa-fi-failback-generation-20260731",
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
                        version="inventory-version-001",
                        payload=ciphertexts[(blob_key, "inventory-version-001")],
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
            expected_source_site="webapp_ir",
            expected_destination_site="webapp_fi",
            expected_campaign_id=CAMPAIGN,
            expected_release_sha=RELEASE,
            expected_writer_epoch=term.writer_epoch,
            expected_writer_lease_id=term.writer_lease_id,
            expected_witnessed_term_proof_sha256=term.proof_sha256,
            expected_baseline_generation_id="wa-fi-failback-generation-20260731",
            expected_wal_segment_size_bytes=WAL_BYTES,
            expected_destination_age_recipient=RECIPIENT,
        )
        baseline = bundle.baseline
        pin = build_physical_wal_receiver_staging_pin(
            source_site="webapp_ir",
            destination_site="webapp_fi",
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

    def _locator(self, *, bundle=None, pin=None, ciphertexts=None):
        active_bundle = self.bundle if bundle is None else bundle
        active_pin = self.pin if pin is None else pin
        active_ciphertexts = self.ciphertexts if ciphertexts is None else ciphertexts
        objects = [active_bundle.baseline.base_backup_object]
        objects.extend(segment.object for manifest in active_bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in active_bundle.blob_frontier.inventory_shards)
        expectations = tuple(
            ArvanExactVersionPullExpectation(
                object_key=item.object_key,
                version_id=item.version_id,
                ciphertext_sha256=item.ciphertext_sha256,
                ciphertext_bytes=item.ciphertext_bytes,
                metadata=self._metadata(
                    payload=active_ciphertexts[(item.object_key, item.version_id)],
                    ordinal=index,
                ),
            )
            for index, item in enumerate(objects, start=1)
        )
        return runtime.PhysicalWaFiPostgresFailbackExactObjectLocator(
            issued_at=NOW,
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256=active_pin.route_binding_sha256,
            manifest_sha256es=active_bundle.manifest_sha256es,
            object_expectations=expectations,
        )

    def _raw(self, *, bundle=None, locator=None, ciphertexts=None):
        active_bundle = self.bundle if bundle is None else bundle
        active_locator = self.locator if locator is None else locator
        active_ciphertexts = self.ciphertexts if ciphertexts is None else ciphertexts
        objects = [active_bundle.baseline.base_backup_object]
        objects.extend(segment.object for manifest in active_bundle.wal_manifests for segment in manifest.segments)
        objects.extend(shard.object for shard in active_bundle.blob_frontier.inventory_shards)
        return _RawExactGetClient(
            {
                (item.object_key, item.version_id): (
                    active_ciphertexts[(item.object_key, item.version_id)],
                    dict(active_locator.object_expectations[index].metadata),
                )
                for index, item in enumerate(objects)
            }
        )

    def _config(self, factory: _Factory, locator=None, **changes: object):
        active_locator = self.locator if locator is None else locator
        fields: dict[str, object] = {
            "receiver_factory": factory,
            "preflight_config": self.preflight_config,
            "preflight": self.preflight,
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
            "expected_locator_sha256": runtime.derive_wa_fi_postgres_failback_exact_object_locator_sha256(
                active_locator
            ),
            "maximum_ciphertext_bytes": 1024 * 1024,
            "enabled": True,
        }
        fields.update(changes)
        return runtime.RootOwnedWaFiPostgresFailbackPullRuntimeConfig(**fields)

    def _stage(self, *, config=None, locator=None, bundle=None, pin=None, term=None, raw=None, decryptor=None):
        active_bundle = self.bundle if bundle is None else bundle
        active_pin = self.pin if pin is None else pin
        active_term = self.term if term is None else term
        active_locator = self.locator if locator is None else locator
        active_raw = self._raw(bundle=active_bundle, locator=active_locator) if raw is None else raw
        active_factory = _Factory(configuration=self.preflight_config, raw=active_raw)
        active_config = self._config(active_factory, active_locator) if config is None else config
        result = runtime.stage_root_owned_wa_fi_postgres_failback_bundle(
            config=active_config,
            bundle=active_bundle,
            receiver_pin=active_pin,
            locator=active_locator,
            current_witnessed_term=active_term,
            now=NOW,
            age_decryptor_factory=lambda _config: decryptor or _FdDecryptor(self.plaintexts),
        )
        return result, active_factory, active_raw

    def test_exact_ir_to_fi_pull_stages_only_non_authorizing_fi_inputs(self) -> None:
        result, factory, raw = self._stage()

        self.assertTrue(result.staged)
        self.assertEqual(runtime.PHYSICAL_WA_FI_POSTGRES_FAILBACK_PULL_STATUS_STAGED, result.status)
        self.assertEqual((), result.reason_codes)
        self.assertEqual(1, factory.admits)
        self.assertEqual(2, factory.requires)
        self.assertEqual(1, factory.executes)
        self.assertEqual(3, len(raw.calls))
        self.assertTrue(all(call["Key"].startswith("physical-failback/") for call in raw.calls))
        self.assertTrue(all("VersionId" in call and call["VersionId"] != "latest" for call in raw.calls))
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.full_matrix_authorized)
        self.assertIsNotNone(result.recovery_preflight_binding)
        self.assertIsNotNone(result.failback_stage_evidence)
        self.assertIsNotNone(result.redacted_receipt)
        receipt = result.redacted_receipt
        assert receipt is not None
        rendered = receipt.raw_receipt.decode("ascii")
        for forbidden in (ENDPOINT, BUCKET, "physical-failback/", str(self.receiver_root)):
            self.assertNotIn(forbidden, rendered)
        parsed = json.loads(rendered)
        self.assertFalse(parsed["failback_materialization_authorized"])
        self.assertFalse(parsed["promotion_authorized"])
        self.assertFalse(parsed["full_matrix_authorized"])
        self.assertEqual(
            "webapp_fi",
            result.recovery_preflight_binding.local_standby_site,
        )
        self.assertEqual(
            0o400,
            os.stat(self.receipt_root / "receipts" / f"{receipt.bundle_id}.json").st_mode & 0o777,
        )

    def test_disabled_normal_namespace_or_mutable_selector_fails_before_factory_executes(self) -> None:
        raw = self._raw()
        factory = _Factory(configuration=self.preflight_config, raw=raw)
        disabled = self._config(factory, enabled=False)
        result, _unused, _unused_raw = self._stage(config=disabled, raw=raw)
        self.assertEqual(("WA_FI_FAILBACK_PULL_DISABLED",), result.reason_codes)
        self.assertEqual(0, factory.admits)
        self.assertEqual(0, factory.executes)

        normal_bundle, normal_pin, normal_term, normal_ciphertexts, _normal_plaintexts = self._route(
            namespace="physical-wal"
        )
        normal_locator = self._locator(
            bundle=normal_bundle,
            pin=normal_pin,
            ciphertexts=normal_ciphertexts,
        )
        raw = self._raw(
            bundle=normal_bundle,
            locator=normal_locator,
            ciphertexts=normal_ciphertexts,
        )
        factory = _Factory(configuration=self.preflight_config, raw=raw)
        config = self._config(factory, normal_locator)
        result, _unused, _unused_raw = self._stage(
            config=config,
            locator=normal_locator,
            bundle=normal_bundle,
            pin=normal_pin,
            term=normal_term,
            raw=raw,
        )
        self.assertEqual(("WA_FI_FAILBACK_PULL_OBJECT_NAMESPACE_INVALID",), result.reason_codes)
        self.assertEqual(0, factory.admits)
        self.assertEqual(0, factory.executes)

        mutable_expectations = list(self.locator.object_expectations)
        mutable_expectations[0] = replace(mutable_expectations[0], version_id="latest")
        mutable_locator = replace(self.locator, object_expectations=tuple(mutable_expectations))
        raw = self._raw()
        factory = _Factory(configuration=self.preflight_config, raw=raw)
        config = self._config(factory, mutable_locator)
        result, _unused, _unused_raw = self._stage(
            config=config,
            locator=mutable_locator,
            raw=raw,
        )
        self.assertEqual(("WA_FI_FAILBACK_PULL_LOCATOR_OBJECTS_MISMATCH",), result.reason_codes)
        self.assertEqual(0, factory.admits)
        self.assertEqual(0, factory.executes)

    def test_receiver_admission_is_opaque_and_cannot_be_substituted(self) -> None:
        admission = runtime.build_physical_wa_fi_failback_exact_version_receiver_admission(
            preflight=self.preflight,
            preflight_config=self.preflight_config,
            current_witnessed_term=self.term,
            now=NOW,
        )
        with self.assertRaises(TypeError):
            pickle.dumps(admission)
        required = runtime.require_physical_wa_fi_failback_exact_version_receiver_admission(
            admission,
            preflight=self.preflight,
            preflight_config=self.preflight_config,
            current_witnessed_term=self.term,
            now=NOW + timedelta(seconds=10),
        )
        self.assertIs(admission, required)
        forged = replace(admission, fi_receiver_identity_sha256="0" * 64)
        with self.assertRaisesRegex(runtime.PhysicalWaFiPostgresFailbackPullRuntimeError, "ADMISSION_FAILED"):
            runtime.require_physical_wa_fi_failback_exact_version_receiver_admission(
                forged,
                preflight=self.preflight,
                preflight_config=self.preflight_config,
                current_witnessed_term=self.term,
                now=NOW,
            )

    def test_factory_must_return_the_one_active_callback_stage_result(self) -> None:
        """A factory cannot skip, forge, retain, or replay the callback."""

        raw = self._raw()

        class SkippingFactory(_Factory):
            retained_operation = None

            def execute_fi_receiver_failback_exact_pull(self, *, admission, now, operation):
                del admission, now
                self.executes += 1
                self.retained_operation = operation
                return PhysicalWalReceiverStagingResult(
                    status="staged",
                    reason_codes=(),
                )

        factory = SkippingFactory(configuration=self.preflight_config, raw=raw)
        config = self._config(factory)
        result, _unused, _unused_raw = self._stage(config=config, raw=raw)
        self.assertEqual(("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID",), result.reason_codes)
        self.assertEqual([], raw.calls)
        assert factory.retained_operation is not None
        with self.assertRaisesRegex(runtime.PhysicalWaFiPostgresFailbackPullRuntimeError, "CALLBACK_INVALID"):
            factory.retained_operation(raw, factory.route)
        self.assertEqual([], raw.calls)

    def test_factory_double_callback_is_rejected_even_if_it_returns_the_first_result(self) -> None:
        raw = self._raw()

        class DoubleCallFactory(_Factory):
            def execute_fi_receiver_failback_exact_pull(self, *, admission, now, operation):
                del admission, now
                self.executes += 1
                first = operation(self.raw, self.route)
                try:
                    operation(self.raw, self.route)
                except runtime.PhysicalWaFiPostgresFailbackPullRuntimeError:
                    pass
                return first

        factory = DoubleCallFactory(configuration=self.preflight_config, raw=raw)
        config = self._config(factory)
        result, _unused, _unused_raw = self._stage(config=config, raw=raw)
        self.assertEqual(("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID",), result.reason_codes)
        self.assertEqual(3, len(raw.calls))

    def test_factory_cannot_invoke_the_bounded_callback_from_another_thread(self) -> None:
        raw = self._raw()

        class CrossThreadFactory(_Factory):
            callback_error = None

            def execute_fi_receiver_failback_exact_pull(self, *, admission, now, operation):
                del admission, now
                self.executes += 1

                def invoke() -> None:
                    try:
                        operation(self.raw, self.route)
                    except BaseException as exc:  # record the fixed boundary error only.
                        self.callback_error = exc

                worker = threading.Thread(target=invoke)
                worker.start()
                worker.join()
                return PhysicalWalReceiverStagingResult(status="staged", reason_codes=())

        factory = CrossThreadFactory(configuration=self.preflight_config, raw=raw)
        result, _unused, _unused_raw = self._stage(config=self._config(factory), raw=raw)
        self.assertEqual(("WA_FI_FAILBACK_PULL_FACTORY_CALLBACK_INVALID",), result.reason_codes)
        self.assertIsInstance(factory.callback_error, runtime.PhysicalWaFiPostgresFailbackPullRuntimeError)
        self.assertEqual([], raw.calls)

    def test_runtime_has_no_normal_pull_or_normal_credential_factory_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "core/physical_wa_fi_postgres_failback_pull_runtime.py"
        ).read_text(encoding="utf-8")
        forbidden = (
            "physical_wa_ir_postgres_recovery_pull_runtime",
            "physical_arvan_s3_separated_credential_loader",
            "physical_arvan_s3_separated_client_factory",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "import docker",
            "from docker",
            "import psycopg",
            "from psycopg",
        )
        self.assertFalse([item for item in forbidden if item in source])


if __name__ == "__main__":
    unittest.main()
