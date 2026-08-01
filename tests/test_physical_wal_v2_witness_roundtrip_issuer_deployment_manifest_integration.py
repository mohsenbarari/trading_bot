from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher
from core import physical_wal_v2_witness_roundtrip_delivery_contract as delivery
from core import physical_wal_v2_witness_roundtrip_deployment_plan as deployment
from core import physical_wal_v2_witness_roundtrip_full_bundle_deployment_reference as reference_bridge
from core import physical_wal_v2_witness_roundtrip_full_bundle_issuer as issuer
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as mailbox_adapter
from tests import test_physical_wal_v2_witness_roundtrip_deployment_plan as deployment_tests
from tests import test_physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as adapter_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(authority: Ed25519PrivateKey) -> bytes:
    return authority.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _RootOwnedSigner:
    def __init__(self, authority: Ed25519PrivateKey) -> None:
        self._authority = authority

    def sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes:
        return self._authority.sign(signing_payload)


class PhysicalWalV2WitnessRoundtripIssuerDeploymentManifestIntegrationTests(
    unittest.TestCase
):
    """Keep the issuer→plan hand-off public, typed, and non-operational."""

    def setUp(self) -> None:
        self.mailbox_fixture = (
            adapter_tests.PhysicalWalV2WitnessRoundtripS3MailboxAdapterTests("runTest")
        )
        self.mailbox_fixture.setUp()
        self.deployment_fixture = (
            deployment_tests.PhysicalWalV2WitnessRoundtripDeploymentPlanTests("runTest")
        )
        self.deployment_fixture.setUp()
        self.projections, self.local_mailbox_configs = self._public_role_material()
        self.release_sha = delivery._config(
            self.local_mailbox_configs["fi-writer-source-outbox"].delivery_config,
            mailbox="fi-to-witness",
        ).binding.release_sha
        # The deployment fixture deliberately uses synthetic public pins.  For
        # this issuer→manifest integration, make its release pin the exact
        # release bound to the local delivery evidence.
        self.deployment_fixture.config = replace(
            self.deployment_fixture.config,
            release_sha=self.release_sha,
            full_bundle_reference=replace(
                self.deployment_fixture.config.full_bundle_reference,
                release_sha=self.release_sha,
            ),
        )
        self.verification_config = (
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig(
                mailbox_adapter_config=self.local_mailbox_configs[
                    "fi-writer-source-outbox"
                ],
                expected_release_sha=self.release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )

    def tearDown(self) -> None:
        self.mailbox_fixture.tearDown()

    def _public_role_material(self):
        projections: list[dict[str, str]] = []
        configs = {}
        for index, policy in enumerate(
            admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
        ):
            # This absolute path is deliberately never created or opened: full
            # bundle verification validates admissions/retention only, and no
            # dispatcher, adapter, or runtime is opened in this test.
            config = self.mailbox_fixture._bundle(
                policy,
                root=Path(f"/v2-nonoperational-reference-fixture/{index}"),
                write_credential=False,
            )
            mailbox_facts = mailbox_adapter._config(
                config,
                local_role=policy.local_role,
                direction=policy.direction,
                now=NOW,
            )
            delivery_facts = delivery._config(
                config.delivery_config,
                mailbox=policy.mailbox,
            )
            projections.append(
                {
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
                    "provider_route_iam_attestation_sha256": hashlib.sha256(
                        f"public-route-attestation-{policy.local_role}".encode("ascii")
                    ).hexdigest(),
                    "roundtrip_configuration_sha256": (
                        delivery_facts.binding.roundtrip_configuration_sha256
                    ),
                }
            )
            configs[policy.local_role] = config
        return projections, configs

    @staticmethod
    def _issuer_projection(value: dict[str, str]):
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
        bundle_id: str,
        bundle_nonce: str,
        projections: list[dict[str, str]] | None = None,
        release_sha: str | None = None,
        expires_at=NOW + timedelta(seconds=30),
    ) -> bytes:
        projections = self.projections if projections is None else projections
        release_sha = self.release_sha if release_sha is None else release_sha
        authority = self.mailbox_fixture.authority
        authority_public_key = _public(authority)
        signing_config = (
            issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig(
                deployment_authority_public_key=authority_public_key,
                expected_deployment_binding_sha256=projections[0][
                    "deployment_binding_sha256"
                ],
                expected_delivery_binding_sha256=projections[0][
                    "delivery_binding_sha256"
                ],
                expected_roundtrip_configuration_sha256=projections[0][
                    "roundtrip_configuration_sha256"
                ],
                expected_release_sha=release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        (
            fi_writer_source_outbox,
            witness_fi_ingress,
            witness_ir_egress,
            ir_standby_ack_inbox,
            ir_durable_ack_outbox,
            witness_ir_ingress,
            witness_fi_egress,
            fi_writer_ack_inbox,
        ) = tuple(self._issuer_projection(value) for value in projections)
        request = issuer.PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest(
            bundle_id=bundle_id,
            bundle_nonce=bundle_nonce,
            issued_at=NOW,
            expires_at=expires_at,
            release_sha=release_sha,
            deployment_binding_sha256=projections[0]["deployment_binding_sha256"],
            deployment_authority_public_key_sha256=hashlib.sha256(
                authority_public_key
            ).hexdigest(),
            delivery_binding_sha256=projections[0]["delivery_binding_sha256"],
            roundtrip_configuration_sha256=projections[0][
                "roundtrip_configuration_sha256"
            ],
            fi_writer_source_outbox=fi_writer_source_outbox,
            witness_fi_ingress=witness_fi_ingress,
            witness_ir_egress=witness_ir_egress,
            ir_standby_ack_inbox=ir_standby_ack_inbox,
            ir_durable_ack_outbox=ir_durable_ack_outbox,
            witness_ir_ingress=witness_ir_ingress,
            witness_fi_egress=witness_fi_egress,
            fi_writer_ack_inbox=fi_writer_ack_inbox,
            enabled=True,
        )
        prepared = issuer.prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            request=request,
            signing_config=signing_config,
            now=NOW,
        )
        return issuer.finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            prepared,
            signer=_RootOwnedSigner(authority),
            signing_config=signing_config,
            now=NOW,
        )

    def _reference_from_issued_bundle(self, *, bundle_id: str, bundle_nonce: str):
        wire = self._issue(bundle_id=bundle_id, bundle_nonce=bundle_nonce)
        reference = (
            reference_bridge.derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
                wire,
                verification_config=self.verification_config,
                now=NOW,
            )
        )
        return wire, reference

    def _render(self, reference, *, plan_id=None, release_sha=None):
        return deployment.render_physical_wal_v2_witness_roundtrip_deployment_plan(
            config=replace(
                self.deployment_fixture.config,
                plan_id=(
                    self.deployment_fixture.config.plan_id
                    if plan_id is None
                    else plan_id
                ),
                release_sha=(
                    reference.release_sha
                    if release_sha is None
                    else release_sha
                ),
                full_bundle_reference=reference,
            )
        )

    @staticmethod
    def _admission(manifest: bytes, *, rendered):
        return deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig(
            expected_plan_id=rendered.plan_id,
            expected_release_sha=rendered.release_sha,
            expected_full_bundle_reference=rendered.full_bundle_reference,
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            enabled=True,
        )

    def _admissions(self, rendered):
        return (
            self._admission(rendered.wa_fi_service_manifest, rendered=rendered),
            self._admission(rendered.wa_ir_service_manifest, rendered=rendered),
            self._admission(rendered.witness_service_manifest, rendered=rendered),
        )

    @staticmethod
    def _require_all_named(rendered, admissions):
        wa_fi_admission, wa_ir_admission, witness_admission = admissions
        return (
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                rendered.wa_fi_service_manifest,
                config=wa_fi_admission,
            ),
            deployment.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_admission(
                rendered.wa_ir_service_manifest,
                config=wa_ir_admission,
            ),
            deployment.require_physical_wal_v2_witness_roundtrip_witness_service_manifest_admission(
                rendered.witness_service_manifest,
                config=witness_admission,
            ),
        )

    def _assert_all_named_cross_pin_rejections(self, rendered, admissions) -> None:
        wa_fi_admission, wa_ir_admission, witness_admission = admissions
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                rendered.wa_fi_service_manifest,
                config=replace(
                    wa_fi_admission,
                    expected_manifest_sha256=hashlib.sha256(
                        rendered.wa_fi_service_manifest
                    ).hexdigest(),
                ),
            )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_admission(
                rendered.wa_ir_service_manifest,
                config=replace(
                    wa_ir_admission,
                    expected_manifest_sha256=hashlib.sha256(
                        rendered.wa_ir_service_manifest
                    ).hexdigest(),
                ),
            )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_witness_service_manifest_admission(
                rendered.witness_service_manifest,
                config=replace(
                    witness_admission,
                    expected_manifest_sha256=hashlib.sha256(
                        rendered.witness_service_manifest
                    ).hexdigest(),
                ),
            )

    def test_issued_full_bundle_derives_exact_reference_and_admits_all_three_named_manifests(
        self,
    ) -> None:
        wire, reference = self._reference_from_issued_bundle(
            bundle_id="full-bundle-attestation-plan-000001",
            bundle_nonce="P" * 22,
        )
        self.assertEqual("full-bundle-attestation-plan-000001", reference.bundle_id)
        self.assertEqual(self.release_sha, reference.release_sha)
        self.assertEqual(hashlib.sha256(wire).hexdigest(), reference.full_bundle_attestation_sha256)
        self.assertEqual(
            self.projections[0]["deployment_binding_sha256"],
            reference.deployment_binding_sha256,
        )
        self.assertEqual(
            hashlib.sha256(_public(self.mailbox_fixture.authority)).hexdigest(),
            reference.deployment_authority_public_key_sha256,
        )
        self.assertEqual(
            self.projections[0]["roundtrip_configuration_sha256"],
            reference.roundtrip_configuration_sha256,
        )

        rendered = self._render(reference)
        admitted = self._require_all_named(rendered, self._admissions(rendered))
        self.assertEqual(("wa-fi", "wa-ir", "witness"), tuple(item.site for item in admitted))
        self.assertTrue(
            all(
                item.full_bundle_reference == reference
                and item.release_sha == rendered.release_sha
                and item.plan_id == rendered.plan_id
                for item in admitted
            )
        )

    def test_valid_cross_release_cross_plan_and_issued_bundle_substitutions_fail_at_all_named_admissions(
        self,
    ) -> None:
        _wire, reference = self._reference_from_issued_bundle(
            bundle_id="full-bundle-attestation-plan-000001",
            bundle_nonce="P" * 22,
        )
        baseline = self._render(reference)
        baseline_admissions = self._admissions(baseline)

        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "DEPLOYMENT_RELEASE_CROSS_PIN_MISMATCH",
        ):
            self._render(reference, release_sha="f" * 40)
        self._assert_all_named_cross_pin_rejections(
            self._render(reference, plan_id="v2-deployment-plan-000002"),
            baseline_admissions,
        )

        _cross_bundle_wire, cross_bundle_reference = self._reference_from_issued_bundle(
            bundle_id="full-bundle-attestation-plan-000002",
            bundle_nonce="Q" * 22,
        )
        self._assert_all_named_cross_pin_rejections(
            self._render(cross_bundle_reference),
            baseline_admissions,
        )

        # A separately signed wire with the same public bundle id but a new
        # nonce has a different canonical SHA-256.  This models replacement of
        # artifact bytes behind a stable external bundle label.
        _substituted_wire, substituted_reference = self._reference_from_issued_bundle(
            bundle_id="full-bundle-attestation-plan-000001",
            bundle_nonce="R" * 22,
        )
        self.assertEqual(reference.bundle_id, substituted_reference.bundle_id)
        self.assertNotEqual(
            reference.full_bundle_attestation_sha256,
            substituted_reference.full_bundle_attestation_sha256,
        )
        self._assert_all_named_cross_pin_rejections(
            self._render(substituted_reference),
            baseline_admissions,
        )

        deployment_pin_substitution = replace(
            reference,
            deployment_binding_sha256="f" * 64,
        )
        self._assert_all_named_cross_pin_rejections(
            self._render(deployment_pin_substitution),
            baseline_admissions,
        )

    def test_bridge_rejects_noncanonical_or_tampered_wire_before_any_plan_rendering(self) -> None:
        wire, _reference = self._reference_from_issued_bundle(
            bundle_id="full-bundle-attestation-plan-000001",
            bundle_nonce="P" * 22,
        )
        tampered = wire.replace(b"plan-000001", b"plan-000009", 1)
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "FULL_BUNDLE_INVALID",
        ):
            reference_bridge.derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
                tampered,
                verification_config=self.verification_config,
                now=NOW,
            )

        source = inspect.getsource(reference_bridge)
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
