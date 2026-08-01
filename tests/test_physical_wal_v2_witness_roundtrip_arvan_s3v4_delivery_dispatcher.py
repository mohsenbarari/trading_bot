from __future__ import annotations

import ast
import base64
from contextlib import ExitStack
from dataclasses import fields, replace
from datetime import timedelta
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as scope_module
from core import physical_wal_v2_witness_roundtrip_delivery_contract as delivery
from core import physical_wal_v2_witness_roundtrip_delivery_runtime as runtime
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as mailbox_adapter
from tests import test_physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as scope_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherTests(unittest.TestCase):
    """Exercise only role-local opens; the eight-role proof is public evidence."""

    def setUp(self) -> None:
        self.fixture = scope_tests.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeTests(
            "runTest"
        )
        self.fixture.setUp()
        # The full topology is signed by the exact same deployment authority
        # that signs each local host-role admission.  It is not a new trust
        # root.
        self.full_bundle_authority = self.fixture.base.authority

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _sign_full_bundle(
        self,
        projections: list[dict[str, object]],
        *,
        expires_at=NOW + timedelta(seconds=30),
        signer: Ed25519PrivateKey | None = None,
        authority_public_key_claim: bytes | None = None,
        deployment_binding_sha256: str | None = None,
        release_sha: str | None = None,
    ) -> bytes:
        signer = self.full_bundle_authority if signer is None else signer
        if authority_public_key_claim is None:
            authority_public_key_claim = _public(signer)
        if deployment_binding_sha256 is None:
            deployment_binding_sha256 = projections[0]["deployment_binding_sha256"]  # type: ignore[assignment]
        if release_sha is None:
            release_sha = self.release_sha
        unsigned = {
            "schema": dispatcher._FULL_BUNDLE_SCHEMA,
            "version": dispatcher._FULL_BUNDLE_VERSION,
            "bundle_id": "full-bundle-attestation-000001",
            "bundle_nonce": "B" * 22,
            "release_sha": release_sha,
            "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "deployment_binding_sha256": deployment_binding_sha256,
            "deployment_authority_public_key_sha256": hashlib.sha256(
                authority_public_key_claim
            ).hexdigest(),
            "role_projections": projections,
        }
        signature = signer.sign(
            dispatcher._FULL_BUNDLE_DOMAIN + canonical_json_bytes(unsigned)
        )
        return canonical_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )

    def _bundle(self, stack: ExitStack):
        """Build eight independent host-local configs and one portable proof.

        The returned dispatcher configuration for any opener is deliberately a
        *single* host config.  The temporary roots of the other roles are not
        reachable from it; only their public hash projections have been copied
        into the signed topology evidence.
        """

        public_by_role: dict[str, dict[str, object]] = {}
        local_material: dict[str, tuple[object, object]] = {}
        fakes: dict[str, scope_tests._FakeS3] = {}
        for policy in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            state_root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            mailbox_config = self.fixture.base._bundle(policy, root=root)
            route_config, route_iam = self.fixture._route_iam(
                config=mailbox_config,
                policy=policy,
            )
            self.fixture._write_profile(
                root,
                config=mailbox_config,
                policy=policy,
                route_iam=route_iam,
            )
            mailbox_facts = mailbox_adapter._config(
                mailbox_config,
                local_role=policy.local_role,
                direction=policy.direction,
                now=NOW,
            )
            delivery_facts = delivery._config(
                mailbox_config.delivery_config,
                mailbox=policy.mailbox,
            )
            self.release_sha = delivery_facts.binding.release_sha
            public_by_role[policy.local_role] = {
                "host_id": mailbox_facts.mailbox_admission.host_id,
                "local_role": policy.local_role,
                "mailbox": policy.mailbox,
                "direction": policy.direction,
                "object_prefix": policy.object_prefix,
                "admission_sha256": mailbox_facts.mailbox_admission.admission_sha256,
                "deployment_binding_sha256": (
                    mailbox_facts.mailbox_admission.deployment_binding_sha256
                ),
                "delivery_binding_sha256": delivery_facts.binding_sha256,
                "retention_proof_sha256": mailbox_facts.retention_proof.proof_sha256,
                "provider_route_iam_attestation_sha256": route_iam.attestation_sha256,
                "roundtrip_configuration_sha256": (
                    delivery_facts.binding.roundtrip_configuration_sha256
                ),
            }
            runtime_config = runtime.PhysicalWalV2WitnessRoundtripDeliveryRuntimeConfig(
                state_root=state_root,
                delivery_config=mailbox_config.delivery_config,
                local_role=policy.local_role,
                enabled=True,
                maximum_records=8,
            )
            scope_config = self.fixture._scope_config(
                root,
                mailbox_config,
                route_config,
                route_iam,
            )
            local_material[policy.local_role] = (runtime_config, scope_config)
            fake = scope_tests._FakeS3(
                mailbox=policy.mailbox,
                packet=self.fixture.base.deliveries[policy.mailbox],
            )
            fake.metadata["retention-proof-sha256"] = mailbox_facts.retention_proof.proof_sha256
            fakes[policy.local_role] = fake

        projections = [
            public_by_role[local_role]
            for local_role, _mailbox, _direction, _prefix in dispatcher._ROLE_SPECS
        ]
        full_bundle_wire = self._sign_full_bundle(projections)
        configs: dict[
            str, dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig
        ] = {}
        for local_role, (runtime_config, scope_config) in local_material.items():
            full_bundle_config = (
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig(
                    mailbox_adapter_config=scope_config.mailbox_adapter_config,  # type: ignore[union-attr]
                    expected_release_sha=self.release_sha,
                    enabled=True,
                    maximum_evidence_age_seconds=60,
                )
            )
            full_bundle = (
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    full_bundle_wire,
                    config=full_bundle_config,
                    now=NOW,
                )
            )
            configs[local_role] = (
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig(
                    runtime_config=runtime_config,  # type: ignore[arg-type]
                    scope_config=scope_config,  # type: ignore[arg-type]
                    full_bundle_attestation_config=full_bundle_config,
                    full_bundle_attestation=full_bundle,
                    enabled=True,
                )
            )
        return configs, fakes, projections, full_bundle_wire

    @staticmethod
    def _fake_factory(fakes: dict[str, scope_tests._FakeS3]):
        def factory(*, credentials, profile):
            del profile
            return fakes[credentials.local_role]

        return factory

    @staticmethod
    def _opens():
        return {
            "fi-writer-source-outbox": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher,
            "witness-fi-ingress": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher,
            "witness-ir-egress": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_ir_publisher_dispatcher,
            "ir-standby-ack-inbox": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_standby_ingress_dispatcher,
            "ir-durable-ack-outbox": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_ir_to_witness_publisher_dispatcher,
            "witness-ir-ingress": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_ir_ingress_dispatcher,
            "witness-fi-egress": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_to_fi_publisher_dispatcher,
            "fi-writer-ack-inbox": dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_ack_ingress_dispatcher,
        }

    def test_exact_eight_named_dispatchers_run_the_four_hop_runtime_chain(self) -> None:
        with ExitStack() as stack:
            configs, fakes, _projections, _wire = self._bundle(stack)
            factory = self._fake_factory(fakes)
            with patch.object(scope_module, "_new_s3v4_client", side_effect=factory), patch.object(
                scope_module, "_host_now", return_value=NOW
            ), patch.object(mailbox_adapter, "_host_now", return_value=NOW), patch.object(
                runtime, "_host_now", return_value=NOW
            ), patch.object(dispatcher, "_host_now", return_value=NOW):
                fi_source = self._opens()["fi-writer-source-outbox"](
                    config=configs["fi-writer-source-outbox"], now=NOW
                )
                witness_fi = self._opens()["witness-fi-ingress"](
                    config=configs["witness-fi-ingress"], now=NOW
                )
                witness_ir = self._opens()["witness-ir-egress"](
                    config=configs["witness-ir-egress"], now=NOW
                )
                ir_standby = self._opens()["ir-standby-ack-inbox"](
                    config=configs["ir-standby-ack-inbox"], now=NOW
                )
                ir_ack = self._opens()["ir-durable-ack-outbox"](
                    config=configs["ir-durable-ack-outbox"], now=NOW
                )
                witness_ack = self._opens()["witness-ir-ingress"](
                    config=configs["witness-ir-ingress"], now=NOW
                )
                witness_final = self._opens()["witness-fi-egress"](
                    config=configs["witness-fi-egress"], now=NOW
                )
                fi_final = self._opens()["fi-writer-ack-inbox"](
                    config=configs["fi-writer-ack-inbox"], now=NOW
                )

                self.assertEqual(
                    "published",
                    fi_source.publish_fi_to_witness_delivery(
                        self.fixture.base.deliveries["fi-to-witness"]
                    ).status,
                )
                self.assertEqual("consumed", witness_fi.consume_fi_to_witness_delivery()[0].status)
                self.assertEqual(
                    "published",
                    witness_ir.publish_witness_to_ir_delivery(
                        self.fixture.base.deliveries["witness-to-ir"]
                    ).status,
                )
                self.assertEqual("consumed", ir_standby.consume_witness_to_ir_delivery()[0].status)
                self.assertEqual(
                    "published",
                    ir_ack.publish_ir_to_witness_delivery(
                        self.fixture.base.deliveries["ir-to-witness"]
                    ).status,
                )
                self.assertEqual("consumed", witness_ack.consume_ir_to_witness_delivery()[0].status)
                self.assertEqual(
                    "published",
                    witness_final.publish_witness_to_fi_delivery(
                        self.fixture.base.deliveries["witness-to-fi"]
                    ).status,
                )
                self.assertEqual("consumed", fi_final.consume_witness_to_fi_delivery()[0].status)

            expected = {
                "fi-writer-source-outbox": ["put_object", "head_object", "get_object"],
                "witness-fi-ingress": ["list_object_versions", "head_object", "head_object", "get_object"],
                "witness-ir-egress": ["put_object", "head_object", "get_object"],
                "ir-standby-ack-inbox": ["list_object_versions", "head_object", "head_object", "get_object"],
                "ir-durable-ack-outbox": ["put_object", "head_object", "get_object"],
                "witness-ir-ingress": ["list_object_versions", "head_object", "head_object", "get_object"],
                "witness-fi-egress": ["put_object", "head_object", "get_object"],
                "fi-writer-ack-inbox": ["list_object_versions", "head_object", "head_object", "get_object"],
            }
            self.assertEqual(
                expected,
                {role: [name for name, _kwargs in fake.calls] for role, fake in fakes.items()},
            )

    def test_local_open_reads_only_its_local_configs_and_no_remote_root_or_client(self) -> None:
        with ExitStack() as stack:
            configs, fakes, _projections, _wire = self._bundle(stack)
            local = configs["fi-writer-source-outbox"]
            remote_scope_ids = {
                id(config.scope_config)
                for role, config in configs.items()
                if role != "fi-writer-source-outbox"
            }
            remote_mailbox_ids = {
                id(config.scope_config.mailbox_adapter_config)  # type: ignore[union-attr]
                for role, config in configs.items()
                if role != "fi-writer-source-outbox"
            }
            with patch.object(scope_module, "_config", wraps=scope_module._config) as scope_config, patch.object(
                mailbox_adapter, "_config", wraps=mailbox_adapter._config
            ) as mailbox_config, patch.object(
                runtime, "_config", wraps=runtime._config
            ) as runtime_config, patch.object(
                scope_module, "_open_secure_root", wraps=scope_module._open_secure_root
            ) as profile_root, patch.object(
                scope_module, "_new_s3v4_client", side_effect=self._fake_factory(fakes)
            ) as client_factory, patch.object(scope_module, "_host_now", return_value=NOW), patch.object(
                mailbox_adapter, "_host_now", return_value=NOW
            ), patch.object(runtime, "_host_now", return_value=NOW), patch.object(
                dispatcher, "_host_now", return_value=NOW):
                self._opens()["fi-writer-source-outbox"](config=local, now=NOW)

            self.assertTrue(scope_config.call_args_list)
            self.assertTrue(mailbox_config.call_args_list)
            self.assertTrue(runtime_config.call_args_list)
            self.assertTrue(profile_root.call_args_list)
            self.assertTrue(
                all(call.args[0] is local.scope_config for call in scope_config.call_args_list)
            )
            self.assertTrue(
                all(
                    call.args[0] is local.scope_config.mailbox_adapter_config
                    for call in mailbox_config.call_args_list
                )
            )
            self.assertTrue(
                all(call.args[0] is local.runtime_config for call in runtime_config.call_args_list)
            )
            self.assertTrue(
                all(call.args[0] == local.scope_config.root for call in profile_root.call_args_list)
            )
            self.assertNotIn(id(local.scope_config), remote_scope_ids)
            self.assertNotIn(id(local.scope_config.mailbox_adapter_config), remote_mailbox_ids)
            client_factory.assert_not_called()

            public_wire = local.full_bundle_attestation.canonical_attestation.decode("ascii")  # type: ignore[union-attr]
            for forbidden in (
                "credential_root",
                "access_key_id",
                "secret_access_key",
                "scope_config",
                "runtime_config",
                "/tmp/",
            ):
                self.assertNotIn(forbidden, public_wire)

    def test_missing_or_locally_mismatched_full_bundle_fails_before_s3_client_creation(self) -> None:
        with ExitStack() as stack:
            configs, fakes, projections, _wire = self._bundle(stack)
            local_bundle_config = configs[
                "fi-writer-source-outbox"
            ].full_bundle_attestation_config
            missing_wire = self._sign_full_bundle(projections[:-1])
            with patch.object(scope_module, "_new_s3v4_client", side_effect=self._fake_factory(fakes)) as factory, self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
            ):
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    missing_wire,
                    config=local_bundle_config,  # type: ignore[arg-type]
                    now=NOW,
                )
            factory.assert_not_called()

            # The v1 signed schema is exact: an otherwise canonical legacy
            # wire without the release pin is never accepted compatibly.
            legacy_item = json.loads(
                self._sign_full_bundle(projections).decode("ascii")
            )
            legacy_item.pop("release_sha")
            with self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "FULL_BUNDLE_ATTESTATION_INVALID",
            ):
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    canonical_json_bytes(legacy_item),
                    config=local_bundle_config,  # type: ignore[arg-type]
                    now=NOW,
                )

            foreign_authority = Ed25519PrivateKey.generate()
            for forged_wire, expected_error in (
                (
                    self._sign_full_bundle(
                        projections,
                        authority_public_key_claim=_public(foreign_authority),
                    ),
                    "FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
                ),
                (
                    self._sign_full_bundle(projections, signer=foreign_authority),
                    "FULL_BUNDLE_ATTESTATION_SIGNATURE_INVALID",
                ),
                (
                    self._sign_full_bundle(
                        projections,
                        deployment_binding_sha256="b" * 64,
                    ),
                    "FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
                ),
                (
                    self._sign_full_bundle(projections, release_sha="f" * 40),
                    "FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
                ),
            ):
                with self.assertRaisesRegex(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                    expected_error,
                ):
                    dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                        forged_wire,
                        config=local_bundle_config,  # type: ignore[arg-type]
                        now=NOW,
                    )

            mismatched = [dict(item) for item in projections]
            mismatched[0]["retention_proof_sha256"] = "b" * 64
            typed_mismatch = (
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    self._sign_full_bundle(mismatched),
                    config=local_bundle_config,  # type: ignore[arg-type]
                    now=NOW,
                )
            )
            local = replace(
                configs["fi-writer-source-outbox"],
                full_bundle_attestation=typed_mismatch,
            )
            with patch.object(scope_module, "_new_s3v4_client", side_effect=self._fake_factory(fakes)) as factory, patch.object(
                scope_module, "_host_now", return_value=NOW
            ), patch.object(mailbox_adapter, "_host_now", return_value=NOW), patch.object(
                runtime, "_host_now", return_value=NOW
            ), patch.object(dispatcher, "_host_now", return_value=NOW), self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "LOCAL_CROSS_PIN_MISMATCH",
            ):
                self._opens()["fi-writer-source-outbox"](config=local, now=NOW)
            factory.assert_not_called()

    def test_expired_full_bundle_after_open_blocks_every_runtime_and_s3_effect(self) -> None:
        with ExitStack() as stack:
            configs, fakes, projections, _wire = self._bundle(stack)
            expiring_wire = self._sign_full_bundle(
                projections,
                expires_at=NOW + timedelta(seconds=5),
            )
            source_config = configs["fi-writer-source-outbox"]
            ingress_config = configs["witness-fi-ingress"]
            source_config = replace(
                source_config,
                full_bundle_attestation=(
                    dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                        expiring_wire,
                        config=source_config.full_bundle_attestation_config,  # type: ignore[arg-type]
                        now=NOW,
                    )
                ),
            )
            ingress_config = replace(
                ingress_config,
                full_bundle_attestation=(
                    dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                        expiring_wire,
                        config=ingress_config.full_bundle_attestation_config,  # type: ignore[arg-type]
                        now=NOW,
                    )
                ),
            )
            with patch.object(
                scope_module,
                "_new_s3v4_client",
                side_effect=self._fake_factory(fakes),
            ) as client_factory, patch.object(
                dispatcher, "_host_now", return_value=NOW
            ) as dispatcher_clock, patch.object(
                runtime,
                "publish_physical_wal_v2_witness_fi_to_witness_delivery",
            ) as publish, patch.object(
                runtime,
                "consume_physical_wal_v2_witness_fi_to_witness_delivery",
            ) as consume:
                source = self._opens()["fi-writer-source-outbox"](
                    config=source_config,
                    now=NOW,
                )
                ingress = self._opens()["witness-fi-ingress"](
                    config=ingress_config,
                    now=NOW,
                )
                dispatcher_clock.return_value = NOW + timedelta(seconds=6)
                with self.assertRaisesRegex(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                    "OPERATION_FRESHNESS_INVALID",
                ):
                    source.publish_fi_to_witness_delivery(
                        self.fixture.base.deliveries["fi-to-witness"]
                    )
                with self.assertRaisesRegex(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                    "OPERATION_FRESHNESS_INVALID",
                ):
                    ingress.consume_fi_to_witness_delivery()
            publish.assert_not_called()
            consume.assert_not_called()
            client_factory.assert_not_called()

    def test_dispatcher_public_surface_is_eight_fixed_local_openers_without_generic_route(self) -> None:
        source = inspect.getsource(dispatcher)
        tree = ast.parse(source)
        self.assertNotIn("physical_arvan_s3_", source)
        self.assertNotIn("fi-to-ir", source)
        self.assertNotIn("ir-to-fi", source)
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
        self.assertFalse(imports & {"boto3", "botocore", "requests", "socket", "urllib"})
        self.assertEqual(
            {
                "runtime_config",
                "scope_config",
                "full_bundle_attestation_config",
                "full_bundle_attestation",
                "enabled",
            },
            {
                item.name
                for item in fields(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherConfig
                )
            },
        )
        self.assertEqual(
            {
                "mailbox_adapter_config",
                "expected_release_sha",
                "enabled",
                "maximum_evidence_age_seconds",
            },
            {
                item.name
                for item in fields(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig
                )
            },
        )
        self.assertNotIn("authority_public_key: bytes", source)
        expected = {
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FiToWitnessPublisherDispatcher: {
                "publish_fi_to_witness_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiIngressDispatcher: {
                "consume_fi_to_witness_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrEgressDispatcher: {
                "publish_witness_to_ir_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4IrStandbyIngressDispatcher: {
                "consume_witness_to_ir_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4IrToWitnessPublisherDispatcher: {
                "publish_ir_to_witness_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessIrIngressDispatcher: {
                "consume_ir_to_witness_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4WitnessFiPublisherDispatcher: {
                "publish_witness_to_fi_delivery"
            },
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FiAckIngressDispatcher: {
                "consume_witness_to_fi_delivery"
            },
        }
        for dispatcher_type, methods in expected.items():
            public = {
                name
                for name, value in dispatcher_type.__dict__.items()
                if callable(value) and not name.startswith("_")
            }
            self.assertEqual(methods, public)
            for method_name in methods:
                method_source = inspect.getsource(getattr(dispatcher_type, method_name))
                self.assertIn("self._fresh_local_bundle_gate()", method_source)
        for opener in self._opens().values():
            self.assertEqual(("config", "now"), tuple(inspect.signature(opener).parameters))
