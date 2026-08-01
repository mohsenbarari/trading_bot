from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as scope_module
from core import physical_wal_v2_witness_roundtrip_delivery_runtime as runtime
from core import physical_wal_v2_witness_roundtrip_full_bundle_issuer as issuer
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as mailbox_adapter
from tests import test_physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(authority: Ed25519PrivateKey) -> bytes:
    return authority.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _RootOwnedSigner:
    def __init__(self, authority: Ed25519PrivateKey) -> None:
        self._authority = authority
        self.calls: list[bytes] = []

    def sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes:
        self.calls.append(signing_payload)
        return self._authority.sign(signing_payload)


class PhysicalWalV2WitnessRoundtripIssuerDispatcherIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = dispatcher_tests.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherTests(
            "runTest"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    @staticmethod
    def _fake_factory(fakes):
        def factory(*, credentials, profile):
            del profile
            return fakes[credentials.local_role]

        return factory

    @staticmethod
    def _public_projection(value):
        return issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection(
            host_id=value["host_id"],
            local_role=value["local_role"],
            mailbox=value["mailbox"],
            direction=value["direction"],
            object_prefix=value["object_prefix"],
            admission_sha256=value["admission_sha256"],
            retention_proof_sha256=value["retention_proof_sha256"],
            provider_route_iam_attestation_sha256=value[
                "provider_route_iam_attestation_sha256"
            ],
        )

    def _issue(
        self,
        *,
        projections,
        expires_at,
        deployment_binding_sha256: str | None = None,
    ):
        authority = self.fixture.full_bundle_authority
        public = _public(authority)
        deployment_binding_sha256 = (
            projections[0]["deployment_binding_sha256"]
            if deployment_binding_sha256 is None
            else deployment_binding_sha256
        )
        signing_config = (
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig(
                deployment_authority_public_key=public,
                expected_deployment_binding_sha256=deployment_binding_sha256,
                expected_delivery_binding_sha256=projections[0]["delivery_binding_sha256"],
                expected_roundtrip_configuration_sha256=projections[0][
                    "roundtrip_configuration_sha256"
                ],
                expected_release_sha=self.fixture.release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        public_projections = [self._public_projection(value) for value in projections]
        request = issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest(
            bundle_id="full-bundle-attestation-integration-000001",
            bundle_nonce="I" * 22,
            issued_at=NOW,
            expires_at=expires_at,
            release_sha=self.fixture.release_sha,
            deployment_binding_sha256=deployment_binding_sha256,
            deployment_authority_public_key_sha256=hashlib.sha256(public).hexdigest(),
            delivery_binding_sha256=projections[0]["delivery_binding_sha256"],
            roundtrip_configuration_sha256=projections[0]["roundtrip_configuration_sha256"],
            fi_writer_source_outbox=public_projections[0],
            witness_fi_ingress=public_projections[1],
            witness_ir_egress=public_projections[2],
            ir_standby_ack_inbox=public_projections[3],
            ir_durable_ack_outbox=public_projections[4],
            witness_ir_ingress=public_projections[5],
            witness_fi_egress=public_projections[6],
            fi_writer_ack_inbox=public_projections[7],
            enabled=True,
        )
        prepared = issuer.prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            request=request,
            signing_config=signing_config,
            now=NOW,
        )
        return (
            issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                prepared,
                signer=_RootOwnedSigner(authority),
                signing_config=signing_config,
                now=NOW,
            ),
            signing_config,
            request,
        )

    def _issued_configs(self, configs, wire):
        return {
            role: replace(
                config,
                full_bundle_attestation=(
                    dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                        wire,
                        config=config.full_bundle_attestation_config,
                        now=NOW,
                    )
                ),
            )
            for role, config in configs.items()
        }

    def test_issued_bundle_is_accepted_by_real_publisher_and_consumer_dispatchers(self) -> None:
        with ExitStack() as stack:
            configs, fakes, projections, _old_wire = self.fixture._bundle(stack)
            wire, _signing_config, _request = self._issue(
                projections=projections,
                expires_at=NOW + timedelta(seconds=30),
            )
            configs = self._issued_configs(configs, wire)
            with patch.object(
                scope_module,
                "_new_s3v4_client",
                side_effect=self._fake_factory(fakes),
            ), patch.object(scope_module, "_host_now", return_value=NOW), patch.object(
                mailbox_adapter, "_host_now", return_value=NOW
            ), patch.object(runtime, "_host_now", return_value=NOW), patch.object(
                dispatcher, "_host_now", return_value=NOW):
                publisher = dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher(
                    config=configs["fi-writer-source-outbox"],
                    now=NOW,
                )
                consumer = dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher(
                    config=configs["witness-fi-ingress"],
                    now=NOW,
                )
                self.assertEqual(
                    "published",
                    publisher.publish_fi_to_witness_delivery(
                        self.fixture.fixture.base.deliveries["fi-to-witness"]
                    ).status,
                )
                self.assertEqual("consumed", consumer.consume_fi_to_witness_delivery()[0].status)
            self.assertEqual(
                ["put_object", "head_object", "get_object"],
                [name for name, _kwargs in fakes["fi-writer-source-outbox"].calls],
            )
            self.assertEqual(
                ["list_object_versions", "head_object", "head_object", "get_object"],
                [name for name, _kwargs in fakes["witness-fi-ingress"].calls],
            )

    def test_signer_tamper_and_deployment_binding_mismatch_fail_before_s3(self) -> None:
        with ExitStack() as stack:
            configs, fakes, projections, _old_wire = self.fixture._bundle(stack)
            wire, signing_config, request = self._issue(
                projections=projections,
                expires_at=NOW + timedelta(seconds=30),
            )
            wrong_signer = _RootOwnedSigner(Ed25519PrivateKey.generate())
            prepared = issuer.prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                request=request,
                signing_config=signing_config,
                now=NOW,
            )
            with self.assertRaisesRegex(
                issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
                "ISSUER_SIGNER_MISMATCH",
            ):
                issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                    prepared,
                    signer=wrong_signer,
                    signing_config=signing_config,
                    now=NOW,
                )

            modified = json.loads(wire.decode("ascii"))
            modified["role_projections"][0]["admission_sha256"] = "f" * 64
            tampered_wire = issuer._canonical(modified, code="test")
            with patch.object(
                scope_module,
                "_new_s3v4_client",
                side_effect=self._fake_factory(fakes),
            ) as client_factory, self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "FULL_BUNDLE_ATTESTATION_SIGNATURE_INVALID",
            ):
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    tampered_wire,
                    config=configs[
                        "fi-writer-source-outbox"
                    ].full_bundle_attestation_config,
                    now=NOW,
                )
            client_factory.assert_not_called()

            wrong_deployment_wire, _wrong_config, _wrong_request = self._issue(
                projections=projections,
                expires_at=NOW + timedelta(seconds=30),
                deployment_binding_sha256="f" * 64,
            )
            with patch.object(
                scope_module,
                "_new_s3v4_client",
                side_effect=self._fake_factory(fakes),
            ) as client_factory, self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "FULL_BUNDLE_ATTESTATION_CROSS_PIN_MISMATCH",
            ):
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    wrong_deployment_wire,
                    config=configs[
                        "fi-writer-source-outbox"
                    ].full_bundle_attestation_config,
                    now=NOW,
                )
            client_factory.assert_not_called()

    def test_expired_after_open_and_valid_route_substitution_fail_before_runtime_or_s3(self) -> None:
        with ExitStack() as stack:
            configs, fakes, projections, _old_wire = self.fixture._bundle(stack)
            expiring_wire, _signing_config, _request = self._issue(
                projections=projections,
                expires_at=NOW + timedelta(seconds=5),
            )
            expiring_configs = self._issued_configs(configs, expiring_wire)
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
                publisher = dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher(
                    config=expiring_configs["fi-writer-source-outbox"],
                    now=NOW,
                )
                consumer = dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_witness_fi_ingress_dispatcher(
                    config=expiring_configs["witness-fi-ingress"],
                    now=NOW,
                )
                dispatcher_clock.return_value = NOW + timedelta(seconds=6)
                with self.assertRaisesRegex(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                    "OPERATION_FRESHNESS_INVALID",
                ):
                    publisher.publish_fi_to_witness_delivery(
                        self.fixture.fixture.base.deliveries["fi-to-witness"]
                    )
                with self.assertRaisesRegex(
                    dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                    "OPERATION_FRESHNESS_INVALID",
                ):
                    consumer.consume_fi_to_witness_delivery()
            publish.assert_not_called()
            consume.assert_not_called()
            client_factory.assert_not_called()

            route_substituted = [dict(value) for value in projections]
            route_substituted[0]["provider_route_iam_attestation_sha256"] = "f" * 64
            route_wire, _signing_config, _request = self._issue(
                projections=route_substituted,
                expires_at=NOW + timedelta(seconds=30),
            )
            route_typed = (
                dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    route_wire,
                    config=configs[
                        "fi-writer-source-outbox"
                    ].full_bundle_attestation_config,
                    now=NOW,
                )
            )
            route_config = replace(
                configs["fi-writer-source-outbox"],
                full_bundle_attestation=route_typed,
            )
            with patch.object(
                scope_module,
                "_new_s3v4_client",
                side_effect=self._fake_factory(fakes),
            ) as client_factory, patch.object(scope_module, "_host_now", return_value=NOW), patch.object(
                mailbox_adapter, "_host_now", return_value=NOW
            ), patch.object(runtime, "_host_now", return_value=NOW), patch.object(
                dispatcher, "_host_now", return_value=NOW
            ), self.assertRaisesRegex(
                dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError,
                "LOCAL_CROSS_PIN_MISMATCH",
            ):
                dispatcher.open_physical_wal_v2_witness_roundtrip_arvan_s3v4_fi_to_witness_publisher_dispatcher(
                    config=route_config,
                    now=NOW,
                )
            client_factory.assert_not_called()
