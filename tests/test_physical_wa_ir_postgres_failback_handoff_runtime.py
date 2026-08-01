"""Injected tests for the separate IR-publisher reverse handoff boundary.

These tests use only temporary local files and in-memory doubles.  They do
not open a network connection, load a credential file, run age, Docker, SSH,
or PostgreSQL.
"""

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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core import physical_wa_ir_postgres_failback_handoff_runtime as runtime
from core.object_delta_role_matrix_rollover import (
    build_object_delta_role_matrix_witnessed_term_proof,
    verify_object_delta_role_matrix_witnessed_term,
)
from core.physical_age_v1_adapter import PhysicalAgeV1EncryptorConfig
from core.physical_wal_base_backup_spool import PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
RELEASE_SHA = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
RECIPIENT = "age1" + "a" * 30


def sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii")
    return hashlib.sha256(value).hexdigest()


def hex_sha(character: str) -> str:
    return character * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def witnessed_term(*, now: datetime = NOW):
    signer = Ed25519PrivateKey.generate()
    proof = build_object_delta_role_matrix_witnessed_term_proof(
        holder_site="webapp_ir",
        writer_epoch=71,
        writer_lease_id="ir-writer-lease-71",
        witness_transition_id="ir-witness-transition-71",
        issued_at=now - timedelta(seconds=10),
        expires_at=now + timedelta(seconds=100),
        witness_signer=signer,
    )
    public_key = signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return verify_object_delta_role_matrix_witnessed_term(
        proof,
        witness_public_key=public_key,
        maximum_lease_duration_seconds=120,
        safety_margin_seconds=5,
        now=now,
    )


def reverse_binding(**changes: object) -> preflight.PhysicalIrToFiObjectStorageFailbackBinding:
    fixture = make_four_role_fixture(
        campaign_id="ir-fi-failback-20260731",
        release_sha=RELEASE_SHA,
        fi_publisher_identity_sha256=hex_sha("a"),
        ir_receiver_identity_sha256=hex_sha("b"),
        ir_publisher_identity_sha256=hex_sha("c"),
        fi_receiver_identity_sha256=hex_sha("d"),
    )
    return replace(fixture.binding, **changes)


def reverse_preflight(
    binding: preflight.PhysicalIrToFiObjectStorageFailbackBinding,
    *,
    fixture=None,
    live_iam=None,
):
    fixture = fixture or make_four_role_fixture(
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        fi_publisher_identity_sha256=binding.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=binding.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=binding.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=binding.fi_receiver_identity_sha256,
    )
    live_iam = live_iam or make_four_role_live_iam_durable_admission_fixture(
        binding=binding,
        observed_at=NOW,
    )
    observation = preflight.build_physical_ir_to_fi_object_storage_failback_observation(
        binding=binding,
        four_role_projection_binding=fixture.verified_binding,
        four_role_live_iam_binding=live_iam.live_iam_binding,
        four_role_live_iam_durable_admission=live_iam.live_iam_durable_admission,
        observed_at=NOW,
    )
    return preflight.verify_physical_ir_to_fi_object_storage_failback_preflight(
        observation,
        binding=binding,
        four_role_projection_binding=fixture.verified_binding,
        four_role_live_iam_binding=live_iam.live_iam_binding,
        four_role_live_iam_durable_admission=live_iam.live_iam_durable_admission,
        now=NOW,
    )


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._payload) - self._offset
        result = self._payload[self._offset : self._offset + amount]
        self._offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class _MemoryCreateOnlyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.record: dict[str, object] | None = None

    def get_bucket_versioning(self, *, Bucket: str):
        self.calls.append("versioning")
        return {"Status": "Enabled"}

    def get_bucket_acl(self, *, Bucket: str):
        self.calls.append("acl")
        return {
            "Owner": {"ID": "owner"},
            "Grants": [
                {"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}
            ],
        }

    def list_object_versions(self, **request: object):
        self.calls.append("versions")
        key = request["Prefix"]
        if self.record is None:
            versions: list[dict[str, object]] = []
        else:
            versions = [{"Key": key, "VersionId": self.record["version_id"], "IsLatest": True}]
        return {"Versions": versions, "DeleteMarkers": [], "IsTruncated": False}

    def put_object(self, **request: object):
        self.calls.append("put")
        if self.record is not None:
            raise AssertionError("duplicate synthetic put")
        body = request["Body"]
        self.record = {
            "key": request["Key"],
            "version_id": "failback-version-20260731-01",
            "payload": body.read(),
            "metadata": dict(request["Metadata"]),
        }
        return {"VersionId": self.record["version_id"]}

    def head_object(self, **request: object):
        self.calls.append("head")
        assert self.record is not None
        return {
            "VersionId": self.record["version_id"],
            "ContentLength": len(self.record["payload"]),
            "Metadata": self.record["metadata"],
        }

    def get_object(self, **request: object):
        self.calls.append("get")
        assert self.record is not None
        return {
            "VersionId": self.record["version_id"],
            "Metadata": self.record["metadata"],
            "Body": _Body(self.record["payload"]),
        }


class _FakeAgeEncryptor:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def encrypt(self, *, recipient: str, plaintext_path: Path, ciphertext_path: Path) -> None:
        self.calls.append(plaintext_path)
        ciphertext_path.write_bytes(b"age-encryption.org/v1\n" + hashlib.sha256(plaintext_path.read_bytes()).digest())
        os.chmod(ciphertext_path, 0o600)


class _ReversePublisherFactory:
    def __init__(
        self,
        *,
        config: preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig,
        client: _MemoryCreateOnlyClient,
    ) -> None:
        self._config = config
        self._client = client
        self.calls: list[str] = []

    def admit_ir_publisher_failback_handoff(self, *, preflight, current_witnessed_term, now):
        self.calls.append("admit")
        return runtime.build_physical_wa_ir_failback_object_storage_publisher_admission(
            preflight=preflight,
            preflight_config=self._config,
            current_witnessed_term=current_witnessed_term,
            now=now,
        )

    def require_ir_publisher_failback_handoff_admission(
        self, admission, *, preflight, current_witnessed_term, now
    ):
        self.calls.append("require")
        return runtime.require_physical_wa_ir_failback_object_storage_publisher_admission(
            admission,
            preflight=preflight,
            preflight_config=self._config,
            current_witnessed_term=current_witnessed_term,
            now=now,
        )

    def execute_ir_publisher_failback_handoff(self, *, admission, now, operation):
        self.calls.append("execute")
        return operation(
            self._client,
            runtime.PhysicalWaIrFailbackObjectStoragePublisherRoute(
                bucket="private-failback-recovery",
                region="ir-thr-at1",
            ),
        )


class _ShouldNotRunFactory:
    def admit_ir_publisher_failback_handoff(self, **kwargs):
        del kwargs
        raise AssertionError("publisher factory must not be reached")

    def require_ir_publisher_failback_handoff_admission(self, admission, **kwargs):
        del admission, kwargs
        raise AssertionError("publisher factory must not be reached")

    def execute_ir_publisher_failback_handoff(self, **kwargs):
        del kwargs
        raise AssertionError("publisher factory must not be reached")


@unittest.skipUnless(os.geteuid() == 0, "runtime contract explicitly requires root")
class PhysicalWaIrPostgresFailbackHandoffRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="wa-ir-failback-handoff-")
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.spool = self.root / "spool"
        self.workspace.mkdir(mode=0o700)
        self.spool.mkdir(mode=0o700)
        os.chmod(self.workspace, 0o700)
        os.chmod(self.spool, 0o700)
        self.plaintext = b"reverse-base-backup" * 32
        self.plaintext_sha = sha(self.plaintext)
        snapshot_dir = self.spool / "snapshots" / self.plaintext_sha[:2]
        snapshot_dir.mkdir(parents=True, mode=0o700)
        os.chmod(snapshot_dir, 0o700)
        self.snapshot = snapshot_dir / f"{self.plaintext_sha}.basebackup"
        self.snapshot.write_bytes(self.plaintext)
        os.chmod(self.snapshot, 0o600)
        self.term = witnessed_term()
        self.binding = reverse_binding()
        self.four_role_fixture = make_four_role_fixture(
            campaign_id=self.binding.campaign_id,
            release_sha=self.binding.release_sha,
            fi_publisher_identity_sha256=self.binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.binding.fi_receiver_identity_sha256,
        )
        self.live_iam = make_four_role_live_iam_durable_admission_fixture(
            binding=self.binding,
            observed_at=NOW,
        )
        self.preflight = reverse_preflight(
            self.binding,
            fixture=self.four_role_fixture,
            live_iam=self.live_iam,
        )
        self.preflight_config = self.four_role_fixture.preflight_config(
            four_role_live_iam_binding=self.live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=self.live_iam.live_iam_durable_admission,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def descriptor(
        self,
        *,
        namespace: str = "physical-failback",
        lineage_route_binding_sha256: str = hex_sha("7"),
    ) -> bytes:
        object_key = "/".join(
            (
                namespace,
                self.binding.campaign_id,
                self.binding.release_sha,
                "ir-fi-baseline-20260731",
                "webapp_ir-to-webapp_fi",
                "timeline-00000001",
                "base-backup",
                f"{self.plaintext_sha}.age",
            )
        )
        return canonical(
            {
                "schema": PHYSICAL_WAL_BASE_BACKUP_SPOOL_DESCRIPTOR_SCHEMA,
                "kind": "physical_postgresql_base_backup_handoff",
                "source_site": "webapp_ir",
                "destination_site": "webapp_fi",
                "campaign_id": self.binding.campaign_id,
                "release_sha": self.binding.release_sha,
                "baseline_generation_id": "ir-fi-baseline-20260731",
                # This is the generic source-spool lineage hash, not the
                # independent provider/identity route hash from preflight.
                "route_binding_sha256": lineage_route_binding_sha256,
                "object_storage_namespace": namespace,
                "database_system_identifier": "7392847193847192834",
                "timeline_id": 1,
                "wal_segment_size_bytes": 16 * 1024 * 1024,
                "baseline_wal_lsn": "0/1800000",
                "wal_chain_start_lsn": "0/1000000",
                "base_backup_end_lsn": "0/2800000",
                "destination_age_recipient": RECIPIENT,
                "writer_term": {
                    "holder_site": "webapp_ir",
                    "epoch": self.term.writer_epoch,
                    "lease_id": self.term.writer_lease_id,
                    "witness_transition_id": self.term.witness_transition_id,
                    "witnessed_term_proof_sha256": self.term.proof_sha256,
                },
                "completed_source_artifact": {
                    "artifact_name": "ir-base-backup-20260731.tar",
                    "plaintext_sha256": self.plaintext_sha,
                    "plaintext_bytes": len(self.plaintext),
                    "completion_attestation_sha256": hex_sha("3"),
                },
                "snapshot_path_name": self.snapshot.name,
                "snapshot_sha256": self.plaintext_sha,
                "snapshot_bytes": len(self.plaintext),
                "object_key": object_key,
                "not_a_remote_apply_proof": True,
                "not_a_strict_acknowledgement_proof": True,
            }
        )

    def config(self, publisher_factory) -> runtime.RootOwnedWaIrPostgresFailbackHandoffConfig:
        policy = runtime.RootOwnedWaIrPostgresFailbackUploaderPolicy(
            workspace=self.workspace,
            spool_root=self.spool,
            destination_age_recipient=RECIPIENT,
            maximum_plaintext_bytes=len(self.plaintext),
        )
        return runtime.RootOwnedWaIrPostgresFailbackHandoffConfig(
            publisher_factory=publisher_factory,
            preflight_config=self.preflight_config,
            preflight=self.preflight,
            age_encryptor_config=PhysicalAgeV1EncryptorConfig(
                workspace_root=self.workspace,
                recipient=RECIPIENT,
                enabled=True,
                maximum_plaintext_bytes=len(self.plaintext),
                maximum_ciphertext_bytes=len(self.plaintext) + 1024,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            ),
            base_backup_policy=policy,
            enabled=True,
        )

    def test_base_backup_uses_only_reverse_admission_and_failback_namespace(self) -> None:
        client = _MemoryCreateOnlyClient()
        publisher_factory = _ReversePublisherFactory(
            config=self.preflight_config,
            client=client,
        )
        encryptor = _FakeAgeEncryptor()
        handoff = runtime.RootOwnedWaIrPostgresFailbackHandoff(
            self.config(publisher_factory),
            clock=lambda: NOW,
            age_encryptor_factory=lambda: encryptor,
        )
        raw = self.descriptor()
        self.assertNotEqual(hex_sha("7"), self.binding.route_binding_sha256)

        receipt = handoff.base_backup_uploader(current_witnessed_term=self.term).upload(
            snapshot_path=self.snapshot,
            descriptor_bytes=raw,
            descriptor_sha256=sha(raw),
        )

        self.assertTrue(receipt.object_key.startswith("physical-failback/"))
        self.assertEqual(["admit", "require", "execute", "require"], publisher_factory.calls)
        self.assertEqual(["versioning", "acl", "versions", "put", "versions", "head", "get"], client.calls)
        self.assertEqual(1, len(encryptor.calls))
        self.assertNotIn("credential", repr(receipt).lower())

    def test_normal_namespace_descriptor_is_rejected_before_factory_or_object_store(self) -> None:
        handoff = runtime.RootOwnedWaIrPostgresFailbackHandoff(
            self.config(_ShouldNotRunFactory()),
            clock=lambda: NOW,
            age_encryptor_factory=lambda: _FakeAgeEncryptor(),
        )
        raw = self.descriptor(namespace="physical-wal")

        with self.assertRaisesRegex(
            runtime.PhysicalWaIrPostgresFailbackHandoffError,
            "DESCRIPTOR_BINDING_MISMATCH",
        ):
            handoff.base_backup_uploader(current_witnessed_term=self.term).upload(
                snapshot_path=self.snapshot,
                descriptor_bytes=raw,
                descriptor_sha256=sha(raw),
            )

    def test_factory_cannot_substitute_an_untyped_or_wrong_term_admission(self) -> None:
        client = _MemoryCreateOnlyClient()

        class WrongAdmissionFactory(_ReversePublisherFactory):
            def admit_ir_publisher_failback_handoff(self, **kwargs):
                del kwargs
                return object()

        handoff = runtime.RootOwnedWaIrPostgresFailbackHandoff(
            self.config(WrongAdmissionFactory(config=self.preflight_config, client=client)),
            clock=lambda: NOW,
            age_encryptor_factory=lambda: _FakeAgeEncryptor(),
        )
        raw = self.descriptor()
        with self.assertRaisesRegex(
            runtime.PhysicalWaIrPostgresFailbackHandoffError,
            "PUBLISHER_ADMISSION_FAILED",
        ):
            handoff.base_backup_uploader(current_witnessed_term=self.term).upload(
                snapshot_path=self.snapshot,
                descriptor_bytes=raw,
                descriptor_sha256=sha(raw),
            )
        self.assertEqual([], client.calls)

    def test_factory_must_return_the_one_synchronous_callback_receipt(self) -> None:
        """A factory cannot synthesize a valid-shaped receipt or retain the callback."""

        client = _MemoryCreateOnlyClient()
        raw = self.descriptor()
        descriptor = json.loads(raw.decode("ascii"))
        forged = runtime.PhysicalWalBaseBackupUploadReceipt(
            descriptor_sha256=sha(raw),
            object_key=descriptor["object_key"],
            version_id="forged-failback-version-01",
            ciphertext_sha256=hex_sha("4"),
            ciphertext_bytes=64,
            encryption="age-v1",
            age_recipient=RECIPIENT,
            immutability="versioned_create_only_readback_v1",
        )

        class SkippingFactory(_ReversePublisherFactory):
            retained_operation = None

            def execute_ir_publisher_failback_handoff(self, *, admission, now, operation):
                del admission, now
                self.calls.append("execute")
                self.retained_operation = operation
                return forged

        publisher_factory = SkippingFactory(config=self.preflight_config, client=client)
        handoff = runtime.RootOwnedWaIrPostgresFailbackHandoff(
            self.config(publisher_factory),
            clock=lambda: NOW,
            age_encryptor_factory=lambda: _FakeAgeEncryptor(),
        )

        with self.assertRaisesRegex(
            runtime.PhysicalWaIrPostgresFailbackHandoffError,
            "FACTORY_CALLBACK_INVALID",
        ):
            handoff.base_backup_uploader(current_witnessed_term=self.term).upload(
                snapshot_path=self.snapshot,
                descriptor_bytes=raw,
                descriptor_sha256=sha(raw),
            )
        self.assertEqual([], client.calls)

        self.assertIsNotNone(publisher_factory.retained_operation)
        with self.assertRaisesRegex(
            runtime.PhysicalWaIrPostgresFailbackHandoffError,
            "FACTORY_CALLBACK_INVALID",
        ):
            publisher_factory.retained_operation(
                client,
                runtime.PhysicalWaIrFailbackObjectStoragePublisherRoute(
                    bucket="private-failback-recovery",
                    region="ir-thr-at1",
                ),
            )
        self.assertEqual([], client.calls)

    def test_source_does_not_import_the_normal_route_or_credential_factory(self) -> None:
        source = Path(runtime.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        forbidden = {
            "core.physical_arvan_s3_separated_client_factory",
            "core.physical_arvan_s3_separated_credential_loader",
            "core.physical_wa_fi_postgres_object_storage_handoff_runtime",
            "core.physical_wa_ir_postgres_recovery_pull_runtime",
        }
        self.assertFalse(imported & forbidden)


if __name__ == "__main__":
    unittest.main()
