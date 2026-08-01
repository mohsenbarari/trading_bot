"""Adversarial tests for the pure V2R public-bundle issuer.

No test opens a credential, Object Storage client, provider API, socket,
filesystem path, delivery runtime, or service installer.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_profile as profile
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as bundle
from core import physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer.py"
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


class _NormalV2BundleSigner:
    """A deliberately incompatible normal-V2 signer shape."""

    def sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes:
        return signing_payload


class PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerTests(unittest.TestCase):
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
        self.profiles = []
        for number, policy in enumerate(
            mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES,
            start=1,
        ):
            config = mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(
                host_id=f"v2r-issuer-{policy.local_site}-host-{number:02d}",
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
            raw = self._assertion(config=config, policy=policy, number=number)
            admission = mailbox.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
                config=config,
                host_role_assertion=raw,
                now=NOW,
            )
            self.configs.append(config)
            self.admissions.append(admission)
            self.profiles.append(
                profile.build_physical_wal_v2r_phase5_control_mailbox_profile(
                    config=profile.PhysicalWalV2rPhase5ControlMailboxProfileConfig(
                        admission_config=config,
                        expected_host_role_assertion_sha256=hashlib.sha256(raw).hexdigest(),
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
                profiles=tuple(self.profiles),
                now=NOW,
            )
        )
        self.signing_config = subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig(
            bundle_authority_public_key=_public(self.bundle_authority),
            normal_v2_bundle_authority_public_key_sha256=hashlib.sha256(
                _public(self.normal_v2_bundle_authority)
            ).hexdigest(),
            normal_v2_mailbox_prefix="physical-wal-v2-witness-roundtrip-delivery-v1/",
            normal_v2_iam_catalog_sha256=_hash("normal-v2-iam-catalog"),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )
        self.request = subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest(
            bundle_id="v2r-public-bundle-issuer-000001",
            bundle_nonce="B" * 22,
            issued_at=NOW,
            expires_at=datetime(2026, 8, 1, 12, 0, 30, tzinfo=timezone.utc),
            enabled=True,
        )
        self.bundle_config = bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig(
            release_sha256=_hash("release"),
            deployment_binding_sha256=_hash("deployment"),
            delivery_binding_sha256=_hash("delivery"),
            v2r_iam_catalog_sha256=_hash("v2r-iam-catalog"),
            bundle_authority_public_key=_public(self.bundle_authority),
            enabled=True,
            maximum_evidence_age_seconds=60,
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
            "role_credential_identity_sha256": config.role_credential_identity_sha256,
            "role_iam_policy_sha256": _hash(f"role-iam:{number}"),
            "provider_route_iam_attestation_sha256": _hash(f"provider-route:{number}"),
            "object_lock_retention_proof_sha256": _hash(f"object-lock:{number}"),
            "assertion_id": f"v2r-issuer-assertion-{number:03d}",
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

    def _prepare(self, *, config=None, profile_set=None, request=None):
        return subject.prepare_physical_wal_v2r_witness_roundtrip_public_full_bundle(
            config=self.signing_config if config is None else config,
            request=self.request if request is None else request,
            profile_set=self.profile_set if profile_set is None else profile_set,
            now=NOW,
        )

    def test_prepared_bundle_round_trips_the_existing_public_bundle_admission(self) -> None:
        prepared = self._prepare()
        self.assertIs(
            prepared,
            subject.require_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=prepared,
                now=NOW,
            ),
        )
        self.assertFalse(prepared.provider_facts_verified)
        self.assertFalse(prepared.phase5_authorized)
        self.assertFalse(prepared.full_matrix_executed)
        wire = subject.finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
            prepared=prepared,
            signer=_V2rBundleSigner(self.bundle_authority),
            now=NOW,
        )
        admitted = bundle.admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(
            full_bundle=wire,
            config=self.bundle_config,
            role_admissions=tuple(self.admissions),
            role_configs=tuple(self.configs),
            now=NOW,
        )
        self.assertEqual(prepared.role_matrix_sha256, admitted.role_matrix_sha256)
        self.assertFalse(admitted.is_operational)
        self.assertFalse(admitted.authorizes_phase5)
        self.assertFalse(admitted.authorizes_full_matrix)
        admissions, matrix = (
            profile.require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set_admissions_and_matrix(
                profile_set=self.profile_set,
                now=NOW,
            )
        )
        self.assertEqual(tuple(self.admissions), admissions)
        self.assertEqual(prepared.role_matrix_sha256, matrix.role_matrix_sha256)

    def test_default_off_and_raw_role_projection_inputs_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "ISSUER_CONFIG_INVALID",
        ):
            self._prepare(
                config=subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig()
            )
        with self.assertRaises(TypeError):
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest(
                roles=()  # type: ignore[call-arg]
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "PROFILE_SET_INVALID",
        ):
            self._prepare(profile_set=tuple(self.admissions))

    def test_normal_v2_signer_key_prefix_and_iam_substitution_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "NORMAL_V2_SIGNER_REUSED",
        ):
            self._prepare(
                config=replace(
                    self.signing_config,
                    normal_v2_bundle_authority_public_key_sha256=hashlib.sha256(
                        _public(self.bundle_authority)
                    ).hexdigest(),
                )
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "NORMAL_V2_PREFIX_REUSED",
        ):
            self._prepare(
                config=replace(
                    self.signing_config,
                    normal_v2_mailbox_prefix="physical-wal-v2r-reverse/",
                )
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "NORMAL_V2_IAM_REUSED",
        ):
            self._prepare(
                config=replace(
                    self.signing_config,
                    normal_v2_iam_catalog_sha256=self.profile_set.v2r_iam_catalog_sha256,
                )
            )
        prepared = self._prepare()
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "ISSUER_SIGNER_INVALID",
        ):
            subject.finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=prepared,
                signer=_NormalV2BundleSigner(),
                now=NOW,
            )

    def test_normal_v2_identity_and_prefix_substitution_cannot_cross_the_profile_set(self) -> None:
        object.__setattr__(
            self.profiles[0],
            "role_credential_identity_sha256",
            self.legacy_pins[4].credential_identity_sha256,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "PROFILE_SET_INVALID",
        ):
            self._prepare()

    def test_raw_normal_v2_bundle_never_becomes_a_prepared_v2r_bundle(self) -> None:
        normal_v2_bundle = (
            b'{"schema":"gold-trade-physical-wal-v2-witness-roundtrip-full-bundle-v1"}'
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "PREPARED_CAPABILITY_INVALID",
        ):
            subject.finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=normal_v2_bundle,
                signer=_V2rBundleSigner(self.bundle_authority),
                now=NOW,
            )

    def test_tampering_and_a_substituted_signature_fail_closed(self) -> None:
        prepared = self._prepare()
        object.__setattr__(prepared, "release_sha256", _hash("tampered"))
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "PREPARED_TAMPERED",
        ):
            subject.require_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=prepared,
                now=NOW,
            )

        prepared = self._prepare()
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuerError,
            "SIGNATURE_INVALID",
        ):
            subject.finalize_prepared_physical_wal_v2r_witness_roundtrip_public_full_bundle(
                prepared=prepared,
                signer=_V2rBundleSigner(Ed25519PrivateKey.generate()),
                now=NOW,
            )

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
                    "physical_arvan_s3",
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
            {"config", "request", "profile_set", "now"},
            set(
                __import__("inspect").signature(
                    subject.prepare_physical_wal_v2r_witness_roundtrip_public_full_bundle
                ).parameters
            ),
        )
        for config_type in (
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleSigningConfig,
            subject.PhysicalWalV2rWitnessRoundtripPublicFullBundleIssuanceRequest,
        ):
            self.assertFalse(
                {
                    "credential",
                    "provider",
                    "retention",
                    "role_projection",
                }
                & {item.name for item in fields(config_type)}
            )


if __name__ == "__main__":
    unittest.main()
