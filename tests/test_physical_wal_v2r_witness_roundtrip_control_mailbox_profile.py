"""Adversarial tests for the pure V2R Phase-5 mailbox profile layer.

The suite deliberately does not construct a credential, client, provider
policy, Object-Storage request, delivery runtime, or Phase-5 adapter.
"""

from __future__ import annotations

import ast
import base64
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as admission
from core import physical_wal_v2r_witness_roundtrip_control_mailbox_profile as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_control_mailbox_profile.py"
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


class PhysicalWalV2rWitnessRoundtripControlMailboxProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = Ed25519PrivateKey.generate()
        self.legacy_pins = tuple(
            subject.PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin(
                plane=plane,
                role=role,
                credential_identity_sha256=_hash(f"legacy:{plane}:{role}"),
            )
            for plane, role in _LEGACY_ROLE_KEYS
        )

    def _admission_config(
        self,
        policy: admission.PhysicalWalV2rWitnessRoundtripControlMailboxPolicy,
        *,
        number: int,
        identity: str | None = None,
        **changes: object,
    ) -> admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig:
        values: dict[str, object] = {
            "host_id": f"v2r-profile-{policy.local_site}-host",
            "local_site": policy.local_site,
            "local_role": policy.local_role,
            "release_sha256": _hash("release"),
            "deployment_binding_sha256": _hash("deployment"),
            "delivery_binding_sha256": _hash("delivery"),
            "v2r_iam_catalog_sha256": _hash("v2r-iam-catalog"),
            "role_credential_identity_sha256": (
                _hash(f"v2r-role-identity:{number}")
                if identity is None
                else identity
            ),
            "non_v2r_credential_identity_sha256s": tuple(
                pin.credential_identity_sha256 for pin in self.legacy_pins
            ),
            "host_role_authority_public_key": _public(self.authority),
            "enabled": True,
            "maximum_evidence_age_seconds": 60,
        }
        values.update(changes)
        return admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(
            **values
        )

    def _assertion(
        self,
        config: admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig,
        policy: admission.PhysicalWalV2rWitnessRoundtripControlMailboxPolicy,
        **changes: object,
    ) -> bytes:
        unsigned: dict[str, object] = {
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
            "policy_sha256": admission._policy_sha(policy),
            "release_sha256": config.release_sha256,
            "deployment_binding_sha256": config.deployment_binding_sha256,
            "delivery_binding_sha256": config.delivery_binding_sha256,
            "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256,
            "role_credential_identity_sha256": config.role_credential_identity_sha256,
            "role_iam_policy_sha256": _hash(f"role-iam:{policy.local_role}"),
            "provider_route_iam_attestation_sha256": _hash(
                f"provider-route:{policy.local_role}"
            ),
            "object_lock_retention_proof_sha256": _hash(
                f"object-lock:{policy.local_role}"
            ),
            "assertion_id": "v2r-profile-assertion-000001",
            "assertion_nonce": "P" * 22,
            "issued_at": "2026-08-01T12:00:00Z",
            "expires_at": "2026-08-01T12:00:30Z",
        }
        unsigned.update(changes)
        signature = self.authority.sign(
            admission._DOMAIN + admission._canonical(unsigned, "TEST_INVALID")
        )
        return admission._canonical(
            {
                **unsigned,
                "signature_base64": base64.b64encode(signature).decode("ascii"),
            },
            "TEST_INVALID",
        )

    def _profile(
        self,
        policy: admission.PhysicalWalV2rWitnessRoundtripControlMailboxPolicy,
        *,
        number: int,
        identity: str | None = None,
        profile_binding: str | None = None,
        **profile_changes: object,
    ) -> subject.VerifiedPhysicalWalV2rPhase5ControlMailboxProfile:
        admission_config = self._admission_config(
            policy,
            number=number,
            identity=identity,
        )
        raw = self._assertion(admission_config, policy)
        verified = admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
            config=admission_config,
            host_role_assertion=raw,
            now=NOW,
        )
        values: dict[str, object] = {
            "admission_config": admission_config,
            "expected_host_role_assertion_sha256": hashlib.sha256(raw).hexdigest(),
            "phase5_profile_binding_sha256": (
                _hash("phase5-profile-binding")
                if profile_binding is None
                else profile_binding
            ),
            "legacy_credential_deny_pins": self.legacy_pins,
            "enabled": True,
        }
        values.update(profile_changes)
        config = subject.PhysicalWalV2rPhase5ControlMailboxProfileConfig(**values)
        return subject.build_physical_wal_v2r_phase5_control_mailbox_profile(
            config=config,
            admission=verified,
            now=NOW,
        )

    def test_default_off_and_exact_v2r_transport_profile_are_mandatory(self) -> None:
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        admission_config = self._admission_config(policy, number=1)
        raw = self._assertion(admission_config, policy)
        verified = admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
            config=admission_config,
            host_role_assertion=raw,
            now=NOW,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_CONFIG_INVALID",
        ):
            subject.build_physical_wal_v2r_phase5_control_mailbox_profile(
                config=subject.PhysicalWalV2rPhase5ControlMailboxProfileConfig(),
                admission=verified,
                now=NOW,
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_CONFIG_INVALID",
        ):
            self._profile(
                policy,
                number=1,
                phase5_transport_profile="ir-v2-witness-roundtrip-strict-ack-v1",
            )

    def test_one_profile_exactly_repeats_signed_host_and_role_pins(self) -> None:
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        profile = self._profile(policy, number=1)
        self.assertIs(
            profile,
            subject.require_verified_physical_wal_v2r_phase5_control_mailbox_profile(
                profile=profile,
                now=NOW,
            ),
        )
        self.assertEqual("wa-ir-v2r-exporter", profile.local_role)
        self.assertEqual("physical-wal-v2r-reverse/ir-to-witness/", profile.object_prefix)
        self.assertEqual(
            subject.PHYSICAL_WAL_V2R_PHASE5_TRANSPORT_PROFILE,
            profile.phase5_transport_profile,
        )
        self.assertFalse(profile.writer_authorized)
        self.assertFalse(profile.phase5_authorized)
        self.assertFalse(profile.full_matrix_executed)

    def test_labeled_legacy_pins_and_host_assertion_are_exact(self) -> None:
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        admission_config = self._admission_config(policy, number=1)
        raw = self._assertion(admission_config, policy)
        verified = admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
            config=admission_config,
            host_role_assertion=raw,
            now=NOW,
        )
        bad_pins = list(self.legacy_pins)
        bad_pins[0] = subject.PhysicalWalV2rPhase5ControlMailboxLegacyDenyPin(
            plane="normal-v2",
            role="fi-publisher",
            credential_identity_sha256=bad_pins[0].credential_identity_sha256,
        )
        config = subject.PhysicalWalV2rPhase5ControlMailboxProfileConfig(
            admission_config=admission_config,
            expected_host_role_assertion_sha256=_hash("wrong-host-assertion"),
            phase5_profile_binding_sha256=_hash("phase5-profile-binding"),
            legacy_credential_deny_pins=tuple(bad_pins),
            enabled=True,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "LEGACY_DENY_PINS_INVALID",
        ):
            subject.build_physical_wal_v2r_phase5_control_mailbox_profile(
                config=config,
                admission=verified,
                now=NOW,
            )
        config = subject.PhysicalWalV2rPhase5ControlMailboxProfileConfig(
            admission_config=admission_config,
            expected_host_role_assertion_sha256=_hash("wrong-host-assertion"),
            phase5_profile_binding_sha256=_hash("phase5-profile-binding"),
            legacy_credential_deny_pins=self.legacy_pins,
            enabled=True,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "ADMISSION_CROSS_PIN_MISMATCH",
        ):
            subject.build_physical_wal_v2r_phase5_control_mailbox_profile(
                config=config,
                admission=verified,
                now=NOW,
            )

    def test_recovery_and_normal_v2_role_reuse_have_no_profile_route(self) -> None:
        base = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        foreign_admission_config = self._admission_config(
            base,
            number=1,
            local_role="fi-publisher",
        )
        config = subject.PhysicalWalV2rPhase5ControlMailboxProfileConfig(
            admission_config=foreign_admission_config,
            expected_host_role_assertion_sha256=_hash("host-assertion"),
            phase5_profile_binding_sha256=_hash("phase5-profile-binding"),
            legacy_credential_deny_pins=self.legacy_pins,
            enabled=True,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_ROLE_INVALID",
        ):
            subject.build_physical_wal_v2r_phase5_control_mailbox_profile(
                config=config,
                admission=object(),
                now=NOW,
            )

    def test_full_eight_role_set_requires_distinct_profiles_and_identities(self) -> None:
        policies = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES
        profiles = tuple(
            self._profile(policy, number=number)
            for number, policy in enumerate(policies, start=1)
        )
        profile_set = subject.verify_physical_wal_v2r_phase5_control_mailbox_profile_set(
            profiles=profiles,
            now=NOW,
        )
        self.assertIs(
            profile_set,
            subject.require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set(
                profile_set=profile_set,
                now=NOW,
            ),
        )
        self.assertEqual(8, len(profile_set.role_profile_sha256s))
        self.assertFalse(profile_set.execution_authorized)
        self.assertFalse(profile_set.full_matrix_executed)

        duplicate_identity = self._profile(
            policies[1],
            number=2,
            identity=profiles[0].role_credential_identity_sha256,
        )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_SET_ALIAS",
        ):
            subject.verify_physical_wal_v2r_phase5_control_mailbox_profile_set(
                profiles=(profiles[0], duplicate_identity, *profiles[2:]),
                now=NOW,
            )
        object.__setattr__(profile_set, "release_sha256", _hash("tampered-release"))
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_SET_TAMPERED",
        ):
            subject.require_verified_physical_wal_v2r_phase5_control_mailbox_profile_set(
                profile_set=profile_set,
                now=NOW,
            )

    def test_capability_and_visible_tampering_fail_closed(self) -> None:
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        profile = self._profile(policy, number=1)
        object.__setattr__(profile, "object_prefix", "physical-wal-v2r-reverse/foreign/")
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rPhase5ControlMailboxProfileError,
            "PROFILE_TAMPERED",
        ):
            subject.require_verified_physical_wal_v2r_phase5_control_mailbox_profile(
                profile=profile,
                now=NOW,
            )
        with self.assertRaises(TypeError):
            subject.VerifiedPhysicalWalV2rPhase5ControlMailboxProfile(
                capability=object()
            )

    def test_source_has_no_provider_credential_or_legacy_plane_import(self) -> None:
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
                    "boto3",
                    "botocore",
                    "docker",
                    "http",
                    "os",
                    "paramiko",
                    "requests",
                    "socket",
                    "subprocess",
                    "physical_arvan_s3_role_profiles",
                    "physical_wal_v2_witness_roundtrip",
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
