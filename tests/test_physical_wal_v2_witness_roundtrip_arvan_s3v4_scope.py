from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import timedelta
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
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as scope_module
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as mailbox_adapter
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW
from tests import test_physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as adapter_tests


class _Body:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.offset = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        result = self.value[self.offset : self.offset + size]
        self.offset += len(result)
        return result

    def close(self) -> None:
        self.closed = True


class _FakeS3:
    def __init__(self, *, mailbox: str, packet: bytes) -> None:
        self.mailbox = mailbox
        self.packet = packet
        self.object_key = mailbox_adapter._object_key(
            mailbox,
            hashlib.sha256(packet).hexdigest(),
        )
        self.version = "version-000001"
        self.retained_until = NOW + timedelta(seconds=120)
        self.metadata = {
            "content-sha256": hashlib.sha256(packet).hexdigest(),
            "retention-proof-sha256": "a" * 64,
        }
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _response(self) -> dict[str, object]:
        return {
            "ContentLength": len(self.packet),
            "Metadata": dict(self.metadata),
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self.retained_until,
        }

    def put_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("put_object", dict(kwargs)))
        self.packet = kwargs["Body"]  # type: ignore[assignment]
        self.object_key = kwargs["Key"]  # type: ignore[assignment]
        self.retained_until = kwargs["ObjectLockRetainUntilDate"]  # type: ignore[assignment]
        self.metadata = dict(kwargs["Metadata"])  # type: ignore[arg-type]
        return {"VersionId": self.version}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("head_object", dict(kwargs)))
        return self._response()

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", dict(kwargs)))
        return {**self._response(), "Body": _Body(self.packet)}

    def list_object_versions(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("list_object_versions", dict(kwargs)))
        return {
            "IsTruncated": False,
            "Versions": [{"Key": self.object_key, "VersionId": self.version}],
        }


class _FakeConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeBoto3:
    def __init__(self, client: _FakeS3) -> None:
        self.client_value = client
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def client(self, *args: object, **kwargs: object) -> _FakeS3:
        self.calls.append((args, dict(kwargs)))
        return self.client_value


class PhysicalWalV2WitnessRoundtripArvanS3v4ScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = adapter_tests.PhysicalWalV2WitnessRoundtripS3MailboxAdapterTests("runTest")
        self.base.setUp()
        self.route_authority = Ed25519PrivateKey.generate()

    def tearDown(self) -> None:
        self.base.tearDown()

    @staticmethod
    def _write_profile(
        root: Path,
        *,
        config: mailbox_adapter.PhysicalWalV2WitnessRoundtripS3MailboxAdapterConfig,
        policy: admission.PhysicalWalV2WitnessRoundtripMailboxPolicy,
        route_iam: scope_module.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
        changes: dict[str, object] | None = None,
    ) -> None:
        facts = mailbox_adapter._config(
            config,
            local_role=policy.local_role,
            direction=policy.direction,
            now=NOW,
        )
        payload: dict[str, object] = {
            "schema": scope_module._PROFILE_SCHEMA,
            "version": scope_module._PROFILE_VERSION,
            "host_id": facts.mailbox_admission.host_id,
            "local_role": policy.local_role,
            "mailbox": policy.mailbox,
            "direction": policy.direction,
            "object_prefix": policy.object_prefix,
            "endpoint_url": "https://s3.ir-thr-at1.arvanstorage.ir",
            "bucket": "gold-trade-v2-witness",
            "region_name": "ir-thr-at1",
            "addressing_style": "path",
            "admission_sha256": facts.mailbox_admission.admission_sha256,
            "delivery_binding_sha256": facts.delivery_binding_sha256,
            "retention_proof_sha256": facts.retention_proof.proof_sha256,
            "provider_route_iam_attestation_sha256": route_iam.attestation_sha256,
        }
        if changes:
            payload.update(changes)
        directory = root / scope_module._PROFILE_DIRECTORY
        os.mkdir(directory, 0o700)
        descriptor = os.open(
            directory / (policy.local_role + ".json"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            raw = canonical_json_bytes(payload)
            if os.write(descriptor, raw) != len(raw):
                raise AssertionError("short profile fixture write")
        finally:
            os.close(descriptor)
        os.chmod(root, 0o700)
        os.chmod(directory, 0o700)

    @staticmethod
    def _scope_config(root: Path, config, route_config, route_iam):
        return scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeConfig(
            mailbox_adapter_config=config,
            provider_route_iam_attestation_config=route_config,
            provider_route_iam_attestation=route_iam,
            root=root,
            enabled=True,
        )

    def _route_iam(
        self,
        *,
        config,
        policy,
        expires_at=NOW + timedelta(seconds=35),
        allowed_actions: tuple[str, ...] | None = None,
    ):
        facts = mailbox_adapter._config(
            config,
            local_role=policy.local_role,
            direction=policy.direction,
            now=NOW,
        )
        public = self.route_authority.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        route_config = scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig(
            mailbox_adapter_config=config,
            provider_route_iam_authority_public_key=public,
            enabled=True,
        )
        actions = allowed_actions or scope_module._route_actions(direction=policy.direction)
        unsigned = {
            "schema": scope_module._ROUTE_IAM_SCHEMA,
            "version": scope_module._ROUTE_IAM_VERSION,
            "host_id": facts.mailbox_admission.host_id,
            "local_role": policy.local_role,
            "mailbox": policy.mailbox,
            "direction": policy.direction,
            "object_prefix": policy.object_prefix,
            "endpoint_url": "https://s3.ir-thr-at1.arvanstorage.ir",
            "bucket": "gold-trade-v2-witness",
            "region_name": "ir-thr-at1",
            "addressing_style": "path",
            "allowed_s3_actions": list(actions),
            "admission_sha256": facts.mailbox_admission.admission_sha256,
            "deployment_binding_sha256": facts.mailbox_admission.deployment_binding_sha256,
            "delivery_binding_sha256": facts.delivery_binding_sha256,
            "retention_proof_sha256": facts.retention_proof.proof_sha256,
            "attestation_id": "provider-route-iam-000001",
            "attestation_nonce": "P" * 22,
            "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        signature = self.route_authority.sign(
            scope_module._ROUTE_IAM_DOMAIN + canonical_json_bytes(unsigned)
        )
        wire = canonical_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )
        return route_config, scope_module.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
            wire,
            config=route_config,
            local_role=policy.local_role,
            direction=policy.direction,
            now=NOW,
        )

    @staticmethod
    def _open_scope(policy, *, config):
        opens = {
            "fi-writer-source-outbox": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_scope,
            "witness-fi-ingress": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_scope,
            "witness-ir-egress": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_scope,
            "ir-standby-ack-inbox": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_scope,
            "ir-durable-ack-outbox": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_scope,
            "witness-ir-ingress": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_scope,
            "witness-fi-egress": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_scope,
            "fi-writer-ack-inbox": scope_module.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_scope,
        }[policy.local_role]
        return opens(config=config, now=NOW)

    @staticmethod
    def _open_adapter(policy, *, config, scope):
        return adapter_tests.PhysicalWalV2WitnessRoundtripS3MailboxAdapterTests._open(
            policy,
            config=config,
            scope=scope,
        )

    def test_lazy_s3v4_publisher_uses_fixed_endpoint_bucket_region_and_object_lock(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.base.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(config=config, policy=policy)
            self._write_profile(root, config=config, policy=policy, route_iam=route_iam)
            fake = _FakeS3(mailbox=policy.mailbox, packet=packet)
            created: list[tuple[object, object]] = []

            def factory(*, credentials, profile):
                created.append((credentials, profile))
                return fake

            with patch.object(scope_module, "_new_s3v4_client", side_effect=factory), patch.object(
                scope_module, "_host_now", return_value=NOW
            ), patch.object(mailbox_adapter, "_host_now", return_value=NOW):
                raw_scope = self._open_scope(
                    policy,
                    config=self._scope_config(root, config, route_config, route_iam),
                )
                self.assertEqual([], created, "scope open must not import/create an SDK client")
                adapter = self._open_adapter(policy, config=config, scope=raw_scope)
                receipt = self.base._invoke_publisher(adapter, mailbox=policy.mailbox, packet=packet)
            self.assertTrue(receipt.create_only)
            self.assertEqual(1, len(created))
            self.assertEqual([name for name, _kwargs in fake.calls], ["put_object", "head_object", "get_object"])
            put = fake.calls[0][1]
            self.assertEqual("gold-trade-v2-witness", put["Bucket"])
            self.assertTrue(str(put["Key"]).startswith(policy.object_prefix))
            self.assertEqual("*", put["IfNoneMatch"])
            self.assertEqual("COMPLIANCE", put["ObjectLockMode"])
            self.assertEqual("SHA256", put["ChecksumAlgorithm"])
            self.assertEqual(config.retention_proof.proof_sha256, put["Metadata"]["retention-proof-sha256"])  # type: ignore[union-attr,index]
            self.assertEqual("bytes=0-2097151", fake.calls[2][1]["Range"])
            self.assertFalse(hasattr(raw_scope, "client"))

    def test_scanner_uses_fixed_prefix_version_listing_then_exact_head_and_bounded_get(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[1]
        packet = self.base.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(config=config, policy=policy)
            self._write_profile(root, config=config, policy=policy, route_iam=route_iam)
            fake = _FakeS3(mailbox=policy.mailbox, packet=packet)
            fake.metadata["retention-proof-sha256"] = config.retention_proof.proof_sha256  # type: ignore[union-attr]
            with patch.object(scope_module, "_new_s3v4_client", return_value=fake), patch.object(
                scope_module, "_host_now", return_value=NOW
            ), patch.object(mailbox_adapter, "_host_now", return_value=NOW):
                raw_scope = self._open_scope(
                    policy,
                    config=self._scope_config(root, config, route_config, route_iam),
                )
                adapter = self._open_adapter(policy, config=config, scope=raw_scope)
                locators, content = self.base._invoke_scanner(adapter, mailbox=policy.mailbox)
            self.assertEqual(packet, content.canonical_delivery)
            self.assertEqual(1, len(locators))
            self.assertEqual([name for name, _kwargs in fake.calls], ["list_object_versions", "head_object", "head_object", "get_object"])
            listed = fake.calls[0][1]
            self.assertEqual("gold-trade-v2-witness", listed["Bucket"])
            self.assertEqual(policy.object_prefix, listed["Prefix"])
            self.assertEqual(8, listed["MaxKeys"])
            self.assertEqual(fake.object_key, fake.calls[-1][1]["Key"])
            self.assertEqual(fake.version, fake.calls[-1][1]["VersionId"])

    def test_profile_cross_pins_are_checked_before_lazy_sdk_creation(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(config=config, policy=policy)
            self._write_profile(
                root,
                config=config,
                policy=policy,
                route_iam=route_iam,
                changes={"mailbox": "witness-to-ir"},
            )
            with patch.object(scope_module, "_new_s3v4_client") as factory, self.assertRaisesRegex(
                scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
                "PROFILE_CROSS_PIN_MISMATCH",
            ):
                self._open_scope(
                    policy,
                    config=self._scope_config(root, config, route_config, route_iam),
                )
            factory.assert_not_called()

    def test_provider_route_iam_attestation_is_mandatory_and_rechecked_before_sdk(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.base.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(config=config, policy=policy)
            self._write_profile(root, config=config, policy=policy, route_iam=route_iam)
            scope_config = self._scope_config(root, config, route_config, route_iam)
            with patch.object(scope_module, "_new_s3v4_client") as factory, self.assertRaisesRegex(
                scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
                "ROUTE_IAM_ATTESTATION_INVALID",
            ):
                self._open_scope(
                    policy,
                    config=replace(scope_config, provider_route_iam_attestation=None),
                )
            factory.assert_not_called()

    def test_signed_route_iam_action_and_expiry_substitution_fail_closed(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.base.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            with self.assertRaisesRegex(
                scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
                "ROUTE_IAM_ATTESTATION_CROSS_PIN_MISMATCH",
            ):
                self._route_iam(
                    config=config,
                    policy=policy,
                    allowed_actions=(
                        "s3:PutObject:IfNoneMatch",
                        "s3:HeadObject:ExactVersion",
                        "s3:GetObject:ExactVersion",
                    ),
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(
                config=config,
                policy=policy,
                expires_at=NOW + timedelta(seconds=5),
            )
            self._write_profile(root, config=config, policy=policy, route_iam=route_iam)
            fake = _FakeS3(mailbox=policy.mailbox, packet=packet)
            with patch.object(scope_module, "_new_s3v4_client", return_value=fake) as factory, patch.object(
                scope_module, "_host_now", return_value=NOW
            ):
                raw_scope = self._open_scope(
                    policy,
                    config=self._scope_config(root, config, route_config, route_iam),
                )
                facts = mailbox_adapter._config(
                    config,
                    local_role=policy.local_role,
                    direction=policy.direction,
                    now=NOW,
                )
                credentials = mailbox_adapter._load_fixed_credentials(facts)
                with patch.object(
                    scope_module,
                    "_host_now",
                    return_value=NOW + timedelta(seconds=6),
                ), self.assertRaisesRegex(
                    scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
                    "ROUTE_IAM_ATTESTATION_INVALID",
                ):
                    raw_scope.with_fi_to_witness_publisher_s3(
                        credentials=credentials,
                        operation=lambda raw: raw,
                    )
            factory.assert_not_called()

    def test_sdk_factory_is_lazy_s3v4_path_style_and_raw_handle_is_dead_after_callback(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        packet = self.base.deliveries[policy.mailbox]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.base._bundle(policy, root=root)
            route_config, route_iam = self._route_iam(config=config, policy=policy)
            self._write_profile(root, config=config, policy=policy, route_iam=route_iam)
            fake = _FakeS3(mailbox=policy.mailbox, packet=packet)
            fake_boto3 = _FakeBoto3(fake)
            with patch.object(
                scope_module,
                "_load_s3v4_sdk",
                return_value=(fake_boto3, _FakeConfig),
            ), patch.object(scope_module, "_host_now", return_value=NOW):
                raw_scope = self._open_scope(
                    policy,
                    config=self._scope_config(root, config, route_config, route_iam),
                )
                facts = mailbox_adapter._config(
                    config,
                    local_role=policy.local_role,
                    direction=policy.direction,
                    now=NOW,
                )
                credentials = mailbox_adapter._load_fixed_credentials(facts)
                captured = raw_scope.with_fi_to_witness_publisher_s3(
                    credentials=credentials,
                    operation=lambda raw: raw,
                )
            self.assertEqual(1, len(fake_boto3.calls))
            positional, keyword = fake_boto3.calls[0]
            self.assertEqual(("s3",), positional)
            self.assertEqual("https://s3.ir-thr-at1.arvanstorage.ir", keyword["endpoint_url"])
            self.assertEqual("ir-thr-at1", keyword["region_name"])
            self.assertEqual("AKIA00000001", keyword["aws_access_key_id"])
            self.assertEqual("s" * 32, keyword["aws_secret_access_key"])
            self.assertEqual("s3v4", keyword["config"].kwargs["signature_version"])
            self.assertEqual({"addressing_style": "path"}, keyword["config"].kwargs["s3"])
            with self.assertRaisesRegex(
                scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
                "RAW_HANDLE_INACTIVE",
            ):
                captured.put_fi_to_witness_create_only(
                    object_key=fake.object_key,
                    canonical_delivery=packet,
                    content_sha256=hashlib.sha256(packet).hexdigest(),
                    content_bytes=len(packet),
                    retained_until=NOW + timedelta(seconds=30),
                    retention_proof_sha256=config.retention_proof.proof_sha256,  # type: ignore[union-attr]
                )

    def test_eight_named_scope_entrypoints_have_no_broad_or_retired_factory_surface(self) -> None:
        source = inspect.getsource(scope_module)
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
        self.assertNotIn("physical_arvan_s3_", source)
        self.assertNotIn("fi-to-ir", source)
        self.assertNotIn("ir-to-fi", source)
        self.assertNotIn("requests", imports)
        self.assertNotIn("socket", imports)
        expected = {
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherScope: {
                "with_fi_to_witness_publisher_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressScope: {
                "with_fi_to_witness_ingress_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressScope: {
                "with_witness_to_ir_egress_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressScope: {
                "with_witness_to_ir_ingress_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherScope: {
                "with_ir_to_witness_publisher_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressScope: {
                "with_ir_to_witness_ingress_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherScope: {
                "with_witness_to_fi_publisher_s3"
            },
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressScope: {
                "with_witness_to_fi_ingress_s3"
            },
        }
        for scope_type, methods in expected.items():
            public = {
                name
                for name, value in scope_type.__dict__.items()
                if callable(value) and not name.startswith("_")
            }
            self.assertEqual(methods, public)
