from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import inspect
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_contract as roundtrip
from core import physical_wal_v2_witness_roundtrip_delivery_contract as delivery
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as adapter
from tests import test_physical_wal_v2_witness_roundtrip_contract as roundtrip_contract_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _RawS3:
    """In-memory raw callback double; it contains no provider client."""

    def __init__(self, *, mailbox: str, packet: bytes, proof_sha256: str) -> None:
        self.mailbox = mailbox
        self.packet = packet
        self.proof_sha256 = proof_sha256
        self.object_key = adapter._object_key(mailbox, hashlib.sha256(packet).hexdigest())
        self.object_version_id = "version-000001"
        self.retained_until = NOW + timedelta(seconds=120)
        self.calls: list[str] = []
        self.bad_create = False
        self.bad_head = False
        self.bad_read = False
        self.bad_list = False

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.packet).hexdigest()

    def _put(self, **kwargs: object) -> adapter.PhysicalWalV2WitnessRoundtripS3ObjectVersion:
        self.calls.append("put")
        self.packet = kwargs["canonical_delivery"]  # type: ignore[assignment]
        self.object_key = kwargs["object_key"]  # type: ignore[assignment]
        self.retained_until = kwargs["retained_until"]  # type: ignore[assignment]
        self.proof_sha256 = kwargs["retention_proof_sha256"]  # type: ignore[assignment]
        return adapter.PhysicalWalV2WitnessRoundtripS3ObjectVersion(
            object_key=self.object_key,
            object_version_id=self.object_version_id,
            content_sha256=self.digest,
            content_bytes=len(self.packet),
            retained_until=self.retained_until,
            conditional_create_only=not self.bad_create,
            object_lock_compliance=True,
            retention_proof_sha256=self.proof_sha256,
        )

    def _head(self, **kwargs: object) -> adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead:
        self.calls.append("head")
        return adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectHead(
            object_key=kwargs["object_key"],  # type: ignore[arg-type]
            object_version_id=(
                "foreign-version-000001" if self.bad_head else kwargs["object_version_id"]
            ),  # type: ignore[arg-type]
            content_sha256=self.digest,
            content_bytes=len(self.packet),
            retained_until=self.retained_until,
            object_lock_compliance=True,
            retention_proof_sha256=self.proof_sha256,
        )

    def _get(self, **kwargs: object) -> adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead:
        self.calls.append("get")
        body = b"{}" if self.bad_read else self.packet
        return adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectRead(
            object_key=kwargs["object_key"],  # type: ignore[arg-type]
            object_version_id=kwargs["object_version_id"],  # type: ignore[arg-type]
            content_sha256=self.digest,
            content_bytes=len(self.packet),
            retained_until=self.retained_until,
            object_lock_compliance=True,
            retention_proof_sha256=self.proof_sha256,
            canonical_delivery=body,
        )

    def _list(self) -> tuple[adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator, ...]:
        self.calls.append("list")
        key = self.object_key
        if self.bad_list:
            key = "physical-wal-v2-witness-roundtrip-delivery-v1/foreign/" + self.digest + ".json"
        return (
            adapter.PhysicalWalV2WitnessRoundtripS3ImmutableObjectLocator(
                object_key=key,
                object_version_id=self.object_version_id,
                content_sha256=self.digest,
                content_bytes=len(self.packet),
                retained_until=self.retained_until,
                object_lock_compliance=True,
                retention_proof_sha256=self.proof_sha256,
            ),
        )

    def put_fi_to_witness_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_fi_to_witness_exact(self, **kwargs: object):
        return self._head(**kwargs)

    def get_fi_to_witness_exact(self, **kwargs: object):
        return self._get(**kwargs)

    def put_witness_to_ir_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_witness_to_ir_exact(self, **kwargs: object):
        return self._head(**kwargs)

    def get_witness_to_ir_exact(self, **kwargs: object):
        return self._get(**kwargs)

    def put_ir_to_witness_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_ir_to_witness_exact(self, **kwargs: object):
        return self._head(**kwargs)

    def get_ir_to_witness_exact(self, **kwargs: object):
        return self._get(**kwargs)

    def put_witness_to_fi_create_only(self, **kwargs: object):
        return self._put(**kwargs)

    def head_witness_to_fi_exact(self, **kwargs: object):
        return self._head(**kwargs)

    def get_witness_to_fi_exact(self, **kwargs: object):
        return self._get(**kwargs)

    def list_fi_to_witness_immutable_locators(self):
        return self._list()

    def list_witness_to_ir_immutable_locators(self):
        return self._list()

    def list_ir_to_witness_immutable_locators(self):
        return self._list()

    def list_witness_to_fi_immutable_locators(self):
        return self._list()


class _Scope:
    def __init__(self, raw_s3: _RawS3) -> None:
        self.raw_s3 = raw_s3
        self.calls: list[tuple[str, str]] = []

    def _with(self, name: str, *, credentials, operation):
        self.calls.append((name, credentials.local_role))
        return operation(self.raw_s3)

    def with_fi_to_witness_publisher_s3(self, **kwargs):
        return self._with("fi-publish", **kwargs)

    def with_witness_to_ir_egress_s3(self, **kwargs):
        return self._with("witness-ir-publish", **kwargs)

    def with_ir_to_witness_publisher_s3(self, **kwargs):
        return self._with("ir-publish", **kwargs)

    def with_witness_to_fi_publisher_s3(self, **kwargs):
        return self._with("witness-fi-publish", **kwargs)

    def with_fi_to_witness_ingress_s3(self, **kwargs):
        return self._with("witness-fi-list-read", **kwargs)

    def with_witness_to_ir_ingress_s3(self, **kwargs):
        return self._with("ir-standby-list-read", **kwargs)

    def with_ir_to_witness_ingress_s3(self, **kwargs):
        return self._with("witness-ir-list-read", **kwargs)

    def with_witness_to_fi_ingress_s3(self, **kwargs):
        return self._with("fi-ack-list-read", **kwargs)


class PhysicalWalV2WitnessRoundtripS3MailboxAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = Ed25519PrivateKey.generate()
        self.fixture = roundtrip_contract_tests.PhysicalWalV2WitnessRoundtripContractTests(
            "runTest"
        )
        self.fixture.setUp()
        recovery_export = self.fixture._recovery_export()
        certificate = self.fixture._certificate(recovery_export)
        envelope = self.fixture._source_envelope(certificate)
        assertion, _issued = self.fixture._assertion(envelope)
        attestation = self.fixture._attestation(assertion)
        self.certificate = roundtrip.verify_physical_wal_v2_witness_context_certificate(
            certificate,
            config=self.fixture.config,
            now=NOW,
        ).canonical_certificate
        self.envelope = roundtrip.verify_physical_wal_v2_witness_source_envelope(
            envelope,
            config=self.fixture.config,
            now=NOW,
        ).canonical_envelope
        self.assertion = roundtrip.verify_physical_wal_v2_witness_ir_durable_assertion(
            assertion,
            config=self.fixture.config,
            now=NOW,
        ).canonical_assertion
        self.attestation = roundtrip.verify_physical_wal_v2_witness_roundtrip_attestation(
            attestation,
            config=self.fixture.config,
            now=NOW,
        ).canonical_attestation
        self.binding = delivery.build_physical_wal_v2_witness_roundtrip_delivery_binding(
            context_certificate=self.certificate,
            roundtrip_config=self.fixture.config,
            now=NOW,
        )
        self.deliveries = {
            "fi-to-witness": delivery.build_physical_wal_v2_witness_fi_to_witness_delivery(
                context_certificate=self.certificate,
                source_envelope=self.envelope,
                config=self._delivery_policy("fi-to-witness"),
                now=NOW,
            ),
            "witness-to-ir": delivery.build_physical_wal_v2_witness_witness_to_ir_delivery(
                context_certificate=self.certificate,
                source_envelope=self.envelope,
                config=self._delivery_policy("witness-to-ir"),
                now=NOW,
            ),
            "ir-to-witness": delivery.build_physical_wal_v2_witness_ir_to_witness_delivery(
                ir_durable_assertion=self.assertion,
                config=self._delivery_policy("ir-to-witness"),
                now=NOW,
            ),
            "witness-to-fi": delivery.build_physical_wal_v2_witness_witness_to_fi_delivery(
                roundtrip_attestation=self.attestation,
                config=self._delivery_policy("witness-to-fi"),
                now=NOW,
            ),
        }

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _delivery_policy(self, mailbox: str) -> delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig:
        return delivery.PhysicalWalV2WitnessRoundtripDeliveryConfig(
            roundtrip_config=self.fixture.config,
            binding=self.binding,
            receiver_mailbox=mailbox,
            enabled=True,
        )

    def _host_assertion(
        self,
        *,
        config: admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
        policy: admission.PhysicalWalV2WitnessRoundtripMailboxPolicy,
        expires_at: datetime,
    ) -> bytes:
        unsigned = {
            "schema": "gold-trade-physical-wal-v2-witness-roundtrip-host-role-assertion-v1",
            "version": 1,
            "host_id": config.host_id,
            "local_role": policy.local_role,
            "mailbox": policy.mailbox,
            "direction": policy.direction,
            "object_prefix": policy.object_prefix,
            "least_privilege_actions": list(policy.least_privilege_actions),
            "policy_sha256": admission._policy_sha256(policy),
            "deployment_binding_sha256": config.deployment_binding_sha256,
            "delivery_binding_sha256": config.delivery_binding_sha256,
            "assertion_id": "s3-mailbox-host-role-000001",
            "assertion_nonce": "H" * 22,
            "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        signature = self.authority.sign(
            admission._HOST_ROLE_DOMAIN + canonical_json_bytes(unsigned)
        )
        return canonical_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )

    def _retention_proof(
        self,
        *,
        admission_config: admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
        granted: admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission,
        expires_at: datetime,
    ) -> adapter.VerifiedPhysicalWalV2WitnessRoundtripS3RetentionProof:
        unsigned = {
            "schema": adapter._RETENTION_SCHEMA,
            "version": 1,
            "host_id": granted.host_id,
            "local_role": granted.local_role,
            "mailbox": granted.mailbox,
            "direction": granted.direction,
            "object_prefix": granted.object_prefix,
            "policy_sha256": granted.policy_sha256,
            "deployment_binding_sha256": granted.deployment_binding_sha256,
            "delivery_binding_sha256": granted.delivery_binding_sha256,
            "host_role_assertion_sha256": granted.host_role_assertion_sha256,
            "retention_mode": adapter._RETENTION_MODE,
            "minimum_retention_seconds": 20,
            "evidence_id": "object-lock-evidence-000001",
            "evidence_nonce": "R" * 22,
            "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        signature = self.authority.sign(
            adapter._RETENTION_DOMAIN + canonical_json_bytes(unsigned)
        )
        wire = canonical_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )
        return adapter.verify_physical_wal_v2_witness_roundtrip_s3_retention_proof(
            wire,
            config=adapter.PhysicalWalV2WitnessRoundtripS3RetentionProofConfig(
                admission_config=admission_config,
                mailbox_admission=granted,
                enabled=True,
            ),
            now=NOW,
        )

    @staticmethod
    def _credential(root: Path, *, local_role: str) -> None:
        directory = root / adapter._CREDENTIAL_DIRECTORY
        os.mkdir(directory, 0o700)
        payload = canonical_json_bytes(
            {
                "schema": adapter._CREDENTIAL_SCHEMA,
                "version": adapter._CREDENTIAL_VERSION,
                "local_role": local_role,
                "access_key_id": "AKIA00000001",
                "secret_access_key": "s" * 32,
            }
        )
        descriptor = os.open(
            directory / (local_role + ".json"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise AssertionError("short credential fixture write")
        finally:
            os.close(descriptor)
        os.chmod(root, 0o700)
        os.chmod(directory, 0o700)

    def _bundle(
        self,
        policy: admission.PhysicalWalV2WitnessRoundtripMailboxPolicy,
        *,
        root: Path,
        admission_expires_at: datetime = NOW + timedelta(seconds=50),
        proof_expires_at: datetime = NOW + timedelta(seconds=40),
        write_credential: bool = True,
    ) -> adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig:
        delivery_config = self._delivery_policy(policy.mailbox)
        binding_sha = delivery._config(delivery_config, mailbox=policy.mailbox).binding_sha256
        admission_config = admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig(
            host_id="s3-mailbox-host-0001",
            local_role=policy.local_role,
            deployment_binding_sha256="a" * 64,
            delivery_binding_sha256=binding_sha,
            host_role_authority_public_key=_public(self.authority),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        granted = admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
            config=admission_config,
            host_role_assertion=self._host_assertion(
                config=admission_config,
                policy=policy,
                expires_at=admission_expires_at,
            ),
            now=NOW,
        )
        proof = self._retention_proof(
            admission_config=admission_config,
            granted=granted,
            expires_at=proof_expires_at,
        )
        if write_credential:
            self._credential(root, local_role=policy.local_role)
        return adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig(
            admission_config=admission_config,
            mailbox_admission=granted,
            delivery_config=delivery_config,
            retention_proof=proof,
            credential_root=root,
            enabled=True,
            maximum_delivery_bytes=2 * 1024 * 1024,
            maximum_list_entries=8,
        )

    @staticmethod
    def _open(policy, *, config, scope):
        opener = {
            "fi-writer-source-outbox": adapter.open_physical_wal_v2_witness_roundtrip_fi_to_witness_publisher_s3_adapter,
            "witness-fi-ingress": adapter.open_physical_wal_v2_witness_roundtrip_witness_fi_ingress_s3_adapter,
            "witness-ir-egress": adapter.open_physical_wal_v2_witness_roundtrip_witness_to_ir_publisher_s3_adapter,
            "ir-standby-ack-inbox": adapter.open_physical_wal_v2_witness_roundtrip_ir_standby_ingress_s3_adapter,
            "ir-durable-ack-outbox": adapter.open_physical_wal_v2_witness_roundtrip_ir_to_witness_publisher_s3_adapter,
            "witness-ir-ingress": adapter.open_physical_wal_v2_witness_roundtrip_witness_ir_ingress_s3_adapter,
            "witness-fi-egress": adapter.open_physical_wal_v2_witness_roundtrip_witness_to_fi_publisher_s3_adapter,
            "fi-writer-ack-inbox": adapter.open_physical_wal_v2_witness_roundtrip_fi_ack_ingress_s3_adapter,
        }[policy.local_role]
        return opener(config=config, scope=scope, now=NOW)

    def _invoke_publisher(self, value, *, mailbox: str, packet: bytes):
        digest = hashlib.sha256(packet).hexdigest()
        arguments = {
            "object_key": adapter._object_key(mailbox, digest),
            "canonical_delivery": packet,
            "content_sha256": digest,
            "content_bytes": len(packet),
            "retained_until": NOW + timedelta(seconds=30),
        }
        if mailbox == "fi-to-witness":
            return value.create_fi_to_witness_delivery(**arguments)
        if mailbox == "witness-to-ir":
            return value.create_witness_to_ir_delivery(**arguments)
        if mailbox == "ir-to-witness":
            return value.create_ir_to_witness_delivery(**arguments)
        if mailbox == "witness-to-fi":
            return value.create_witness_to_fi_delivery(**arguments)
        raise AssertionError("unknown fixed mailbox")

    @staticmethod
    def _invoke_scanner(value, *, mailbox: str):
        if mailbox == "fi-to-witness":
            list_method = value.list_fi_to_witness_delivery_locators
            read_method = value.read_fi_to_witness_delivery_exact
        elif mailbox == "witness-to-ir":
            list_method = value.list_witness_to_ir_delivery_locators
            read_method = value.read_witness_to_ir_delivery_exact
        elif mailbox == "ir-to-witness":
            list_method = value.list_ir_to_witness_delivery_locators
            read_method = value.read_ir_to_witness_delivery_exact
        elif mailbox == "witness-to-fi":
            list_method = value.list_witness_to_fi_delivery_locators
            read_method = value.read_witness_to_fi_delivery_exact
        else:
            raise AssertionError("unknown fixed mailbox")
        locators = list_method()
        return locators, read_method(
            object_key=locators[0].object_key,
            object_version_id=locators[0].object_version_id,
        )

    def test_eight_role_local_adapters_cover_only_fixed_named_hops(self) -> None:
        for policy in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES:
            with self.subTest(role=policy.local_role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = self._bundle(policy, root=root)
                packet = self.deliveries[policy.mailbox]
                raw = _RawS3(
                    mailbox=policy.mailbox,
                    packet=packet,
                    proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
                )
                scope = _Scope(raw)
                value = self._open(policy, config=config, scope=scope)
                self.assertEqual([], scope.calls, "opening must not make an S3 callback")
                with patch.object(adapter, "_host_now", return_value=NOW):
                    if policy.direction == "publish":
                        receipt = self._invoke_publisher(
                            value,
                            mailbox=policy.mailbox,
                            packet=packet,
                        )
                        self.assertTrue(receipt.create_only)
                        self.assertTrue(receipt.immutable)
                        self.assertEqual(raw.object_key, receipt.object_key)
                        self.assertEqual(["put", "head", "get"], raw.calls)
                    else:
                        locators, content = self._invoke_scanner(value, mailbox=policy.mailbox)
                        self.assertEqual(1, len(locators))
                        self.assertEqual(packet, content.canonical_delivery)
                        self.assertEqual(["list", "head", "get"], raw.calls)
                self.assertTrue(scope.calls)
                self.assertTrue(all(role == policy.local_role for _name, role in scope.calls))

    def test_admission_retention_and_credential_fences_happen_before_scope(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(policy, root=root, write_credential=False)
            raw = _RawS3(
                mailbox=policy.mailbox,
                packet=packet,
                proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
            )
            scope = _Scope(raw)
            with self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "RETENTION_PROOF_INVALID",
            ):
                self._open(policy, config=replace(config, retention_proof=None), scope=scope)
            self.assertEqual([], scope.calls)

            value = self._open(policy, config=config, scope=scope)
            with patch.object(adapter, "_host_now", return_value=NOW), self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "CREDENTIAL_DIRECTORY_UNSAFE",
            ):
                self._invoke_publisher(value, mailbox=policy.mailbox, packet=packet)
            self.assertEqual([], scope.calls)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(policy, root=root)
            scope = _Scope(
                _RawS3(
                    mailbox=policy.mailbox,
                    packet=packet,
                    proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
                )
            )
            with self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "ADMISSION",
            ):
                adapter.open_physical_wal_v2_witness_roundtrip_witness_to_ir_publisher_s3_adapter(
                    config=config,
                    scope=scope,
                    now=NOW,
                )
            self.assertEqual([], scope.calls)

            with self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "ADMISSION_INVALID",
            ):
                self._open(
                    policy,
                    config=replace(
                        config,
                        admission_config=replace(
                            config.admission_config,  # type: ignore[arg-type]
                            host_id="s3-mailbox-host-foreign",
                        ),
                    ),
                    scope=scope,
                )
            self.assertEqual([], scope.calls)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(policy, root=root, write_credential=False)
            self._credential(root, local_role="witness-ir-egress")
            scope = _Scope(
                _RawS3(
                    mailbox=policy.mailbox,
                    packet=packet,
                    proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
                )
            )
            value = self._open(policy, config=config, scope=scope)
            with patch.object(adapter, "_host_now", return_value=NOW), self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "CREDENTIAL_FILE_UNSAFE",
            ):
                self._invoke_publisher(value, mailbox=policy.mailbox, packet=packet)
            self.assertEqual([], scope.calls)

    def test_expired_retention_proof_and_unlisted_or_tampered_objects_fail_closed(self) -> None:
        publisher_policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        scanner_policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(
                publisher_policy,
                root=root,
                proof_expires_at=NOW + timedelta(seconds=5),
            )
            packet = self.deliveries[publisher_policy.mailbox]
            scope = _Scope(
                _RawS3(
                    mailbox=publisher_policy.mailbox,
                    packet=packet,
                    proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
                )
            )
            value = self._open(publisher_policy, config=config, scope=scope)
            with patch.object(adapter, "_host_now", return_value=NOW + timedelta(seconds=6)), self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "RETENTION_PROOF_INVALID",
            ):
                self._invoke_publisher(value, mailbox=publisher_policy.mailbox, packet=packet)
            self.assertEqual([], scope.calls)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(scanner_policy, root=root)
            packet = self.deliveries[scanner_policy.mailbox]
            raw = _RawS3(
                mailbox=scanner_policy.mailbox,
                packet=packet,
                proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
            )
            scope = _Scope(raw)
            value = self._open(scanner_policy, config=config, scope=scope)
            with patch.object(adapter, "_host_now", return_value=NOW):
                locators = value.list_fi_to_witness_delivery_locators()
                with self.assertRaisesRegex(
                    adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                    "UNLISTED_EXACT_READ",
                ):
                    value.read_fi_to_witness_delivery_exact(
                        object_key=locators[0].object_key,
                        object_version_id="foreign-version-000001",
                    )
                self.assertEqual(1, len(scope.calls))
                raw.bad_read = True
                with self.assertRaisesRegex(
                    adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                    "READBACK_INVALID",
                ):
                    value.read_fi_to_witness_delivery_exact(
                        object_key=locators[0].object_key,
                        object_version_id=locators[0].object_version_id,
                    )

    def test_deterministic_create_only_and_exact_immutable_readback_are_mandatory(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._bundle(policy, root=root)
            raw = _RawS3(
                mailbox=policy.mailbox,
                packet=packet,
                proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
            )
            scope = _Scope(raw)
            value = self._open(policy, config=config, scope=scope)
            digest = hashlib.sha256(packet).hexdigest()
            with patch.object(adapter, "_host_now", return_value=NOW), self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "PUBLISH_INPUT_INVALID",
            ):
                value.create_fi_to_witness_delivery(
                    object_key="physical-wal-v2-witness-roundtrip-delivery-v1/fi-to-witness/" + "f" * 64 + ".json",
                    canonical_delivery=packet,
                    content_sha256=digest,
                    content_bytes=len(packet),
                    retained_until=NOW + timedelta(seconds=30),
                )
            self.assertEqual([], scope.calls)
            raw.bad_create = True
            with patch.object(adapter, "_host_now", return_value=NOW), self.assertRaisesRegex(
                adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterError,
                "CREATE_RECEIPT_INVALID",
            ):
                self._invoke_publisher(value, mailbox=policy.mailbox, packet=packet)
            self.assertEqual(["put", "head", "get"], raw.calls)

    def test_source_exposes_no_provider_factory_or_generic_or_broad_adapter_api(self) -> None:
        source = inspect.getsource(adapter)
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertFalse(imports & {"boto3", "botocore", "requests", "socket", "urllib", "http"})
        self.assertNotIn("physical_arvan_", source)
        self.assertNotIn("fi-to-ir", source)
        self.assertNotIn("ir-to-fi", source)
        self.assertNotIn("getattr(", source)
        concrete = {
            adapter.PhysicalWalV2WitnessRoundtripFiToWitnessPublisherS3Adapter: {
                "create_fi_to_witness_delivery"
            },
            adapter.PhysicalWalV2WitnessRoundtripWitnessIrEgressS3Adapter: {
                "create_witness_to_ir_delivery"
            },
            adapter.PhysicalWalV2WitnessRoundtripIrToWitnessPublisherS3Adapter: {
                "create_ir_to_witness_delivery"
            },
            adapter.PhysicalWalV2WitnessRoundtripWitnessFiPublisherS3Adapter: {
                "create_witness_to_fi_delivery"
            },
            adapter.PhysicalWalV2WitnessRoundtripWitnessFiIngressS3Adapter: {
                "list_fi_to_witness_delivery_locators",
                "read_fi_to_witness_delivery_exact",
            },
            adapter.PhysicalWalV2WitnessRoundtripIrStandbyIngressS3Adapter: {
                "list_witness_to_ir_delivery_locators",
                "read_witness_to_ir_delivery_exact",
            },
            adapter.PhysicalWalV2WitnessRoundtripWitnessIrIngressS3Adapter: {
                "list_ir_to_witness_delivery_locators",
                "read_ir_to_witness_delivery_exact",
            },
            adapter.PhysicalWalV2WitnessRoundtripFiAckIngressS3Adapter: {
                "list_witness_to_fi_delivery_locators",
                "read_witness_to_fi_delivery_exact",
            },
        }
        for concrete_class, expected in concrete.items():
            public = {
                name
                for name, value in concrete_class.__dict__.items()
                if callable(value) and not name.startswith("_")
            }
            self.assertEqual(expected, public)
