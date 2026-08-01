from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import timedelta
import hashlib
import inspect
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as dispatcher
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as scope_module
from core import physical_wal_v2_witness_roundtrip_deployment_plan as deployment
from core import physical_wal_v2_witness_roundtrip_full_bundle_deployment_reference as reference_bridge
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from core import physical_wal_v2_witness_roundtrip_s3_mailbox_adapter as mailbox_adapter
from tests import test_physical_wal_v2_witness_roundtrip_issuer_deployment_manifest_integration as integration_tests
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


class _WireLookalike:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw


class _PublicReferenceLookalike:
    def __init__(self, value) -> None:
        self.bundle_id = value.bundle_id
        self.release_sha = value.release_sha
        self.full_bundle_attestation_sha256 = value.full_bundle_attestation_sha256
        self.deployment_binding_sha256 = value.deployment_binding_sha256
        self.deployment_authority_public_key_sha256 = (
            value.deployment_authority_public_key_sha256
        )
        self.roundtrip_configuration_sha256 = value.roundtrip_configuration_sha256


class _ManifestAdmissionConfigLookalike:
    def __init__(self, value) -> None:
        self.expected_plan_id = value.expected_plan_id
        self.expected_release_sha = value.expected_release_sha
        self.expected_full_bundle_reference = value.expected_full_bundle_reference
        self.expected_manifest_sha256 = value.expected_manifest_sha256
        self.enabled = value.enabled


class PhysicalWalV2WitnessRoundtripIssuerDeploymentManifestAdversarialTests(
    unittest.TestCase
):
    """Attack only the non-operational issuer→manifest evidence hand-off."""

    def setUp(self) -> None:
        self.fixture = (
            integration_tests.PhysicalWalV2WitnessRoundtripIssuerDeploymentManifestIntegrationTests(
                "runTest"
            )
        )
        self.fixture.setUp()
        local_configs = self.fixture.local_mailbox_configs
        self.wa_fi_verification_config = self.fixture.verification_config
        self.wa_ir_verification_config = (
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig(
                mailbox_adapter_config=local_configs["ir-standby-ack-inbox"],
                expected_release_sha=self.fixture.release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        self.witness_verification_config = (
            dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig(
                mailbox_adapter_config=local_configs["witness-fi-ingress"],
                expected_release_sha=self.fixture.release_sha,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        self.route_material_by_role = {
            policy.local_role: self._route_material(policy)
            for policy in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
        }
        self.route_pinned_projections = [
            dict(value) for value in self.fixture.projections
        ]
        for projection in self.route_pinned_projections:
            _route_config, route_attestation = self.route_material_by_role[
                projection["local_role"]
            ]
            projection["provider_route_iam_attestation_sha256"] = (
                route_attestation.attestation_sha256
            )

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _route_material(self, policy):
        """Issue local typed route evidence in memory; never open a profile/client."""

        config = self.fixture.local_mailbox_configs[policy.local_role]
        route_authority = Ed25519PrivateKey.generate()
        route_public_key = route_authority.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        route_config = (
            scope_module.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig(
                mailbox_adapter_config=config,
                provider_route_iam_authority_public_key=route_public_key,
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        facts = mailbox_adapter._config(
            config,
            local_role=policy.local_role,
            direction=policy.direction,
            now=NOW,
        )
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
            "allowed_s3_actions": list(
                scope_module._route_actions(direction=policy.direction)
            ),
            "admission_sha256": facts.mailbox_admission.admission_sha256,
            "deployment_binding_sha256": (
                facts.mailbox_admission.deployment_binding_sha256
            ),
            "delivery_binding_sha256": facts.delivery_binding_sha256,
            "retention_proof_sha256": facts.retention_proof.proof_sha256,
            "attestation_id": "provider-route-iam-audit-000001",
            "attestation_nonce": "T" * 22,
            "issued_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (NOW + timedelta(seconds=35)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        wire = canonical_json_bytes(
            {
                **unsigned,
                "signature_base64": base64.b64encode(
                    route_authority.sign(
                        scope_module._ROUTE_IAM_DOMAIN
                        + canonical_json_bytes(unsigned)
                    )
                ).decode("ascii"),
            }
        )
        return (
            route_config,
            scope_module.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
                wire,
                config=route_config,
                local_role=policy.local_role,
                direction=policy.direction,
                now=NOW,
            ),
        )

    def _route_material_for(self, local_role: str):
        return self.route_material_by_role[local_role]

    def _route_kwargs(self, local_role: str):
        config, attestation = self._route_material_for(local_role)
        return {
            "provider_route_iam_attestation_config": config,
            "provider_route_iam_attestation": attestation,
        }

    def _baseline(self):
        wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000001",
            bundle_nonce="A" * 22,
            projections=self.route_pinned_projections,
        )
        reference = (
            reference_bridge.derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
                wire,
                verification_config=self.wa_fi_verification_config,
                now=NOW,
            )
        )
        rendered = self.fixture._render(reference)
        return wire, reference, rendered, self.fixture._admissions(rendered)

    def _admit_three_fresh(self, *, wire, rendered, admissions, fi_config, ir_config, witness_config, now):
        wa_fi_admission, wa_ir_admission, witness_admission = admissions
        fi_route_config, fi_route_attestation = self._route_material_for(
            "fi-writer-source-outbox"
        )
        ir_route_config, ir_route_attestation = self._route_material_for(
            "ir-standby-ack-inbox"
        )
        witness_route_config, witness_route_attestation = self._route_material_for(
            "witness-fi-ingress"
        )
        return (
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_fi_service_manifest,
                manifest_admission_config=wa_fi_admission,
                full_bundle_attestation=wire,
                verification_config=fi_config,
                provider_route_iam_attestation_config=fi_route_config,
                provider_route_iam_attestation=fi_route_attestation,
                now=now,
            ),
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_ir_service_manifest,
                manifest_admission_config=wa_ir_admission,
                full_bundle_attestation=wire,
                verification_config=ir_config,
                provider_route_iam_attestation_config=ir_route_config,
                provider_route_iam_attestation=ir_route_attestation,
                now=now,
            ),
            reference_bridge.require_physical_wal_v2_witness_roundtrip_witness_service_manifest_with_fresh_full_bundle_admission(
                rendered.witness_service_manifest,
                manifest_admission_config=witness_admission,
                full_bundle_attestation=wire,
                verification_config=witness_config,
                provider_route_iam_attestation_config=witness_route_config,
                provider_route_iam_attestation=witness_route_attestation,
                now=now,
            ),
        )

    def test_fresh_signed_wire_is_required_at_each_named_site_and_expiry_fails_closed(self) -> None:
        wire, reference, rendered, admissions = self._baseline()
        admitted = self._admit_three_fresh(
            wire=wire,
            rendered=rendered,
            admissions=admissions,
            fi_config=self.wa_fi_verification_config,
            ir_config=self.wa_ir_verification_config,
            witness_config=self.witness_verification_config,
            now=NOW,
        )
        self.assertEqual(("wa-fi", "wa-ir", "witness"), tuple(item.site for item in admitted))
        self.assertTrue(
            all(item.full_bundle_reference == reference for item in admitted)
        )

        wa_fi_admission, wa_ir_admission, witness_admission = admissions
        expired_now = NOW + timedelta(seconds=31)
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "FULL_BUNDLE_INVALID",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_fi_service_manifest,
                manifest_admission_config=wa_fi_admission,
                full_bundle_attestation=wire,
                verification_config=self.wa_fi_verification_config,
                **self._route_kwargs("fi-writer-source-outbox"),
                now=expired_now,
            )
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "FULL_BUNDLE_INVALID",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_ir_service_manifest,
                manifest_admission_config=wa_ir_admission,
                full_bundle_attestation=wire,
                verification_config=self.wa_ir_verification_config,
                **self._route_kwargs("ir-standby-ack-inbox"),
                now=expired_now,
            )
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "FULL_BUNDLE_INVALID",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_witness_service_manifest_with_fresh_full_bundle_admission(
                rendered.witness_service_manifest,
                manifest_admission_config=witness_admission,
                full_bundle_attestation=wire,
                verification_config=self.witness_verification_config,
                **self._route_kwargs("witness-fi-ingress"),
                now=expired_now,
            )

    def test_second_valid_bundle_and_hash_only_substitution_cannot_admit_existing_manifest(self) -> None:
        wire, reference, rendered, admissions = self._baseline()
        wa_fi_admission, wa_ir_admission, witness_admission = admissions

        different_id_wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000002",
            bundle_nonce="B" * 22,
            projections=self.route_pinned_projections,
        )
        same_id_new_nonce_wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000001",
            bundle_nonce="C" * 22,
            projections=self.route_pinned_projections,
        )
        same_id_same_nonce_new_expiry_wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000001",
            bundle_nonce="A" * 22,
            projections=self.route_pinned_projections,
            expires_at=NOW + timedelta(seconds=35),
        )
        self.assertNotEqual(wire, same_id_same_nonce_new_expiry_wire)
        for candidate in (
            different_id_wire,
            same_id_new_nonce_wire,
            same_id_same_nonce_new_expiry_wire,
        ):
            with self.assertRaisesRegex(
                reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
                "MANIFEST_FULL_BUNDLE_CROSS_PIN_MISMATCH",
            ):
                reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                    rendered.wa_fi_service_manifest,
                    manifest_admission_config=wa_fi_admission,
                    full_bundle_attestation=candidate,
                    verification_config=self.wa_fi_verification_config,
                    **self._route_kwargs("fi-writer-source-outbox"),
                    now=NOW,
                )

        hash_only_reference = replace(
            reference,
            full_bundle_attestation_sha256="f" * 64,
        )
        hash_only_rendered = self.fixture._render(hash_only_reference)
        hash_only_admissions = self.fixture._admissions(hash_only_rendered)
        self.assertEqual(
            "wa-fi",
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                hash_only_rendered.wa_fi_service_manifest,
                config=hash_only_admissions[0],
            ).site,
        )
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "MANIFEST_FULL_BUNDLE_CROSS_PIN_MISMATCH",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                hash_only_rendered.wa_fi_service_manifest,
                manifest_admission_config=hash_only_admissions[0],
                full_bundle_attestation=wire,
                verification_config=self.wa_fi_verification_config,
                **self._route_kwargs("fi-writer-source-outbox"),
                now=NOW,
            )

        # The other site-specific admission configs remain intentionally
        # unused by the FI-only substitutions above; access them only to make
        # accidental tuple/order changes visible to the test.
        self.assertEqual("v2-deployment-plan-000001", wa_ir_admission.expected_plan_id)
        self.assertEqual("v2-deployment-plan-000001", witness_admission.expected_plan_id)

    def test_canonical_raw_manifest_cannot_relabel_the_signed_bundle_release(self) -> None:
        """A recomputed render lock never makes an outer release authoritative."""

        _wire, reference, rendered, _admissions = self._baseline()
        relabelled = json.loads(rendered.wa_fi_service_manifest.decode("ascii"))
        relabelled["release_sha"] = "f" * 40
        unsigned = dict(relabelled)
        unsigned.pop("render_lock_sha256")
        relabelled["render_lock_sha256"] = hashlib.sha256(
            deployment._canonical(unsigned, code="test")
        ).hexdigest()
        canonical_relabelled = deployment._canonical(relabelled, code="test")

        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_RELEASE_CROSS_PIN_MISMATCH",
        ):
            deployment.parse_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest(
                canonical_relabelled
            )

        # Match the forged manifest's hash and outer label in the root config
        # too.  The root admission still rejects before any fresh-bundle bridge
        # could treat the manifest as an operationally usable evidence input.
        inconsistent_admission = (
            deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig(
                expected_plan_id=rendered.plan_id,
                expected_release_sha="f" * 40,
                expected_full_bundle_reference=reference,
                expected_manifest_sha256=hashlib.sha256(canonical_relabelled).hexdigest(),
                enabled=True,
            )
        )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "MANIFEST_ADMISSION_CONFIG_RELEASE_CROSS_PIN_MISMATCH",
        ):
            deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                canonical_relabelled,
                config=inconsistent_admission,
            )

    def test_lookalikes_and_cross_site_projection_are_rejected_without_operational_surface(self) -> None:
        wire, reference, rendered, admissions = self._baseline()
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "FULL_BUNDLE_INVALID",
        ):
            reference_bridge.derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
                _WireLookalike(wire),  # type: ignore[arg-type]
                verification_config=self.wa_fi_verification_config,
                now=NOW,
            )
        with self.assertRaisesRegex(
            deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError,
            "FULL_BUNDLE_REFERENCE_INVALID",
        ):
            self.fixture._render(_PublicReferenceLookalike(reference))

        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "MANIFEST_ADMISSION_INVALID",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_fi_service_manifest,
                manifest_admission_config=_ManifestAdmissionConfigLookalike(
                    admissions[0]
                ),
                full_bundle_attestation=wire,
                verification_config=self.wa_fi_verification_config,
                **self._route_kwargs("fi-writer-source-outbox"),
                now=NOW,
            )

        cross_site_projections = [dict(value) for value in self.route_pinned_projections]
        cross_site_projections[0]["host_id"] = "cross-site-substitute-host-0001"
        cross_site_projections[0]["admission_sha256"] = cross_site_projections[3][
            "admission_sha256"
        ]
        cross_site_wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000003",
            bundle_nonce="D" * 22,
            projections=cross_site_projections,
        )
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "LOCAL_CROSS_PIN_MISMATCH",
        ):
            reference_bridge.derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
                cross_site_wire,
                verification_config=self.wa_fi_verification_config,
                now=NOW,
            )

        route_only_substitution = [
            dict(value) for value in self.route_pinned_projections
        ]
        route_only_substitution[0]["provider_route_iam_attestation_sha256"] = "f" * 64
        route_only_wire = self.fixture._issue(
            bundle_id="full-bundle-attestation-audit-000004",
            bundle_nonce="E" * 22,
            projections=route_only_substitution,
        )
        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "LOCAL_ROUTE_IAM_CROSS_PIN_MISMATCH",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_fi_service_manifest,
                manifest_admission_config=admissions[0],
                full_bundle_attestation=route_only_wire,
                verification_config=self.wa_fi_verification_config,
                **self._route_kwargs("fi-writer-source-outbox"),
                now=NOW,
            )

        with self.assertRaisesRegex(
            reference_bridge.PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError,
            "MANIFEST_SITE_LOCAL_ROLE_MISMATCH",
        ):
            reference_bridge.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_with_fresh_full_bundle_admission(
                rendered.wa_ir_service_manifest,
                manifest_admission_config=admissions[1],
                full_bundle_attestation=wire,
                verification_config=self.wa_fi_verification_config,
                **self._route_kwargs("fi-writer-source-outbox"),
                now=NOW,
            )

        source = inspect.getsource(reference_bridge)
        tree = ast.parse(source)
        direct_imports = {
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
            direct_imports
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
        rendered_text = "\n".join(
            manifest.decode("ascii")
            for manifest in (
                rendered.wa_fi_service_manifest,
                rendered.wa_ir_service_manifest,
                rendered.witness_service_manifest,
            )
        )
        for forbidden in (
            "access_key_id",
            "secret_access_key",
            "endpoint_url",
            "provider_route_iam_attestation_sha256",
            "https://",
        ):
            self.assertNotIn(forbidden, rendered_text)
