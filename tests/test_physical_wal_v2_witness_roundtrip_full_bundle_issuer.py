from __future__ import annotations

import ast
import base64
from contextlib import ExitStack
from dataclasses import fields, replace
from datetime import timedelta
import hashlib
import inspect
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher
from core import physical_wal_v2_witness_roundtrip_full_bundle_issuer as issuer
from tests import test_physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(authority: Ed25519PrivateKey) -> bytes:
    return authority.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _RootOwnedSigner:
    """Test double exposes only the production signer protocol method."""

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


class PhysicalWalV2WitnessRoundtripFullBundleIssuerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = Ed25519PrivateKey.generate()
        self.public = _public(self.authority)
        self.deployment_binding_sha256 = "a" * 64
        self.delivery_binding_sha256 = "b" * 64
        self.roundtrip_configuration_sha256 = "c" * 64
        self.release_sha = "d" * 40
        self.signing_config = (
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig(
                deployment_authority_public_key=self.public,
                expected_deployment_binding_sha256=self.deployment_binding_sha256,
                expected_delivery_binding_sha256=self.delivery_binding_sha256,
                expected_roundtrip_configuration_sha256=self.roundtrip_configuration_sha256,
                expected_release_sha=self.release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        projections = [
            self._projection(index=index, expected=expected)
            for index, expected in enumerate(dispatcher._ROLE_SPECS, start=1)
        ]
        self.request = (
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest(
                bundle_id="full-bundle-attestation-issuer-000001",
                bundle_nonce="B" * 22,
                issued_at=NOW,
                expires_at=NOW + timedelta(seconds=30),
                release_sha=self.release_sha,
                deployment_binding_sha256=self.deployment_binding_sha256,
                deployment_authority_public_key_sha256=hashlib.sha256(self.public).hexdigest(),
                delivery_binding_sha256=self.delivery_binding_sha256,
                roundtrip_configuration_sha256=self.roundtrip_configuration_sha256,
                fi_writer_source_outbox=projections[0],
                witness_fi_ingress=projections[1],
                witness_ir_egress=projections[2],
                ir_standby_ack_inbox=projections[3],
                ir_durable_ack_outbox=projections[4],
                witness_ir_ingress=projections[5],
                witness_fi_egress=projections[6],
                fi_writer_ack_inbox=projections[7],
                enabled=True,
            )
        )

    @staticmethod
    def _projection(*, index: int, expected: tuple[str, str, str, str]):
        local_role, mailbox, direction, object_prefix = expected
        return issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection(
            host_id=f"full-bundle-host-{index:04d}",
            local_role=local_role,
            mailbox=mailbox,
            direction=direction,
            object_prefix=object_prefix,
            admission_sha256=(format(index, "x") * 64)[:64],
            retention_proof_sha256=(format(index + 8, "x") * 64)[:64],
            provider_route_iam_attestation_sha256=(format(index + 1, "x") * 64)[:64],
        )

    def _prepare(self, request=None, config=None):
        return issuer.prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            request=self.request if request is None else request,
            signing_config=self.signing_config if config is None else config,
            now=NOW,
        )

    def test_prepare_then_root_owned_finalize_mints_canonical_public_bundle(self) -> None:
        prepared = self._prepare()
        signer = _RootOwnedSigner(self.authority)
        wire = issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            prepared,
            signer=signer,
            signing_config=self.signing_config,
            now=NOW,
        )
        self.assertEqual([prepared.signing_payload], signer.calls)
        item = json.loads(wire.decode("ascii"))
        self.assertEqual(wire, issuer._canonical(item, code="test"))
        self.assertEqual(dispatcher._FULL_BUNDLE_SCHEMA, item["schema"])
        self.assertEqual(self.release_sha, item["release_sha"])
        self.assertEqual(8, len(item["role_projections"]))
        self.assertEqual(
            tuple(expected[0] for expected in dispatcher._ROLE_SPECS),
            tuple(item["local_role"] for item in item["role_projections"]),
        )
        signature = base64.b64decode(item.pop("signature_base64"), validate=True)
        self.authority.public_key().verify(
            signature,
            dispatcher._FULL_BUNDLE_DOMAIN + issuer._canonical(item, code="test"),
        )
        self.assertEqual(
            self.deployment_binding_sha256,
            item["deployment_binding_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(self.public).hexdigest(),
            item["deployment_authority_public_key_sha256"],
        )
        for forbidden in (
            "access_key_id",
            "secret_access_key",
            "credential_root",
            "/etc/",
            "private",
        ):
            self.assertNotIn(forbidden, wire.decode("ascii"))
        with self.assertRaises(TypeError):
            prepared.__reduce_ex__(4)

    def test_signer_and_authority_pin_mismatch_fail_closed(self) -> None:
        prepared = self._prepare()
        wrong_signer = _RootOwnedSigner(Ed25519PrivateKey.generate())
        with self.assertRaisesRegex(
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
            "ISSUER_SIGNER_MISMATCH",
        ):
            issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                prepared,
                signer=wrong_signer,
                signing_config=self.signing_config,
                now=NOW,
            )
        self.assertEqual(1, len(wrong_signer.calls))

        foreign = Ed25519PrivateKey.generate()
        mismatched_config = replace(
            self.signing_config,
            deployment_authority_public_key=_public(foreign),
        )
        with self.assertRaisesRegex(
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
            "ISSUER_COMMON_PIN_MISMATCH",
        ):
            self._prepare(config=mismatched_config)

    def test_projection_unknown_missing_or_duplicate_role_is_rejected_before_signing(self) -> None:
        unknown = replace(
            self.request.witness_fi_ingress,
            local_role="unknown-role",
        )
        duplicate = replace(
            self.request.witness_fi_ingress,
            local_role="fi-writer-source-outbox",
        )
        for request in (
            replace(self.request, witness_fi_ingress=unknown),
            replace(self.request, witness_fi_ingress=duplicate),
            replace(self.request, fi_writer_ack_inbox=None),
        ):
            with self.assertRaisesRegex(
                issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
                "ISSUER_PROJECTION_INVALID",
            ):
                self._prepare(request=request)
        with self.assertRaises(TypeError):
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest(
                unknown_role=issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection()
            )

    def test_common_pin_or_prepared_projection_tamper_is_rejected_before_signer_effect(self) -> None:
        with self.assertRaisesRegex(
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
            "ISSUER_COMMON_PIN_MISMATCH",
        ):
            self._prepare(
                request=replace(self.request, delivery_binding_sha256="d" * 64)
            )
        with self.assertRaisesRegex(
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
            "ISSUER_COMMON_PIN_MISMATCH",
        ):
            self._prepare(request=replace(self.request, release_sha="e" * 40))

        prepared = self._prepare()
        parsed = json.loads(prepared.canonical_unsigned.decode("ascii"))
        parsed["role_projections"][3]["local_role"] = "fi-writer-source-outbox"
        tampered_unsigned = issuer._canonical(parsed, code="test")
        object.__setattr__(prepared, "canonical_unsigned", tampered_unsigned)
        object.__setattr__(
            prepared,
            "signing_payload",
            dispatcher._FULL_BUNDLE_DOMAIN + tampered_unsigned,
        )
        signer = _RootOwnedSigner(self.authority)
        with self.assertRaisesRegex(
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError,
            "ISSUER_PREPARED_INVALID",
        ):
            issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                prepared,
                signer=signer,
                signing_config=self.signing_config,
                now=NOW,
            )
        self.assertEqual([], signer.calls)

    def test_issued_wire_is_accepted_by_the_real_role_local_dispatcher_verifier(self) -> None:
        fixture = dispatcher_tests.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherTests(
            "runTest"
        )
        fixture.setUp()
        try:
            with ExitStack() as stack:
                configs, _fakes, projections, _wire = fixture._bundle(stack)
                public = _public(fixture.full_bundle_authority)
                signing_config = (
                    issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig(
                        deployment_authority_public_key=public,
                        expected_deployment_binding_sha256=projections[0][
                            "deployment_binding_sha256"
                        ],
                        expected_delivery_binding_sha256=projections[0]["delivery_binding_sha256"],
                        expected_roundtrip_configuration_sha256=projections[0][
                            "roundtrip_configuration_sha256"
                        ],
                        expected_release_sha=fixture.release_sha,
                        enabled=True,
                        maximum_evidence_age_seconds=60,
                    )
                )
                role_projections = [
                    issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection(
                        host_id=projection["host_id"],
                        local_role=projection["local_role"],
                        mailbox=projection["mailbox"],
                        direction=projection["direction"],
                        object_prefix=projection["object_prefix"],
                        admission_sha256=projection["admission_sha256"],
                        retention_proof_sha256=projection["retention_proof_sha256"],
                        provider_route_iam_attestation_sha256=projection[
                            "provider_route_iam_attestation_sha256"
                        ],
                    )
                    for projection in projections
                ]
                request = (
                    issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest(
                        bundle_id="full-bundle-attestation-issuer-000002",
                        bundle_nonce="I" * 22,
                        issued_at=NOW,
                        expires_at=NOW + timedelta(seconds=30),
                        release_sha=fixture.release_sha,
                        deployment_binding_sha256=projections[0][
                            "deployment_binding_sha256"
                        ],
                        deployment_authority_public_key_sha256=hashlib.sha256(public).hexdigest(),
                        delivery_binding_sha256=projections[0]["delivery_binding_sha256"],
                        roundtrip_configuration_sha256=projections[0][
                            "roundtrip_configuration_sha256"
                        ],
                        fi_writer_source_outbox=role_projections[0],
                        witness_fi_ingress=role_projections[1],
                        witness_ir_egress=role_projections[2],
                        ir_standby_ack_inbox=role_projections[3],
                        ir_durable_ack_outbox=role_projections[4],
                        witness_ir_ingress=role_projections[5],
                        witness_fi_egress=role_projections[6],
                        fi_writer_ack_inbox=role_projections[7],
                        enabled=True,
                    )
                )
                wire = issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                    issuer.prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
                        request=request,
                        signing_config=signing_config,
                        now=NOW,
                    ),
                    signer=_RootOwnedSigner(fixture.full_bundle_authority),
                    signing_config=signing_config,
                    now=NOW,
                )
                verified = dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                    wire,
                    config=configs[
                        "fi-writer-source-outbox"
                    ].full_bundle_attestation_config,
                    now=NOW,
                )
                self.assertEqual("full-bundle-attestation-issuer-000002", verified.bundle_id)
                self.assertEqual(8, len(verified.projections))
        finally:
            fixture.tearDown()

    def test_public_surface_is_pure_named_and_never_imports_private_key_or_transport(self) -> None:
        source = inspect.getsource(issuer)
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
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "http",
                "os",
                "pathlib",
                "requests",
                "socket",
                "subprocess",
                "urllib",
            }
        )
        for forbidden in (
            "Ed25519PrivateKey",
            "access_key_id",
            "secret_access_key",
            "credential_root",
            "fi-to-ir",
            "ir-to-fi",
            "getattr(",
            "role_selector",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(
            {
                "bundle_id",
                "bundle_nonce",
                "issued_at",
                "expires_at",
                "release_sha",
                "deployment_binding_sha256",
                "deployment_authority_public_key_sha256",
                "delivery_binding_sha256",
                "roundtrip_configuration_sha256",
                "fi_writer_source_outbox",
                "witness_fi_ingress",
                "witness_ir_egress",
                "ir_standby_ack_inbox",
                "ir_durable_ack_outbox",
                "witness_ir_ingress",
                "witness_fi_egress",
                "fi_writer_ack_inbox",
                "enabled",
            },
            {
                item.name
                for item in fields(
                    issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest
                )
            },
        )
        self.assertEqual(
            {
                "deployment_authority_public_key",
                "expected_release_sha",
                "expected_deployment_binding_sha256",
                "expected_delivery_binding_sha256",
                "expected_roundtrip_configuration_sha256",
                "enabled",
                "maximum_evidence_age_seconds",
            },
            {
                item.name
                for item in fields(
                    issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig
                )
            },
        )
        public_methods = {
            name
            for name, value in issuer.PhysicalWalV2WitnessRoundtripFullBundleDeploymentSigner.__dict__.items()
            if callable(value) and not name.startswith("_")
        }
        self.assertEqual(
            {"sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation"},
            public_methods,
        )
