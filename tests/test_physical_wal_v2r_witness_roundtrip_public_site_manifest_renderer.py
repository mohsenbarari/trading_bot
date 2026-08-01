"""Adversarial tests for the pure V2R public per-site manifest renderer.

The subject emits only public canonical JSON.  These tests do not open a
credential, filesystem path, provider client, network connection, installer,
or delivery runtime.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_profile as profile
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as bundle
from core import physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer as issuer
from core import physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer.py"
)
_LEGACY_ROLE_KEYS = (
    ("recovery-data", "fi-publisher"),
    ("recovery-data", "ir-receiver"),
    ("recovery-data", "ir-publisher"),
    ("recovery-data", "fi-receiver"),
    ("normal-v2", "fi-writer-source-outbox"),
    ("normal-v2", "witness-fi-ingress"),
    ("normal-v2", "witness-ir-egress"),
    ("normal-v2", "ir-standby-ack-inbox"),
    ("normal-v2", "ir-durable-ack-outbox"),
    ("normal-v2", "witness-ir-ingress"),
    ("normal-v2", "witness-fi-egress"),
    ("normal-v2", "fi-writer-ack-inbox"),
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class _V2rBundleSigner:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self.key = key

    def sign_physical_wal_v2r_witness_roundtrip_public_full_bundle(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes:
        return self.key.sign(signing_payload)


class PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.host_authority = Ed25519PrivateKey.generate()
        self.bundle_authority = Ed25519PrivateKey.generate()
        self.normal_v2_bundle_authority = Ed25519PrivateKey.generate()
        self.legacy_pins = tuple(
            profile.PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin(
                plane=plane,
                role=role,
                credential_identity_sha256=_hash(f"legacy:{plane}:{role}"),
            )
            for plane, role in _LEGACY_ROLE_KEYS
        )
        self.configs = []
        self.admissions = []
        profiles = []
        for number, policy in enumerate(
            mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES,
            start=1,
        ):
            config = (
                mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(
                    host_id=f"v2r-public-renderer-{policy.local_site}-{number:02d}",
                    local_site=policy.local_site,
                    local_role=policy.local_role,
                    release_sha256=_hash("release"),
                    deployment_binding_sha256=_hash("deployment"),
                    delivery_binding_sha256=_hash("delivery"),
                    v2r_iam_catalog_sha256=_hash("v2r-iam-catalog"),
                    role_credential_identity_sha256=_hash(f"v2r-identity:{number}"),
                    non_v2r_credential_identity_sha256s=tuple(
                        pin.credential_identity_sha256 for pin in self.legacy_pins
                    ),
                    host_role_authority_public_key=_public(self.host_authority),
                    enabled=True,
                    maximum_evidence_age_seconds=60,
                )
            )
            raw = self._assertion(config=config, policy=policy, number=number)
            admission = (
                mailbox.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
                    config=config,
                    host_role_assertion=raw,
                    now=NOW,
                )
            )
            self.configs.append(config)
            self.admissions.append(admission)
            profiles.append(
                profile.build_physical_wal_v2r_phase5_control_mailbox_profile(
                    config=profile.PhysicalWalV2rPhase5ControlMailboxProfileConfig(
                        admission_config=config,
                        expected_host_role_assertion_sha256=hashlib.sha256(
                            raw
                        ).hexdigest(),
                        phase5_profile_binding_sha256=_hash("phase5-profile-binding"),
                        legacy_credential_deny_pins=self.legacy_pins,
                        enabled=True,
                    ),
                    admission=admission,
                    now=NOW,
                )
            )
        self.profile_set = (
            profile.verify_physical_wal_v2r_phase5_control_mailbox_profile_set(
                profiles=tuple(profiles),
                now=NOW,
            )
        )
        self.bundle_config = (
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig(
                release_sha256=_hash("release"),
                deployment_binding_sha256=_hash("deployment"),
                delivery_binding_sha256=_hash("delivery"),
                v2r_iam_catalog_sha256=_hash("v2r-iam-catalog"),
                bundle_authority_public_key=_public(self.bundle_authority),
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        signing_config = (
            issuer.PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig(
                bundle_authority_public_key=_public(self.bundle_authority),
                normal_v2_bundle_authority_public_key_sha256=hashlib.sha256(
                    _public(self.normal_v2_bundle_authority)
                ).hexdigest(),
                normal_v2_mailbox_prefix=(
                    "physical-wal-v2-witness-roundtrip-delivery-v1/"
                ),
                normal_v2_iam_catalog_sha256=_hash("normal-v2-iam-catalog"),
                enabled=True,
                maximum_evidence_age_seconds=60,
            )
        )
        request = issuer.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest(
            bundle_id="v2r-public-site-renderer-000001",
            bundle_nonce="B" * 22,
            issued_at=NOW,
            expires_at=datetime(2026, 8, 1, 12, 0, 30, tzinfo=timezone.utc),
            enabled=True,
        )
        prepared = issuer.prepare_physical_wal_v2r_witness_roundtrip_public_full_bundle(
            config=signing_config,
            request=request,
            profile_set=self.profile_set,
            now=NOW,
        )
        wire = (
            issuer.finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=prepared,
                signer=_V2rBundleSigner(self.bundle_authority),
                now=NOW,
            )
        )
        self.full_bundle = (
            bundle.admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                full_bundle=wire,
                config=self.bundle_config,
                role_admissions=tuple(self.admissions),
                role_configs=tuple(self.configs),
                now=NOW,
            )
        )

    def _assertion(self, *, config, policy, number: int) -> bytes:
        unsigned = {
            "schema": "gold-trade-physical-wal-v2r-control-mailbox-host-role-assertion-v1",
            "version": 1,
            "protocol_domain": "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1",
            "mailbox_prefix": "physical-wal-v2r-reverse/",
            "host_id": config.host_id,
            "local_site": policy.local_site,
            "local_role": policy.local_role,
            "mailbox": policy.mailbox,
            "direction": policy.direction,
            "object_prefix": policy.object_prefix,
            "least_privilege_actions": list(policy.least_privilege_actions),
            "policy_sha256": mailbox._policy_sha(policy),
            "release_sha256": config.release_sha256,
            "deployment_binding_sha256": config.deployment_binding_sha256,
            "delivery_binding_sha256": config.delivery_binding_sha256,
            "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256,
            "role_credential_identity_sha256": (
                config.role_credential_identity_sha256
            ),
            "role_iam_policy_sha256": _hash(f"role-iam:{number}"),
            "provider_route_iam_attestation_sha256": _hash(
                f"provider-route:{number}"
            ),
            "object_lock_retention_proof_sha256": _hash(f"object-lock:{number}"),
            "assertion_id": f"v2r-public-renderer-assertion-{number:03d}",
            "assertion_nonce": "A" * 22,
            "issued_at": "2026-08-01T12:00:00Z",
            "expires_at": "2026-08-01T12:00:30Z",
        }
        signature = self.host_authority.sign(
            mailbox._DOMAIN + mailbox._canonical(unsigned, "TEST_INVALID")
        )
        return mailbox._canonical(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
            "TEST_INVALID",
        )

    def _render(self, site: str) -> bytes:
        return subject.render_physical_wal_v2r_witness_roundtrip_public_site_manifest(
            config=(
                subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig(
                    site=site,
                    enabled=True,
                )
            ),
            full_bundle=self.full_bundle,
            now=NOW,
        )

    def _site_admission_config(self, *, site: str, manifest: bytes):
        return bundle.PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig(
            expected_site=site,
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            enabled=True,
        )

    def _admit_site(self, *, site: str, manifest: bytes):
        return bundle.admit_physical_wal_v2r_witness_roundtrip_site_manifest(
            manifest=manifest,
            config=self._site_admission_config(site=site, manifest=manifest),
            full_bundle=self.full_bundle,
            full_bundle_config=self.bundle_config,
            now=NOW,
        )

    def test_renders_exact_2_2_4_public_slices_that_round_trip_existing_admission(
        self,
    ) -> None:
        expected_counts = {"wa-fi": 2, "wa-ir": 2, "witness": 4}
        for site, expected_count in expected_counts.items():
            with self.subTest(site=site):
                manifest = self._render(site)
                item = json.loads(manifest.decode("ascii"))
                self.assertEqual(bundle._MANIFEST_FIELDS, set(item))
                self.assertEqual(bundle._MANIFEST_SCHEMA, item["schema"])
                self.assertEqual(1, item["version"])
                self.assertEqual(site, item["site"])
                self.assertEqual(expected_count, len(item["roles"]))
                self.assertTrue(
                    all(role["local_site"] == site for role in item["roles"])
                )
                self.assertEqual(
                    [
                        policy.local_role
                        for policy in mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
                        if policy.local_site == site
                    ],
                    [role["local_role"] for role in item["roles"]],
                )
                self.assertFalse(
                    set(item)
                    & {
                        "activation",
                        "credential",
                        "installer",
                        "path",
                        "provider",
                        "runtime",
                        "service",
                        "writer_authorized",
                        "promotion_authorized",
                        "traffic_authorized",
                        "phase5_authorized",
                        "execution_authorized",
                        "full_matrix_authorized",
                        "full_matrix_executed",
                    }
                )
                admitted = self._admit_site(site=site, manifest=manifest)
                self.assertFalse(admitted.is_operational)
                self.assertFalse(admitted.authorizes_phase5)
                self.assertFalse(admitted.authorizes_full_matrix)

    def test_default_off_and_no_raw_role_or_runtime_input_surface(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError,
            "CONFIG_INVALID",
        ):
            subject.render_physical_wal_v2r_witness_roundtrip_public_site_manifest(
                config=(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig(
                        site="wa-fi"
                    )
                ),
                full_bundle=self.full_bundle,
                now=NOW,
            )
        with self.assertRaises(TypeError):
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig(
                roles=()  # type: ignore[call-arg]
            )
        with self.assertRaises(TypeError):
            subject.render_physical_wal_v2r_witness_roundtrip_public_site_manifest(
                config=(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig(
                        site="wa-fi",
                        enabled=True,
                    )
                ),
                full_bundle=self.full_bundle,
                now=NOW,
                credential_path="/forbidden",  # type: ignore[call-arg]
            )
        self.assertEqual(
            {"config", "full_bundle", "now"},
            set(
                inspect.signature(
                    subject.render_physical_wal_v2r_witness_roundtrip_public_site_manifest
                ).parameters
            ),
        )
        self.assertFalse(
            {
                "role",
                "credential",
                "iam",
                "provider",
                "path",
                "runtime",
                "service",
            }
            & {
                token
                for field in fields(
                    subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRenderConfig
                )
                for token in field.name.split("_")
            }
        )

    def test_site_role_prefix_and_identity_substitution_fail_closed(self) -> None:
        source = json.loads(self._render("wa-ir").decode("ascii"))

        site_substitution = {**source, "site": "wa-fi"}
        with self.assertRaisesRegex(
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError,
            "CROSS_PIN_MISMATCH",
        ):
            self._admit_site(
                site="wa-ir",
                manifest=bundle._canonical(site_substitution, "TEST_INVALID"),
            )

        foreign_role = json.loads(self._render("wa-fi").decode("ascii"))["roles"][0]
        role_substitution = {
            **source,
            "roles": [foreign_role, *source["roles"][1:]],
        }
        with self.assertRaisesRegex(
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError,
            "ROLE_SUBSTITUTION",
        ):
            self._admit_site(
                site="wa-ir",
                manifest=bundle._canonical(role_substitution, "TEST_INVALID"),
            )

        prefix_substitution = {
            **source,
            "roles": [
                {
                    **source["roles"][0],
                    "object_prefix": "physical-wal-v2r-reverse/witness-to-fi/",
                },
                *source["roles"][1:],
            ],
        }
        with self.assertRaisesRegex(
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError,
            "ROLE_SUBSTITUTION",
        ):
            self._admit_site(
                site="wa-ir",
                manifest=bundle._canonical(prefix_substitution, "TEST_INVALID"),
            )

        identity_substitution = {
            **source,
            "roles": [
                {
                    **source["roles"][0],
                    "role_credential_identity_sha256": _hash("foreign-identity"),
                },
                *source["roles"][1:],
            ],
        }
        with self.assertRaisesRegex(
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError,
            "ROLE_SUBSTITUTION",
        ):
            self._admit_site(
                site="wa-ir",
                manifest=bundle._canonical(identity_substitution, "TEST_INVALID"),
            )

    def test_tampered_opaque_bundle_is_not_renderable(self) -> None:
        roles = json.loads(self.full_bundle._roles_canonical.decode("ascii"))
        roles[0] = {
            **roles[0],
            "object_prefix": "physical-wal-v2r-reverse/witness-to-fi/",
        }
        object.__setattr__(
            self.full_bundle,
            "_roles_canonical",
            bundle._canonical(roles, "TEST_INVALID"),
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError,
            "BUNDLE_INVALID",
        ):
            self._render("wa-ir")

    def test_operationally_mutated_opaque_bundle_is_not_renderable(self) -> None:
        object.__setattr__(self.full_bundle, "authorizes_phase5", True)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicSiteManifestRendererError,
            "BUNDLE_INVALID",
        ):
            self._render("wa-ir")

    def test_source_has_no_normal_v2_recovery_provider_or_runtime_imports(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            any(
                token in name
                for name in imported
                for token in (
                    "physical_wal_v2_witness_roundtrip",
                    "physical_arvan",
                    "recovery",
                    "boto3",
                    "botocore",
                    "docker",
                    "http",
                    "os",
                    "pathlib",
                    "requests",
                    "socket",
                    "subprocess",
                )
            )
        )
        self.assertEqual(
            {
                "physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission",
            },
            {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "core"
                for alias in node.names
            },
        )


if __name__ == "__main__":
    unittest.main()
