from __future__ import annotations

import ast
import base64
from datetime import timedelta
import inspect
import pickle
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as admission
from tests.test_physical_wal_chunked_base_backup_remote_ack_bridge import NOW


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalWalV2WitnessRoundtripMailboxAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = Ed25519PrivateKey.generate()

    def _config(
        self,
        policy: admission.PhysicalWalV2WitnessRoundtripMailboxPolicy,
        **changes: object,
    ) -> admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig:
        values: dict[str, object] = {
            "host_id": "witness-node-0001",
            "local_role": policy.local_role,
            "deployment_binding_sha256": "a" * 64,
            "delivery_binding_sha256": "b" * 64,
            "host_role_authority_public_key": _public(self.authority),
            "enabled": True,
            "maximum_evidence_age_seconds": 60,
        }
        values.update(changes)
        return admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig(**values)

    def _assertion(
        self,
        config: admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionConfig,
        *,
        policy: admission.PhysicalWalV2WitnessRoundtripMailboxPolicy | None = None,
        now=NOW,
        expires_at=None,
        changes: dict[str, object] | None = None,
    ) -> bytes:
        selected = policy or next(
            item
            for item in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
            if item.local_role == config.local_role
        )
        unsigned: dict[str, object] = {
            "schema": "gold-trade-physical-wal-v2-witness-roundtrip-host-role-assertion-v1",
            "version": 1,
            "host_id": config.host_id,
            "local_role": selected.local_role,
            "mailbox": selected.mailbox,
            "direction": selected.direction,
            "object_prefix": selected.object_prefix,
            "least_privilege_actions": list(selected.least_privilege_actions),
            "policy_sha256": admission._policy_sha256(selected),
            "deployment_binding_sha256": config.deployment_binding_sha256,
            "delivery_binding_sha256": config.delivery_binding_sha256,
            "assertion_id": "v2-mailbox-host-role-000001",
            "assertion_nonce": "H" * 22,
            "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (expires_at or now + timedelta(seconds=30)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        if changes:
            unsigned.update(changes)
        signature = self.authority.sign(
            admission._HOST_ROLE_DOMAIN + canonical_json_bytes(unsigned)
        )
        return canonical_json_bytes(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}
        )

    def test_exact_eight_role_mailbox_direction_prefix_and_action_matrix(self) -> None:
        expected = (
            (
                "fi-writer-source-outbox",
                "fi-to-witness",
                "publish",
                "physical-wal-v2-witness-roundtrip-delivery-v1/fi-to-witness/",
                ("object:create-only-fixed-key", "object:read-own-exact-version-receipt"),
            ),
            (
                "witness-fi-ingress",
                "fi-to-witness",
                "consume",
                "physical-wal-v2-witness-roundtrip-delivery-v1/fi-to-witness/",
                ("object:list-fixed-prefix", "object:read-exact-version"),
            ),
            (
                "witness-ir-egress",
                "witness-to-ir",
                "publish",
                "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-ir/",
                ("object:create-only-fixed-key", "object:read-own-exact-version-receipt"),
            ),
            (
                "ir-standby-ack-inbox",
                "witness-to-ir",
                "consume",
                "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-ir/",
                ("object:list-fixed-prefix", "object:read-exact-version"),
            ),
            (
                "ir-durable-ack-outbox",
                "ir-to-witness",
                "publish",
                "physical-wal-v2-witness-roundtrip-delivery-v1/ir-to-witness/",
                ("object:create-only-fixed-key", "object:read-own-exact-version-receipt"),
            ),
            (
                "witness-ir-ingress",
                "ir-to-witness",
                "consume",
                "physical-wal-v2-witness-roundtrip-delivery-v1/ir-to-witness/",
                ("object:list-fixed-prefix", "object:read-exact-version"),
            ),
            (
                "witness-fi-egress",
                "witness-to-fi",
                "publish",
                "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-fi/",
                ("object:create-only-fixed-key", "object:read-own-exact-version-receipt"),
            ),
            (
                "fi-writer-ack-inbox",
                "witness-to-fi",
                "consume",
                "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-fi/",
                ("object:list-fixed-prefix", "object:read-exact-version"),
            ),
        )
        actual = tuple(
            (
                item.local_role,
                item.mailbox,
                item.direction,
                item.object_prefix,
                item.least_privilege_actions,
            )
            for item in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
        )
        self.assertEqual(expected, actual)
        self.assertNotIn("fi-to-ir", {item.mailbox for item in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES})
        self.assertNotIn("ir-to-fi", {item.mailbox for item in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES})

        for policy in admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES:
            with self.subTest(role=policy.local_role):
                config = self._config(policy)
                wire = self._assertion(config)
                verified = admission.verify_physical_wal_v2_witness_roundtrip_mailbox_host_role_assertion(
                    wire, config=config, now=NOW
                )
                granted = admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                    config=config,
                    host_role_assertion=wire,
                    now=NOW,
                )
                required = admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
                    granted, config=config, now=NOW
                )
                self.assertEqual(policy.local_role, verified.local_role)
                self.assertEqual(policy.mailbox, required.mailbox)
                self.assertEqual(policy.direction, required.direction)
                self.assertEqual(policy.object_prefix, required.object_prefix)
                self.assertEqual(policy.least_privilege_actions, required.least_privilege_actions)
                self.assertEqual(verified.assertion_sha256, required.host_role_assertion_sha256)

    def test_default_off_and_host_role_policy_binding_are_mandatory(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        disabled = self._config(policy, enabled=False)
        wire = self._assertion(self._config(policy))
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "ADMISSION_CONFIG_INVALID",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=disabled, host_role_assertion=wire, now=NOW
            )
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "ADMISSION_ROLE_INVALID",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=self._config(policy, local_role="webapp_fi"),
                host_role_assertion=wire,
                now=NOW,
            )

        config = self._config(policy)
        foreign_host = self._assertion(config, changes={"host_id": "witness-node-9999"})
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "HOST_ROLE_ASSERTION_CROSS_PIN_MISMATCH",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=config, host_role_assertion=foreign_host, now=NOW
            )
        wrong_policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[1]
        foreign_role = self._assertion(config, policy=wrong_policy)
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "HOST_ROLE_ASSERTION_CROSS_PIN_MISMATCH",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=config, host_role_assertion=foreign_role, now=NOW
            )

    def test_signature_action_prefix_binding_and_expiry_substitution_fail_closed(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        config = self._config(policy)
        valid = self._assertion(config)
        tampered = valid[:-1] + (b"0" if valid[-1:] != b"0" else b"1")
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "HOST_ROLE_ASSERTION",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=config, host_role_assertion=tampered, now=NOW
            )
        for field, replacement in (
            ("least_privilege_actions", ["object:delete-anything"]),
            ("object_prefix", "physical-wal-v2-witness-roundtrip-delivery-v1/witness-to-ir/"),
            ("deployment_binding_sha256", "c" * 64),
            ("delivery_binding_sha256", "d" * 64),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
                "HOST_ROLE_ASSERTION_CROSS_PIN_MISMATCH",
            ):
                admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                    config=config,
                    host_role_assertion=self._assertion(config, changes={field: replacement}),
                    now=NOW,
                )
        expired = self._assertion(config, expires_at=NOW)
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "HOST_ROLE_ASSERTION_STALE",
        ):
            admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
                config=config, host_role_assertion=expired, now=NOW
            )

    def test_typed_admission_cannot_be_forged_reused_under_other_role_or_after_expiry(self) -> None:
        policy = admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[0]
        config = self._config(policy)
        wire = self._assertion(config)
        granted = admission.admit_physical_wal_v2_witness_roundtrip_mailbox(
            config=config, host_role_assertion=wire, now=NOW
        )
        with self.assertRaisesRegex(TypeError, "CONSTRUCTION_FORBIDDEN"):
            admission.VerifiedPhysicalWalV2WitnessRoundtripMailboxAdmission(
                host_id=granted.host_id,
                local_role=granted.local_role,
                mailbox=granted.mailbox,
                direction=granted.direction,
                object_prefix=granted.object_prefix,
                least_privilege_actions=granted.least_privilege_actions,
                policy_sha256=granted.policy_sha256,
                deployment_binding_sha256=granted.deployment_binding_sha256,
                delivery_binding_sha256=granted.delivery_binding_sha256,
                host_role_assertion_sha256=granted.host_role_assertion_sha256,
                issued_at=granted.issued_at,
                expires_at=granted.expires_at,
                configuration_sha256="f" * 64,
                capability=object(),
            )
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(granted)
        other_config = self._config(
            admission.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES[1]
        )
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "ADMISSION_CAPABILITY_INVALID",
        ):
            admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
                granted, config=other_config, now=NOW
            )
        with self.assertRaisesRegex(
            admission.PhysicalWalV2WitnessRoundtripMailboxAdmissionError,
            "ADMISSION_CAPABILITY_INVALID",
        ):
            admission.require_verified_physical_wal_v2_witness_roundtrip_mailbox_admission(
                granted, config=config, now=NOW + timedelta(seconds=31)
            )

    def test_contract_is_provider_free_and_has_no_legacy_profile_or_transport_surface(self) -> None:
        source = inspect.getsource(admission)
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        for forbidden in (
            "os",
            "pathlib",
            "socket",
            "subprocess",
            "requests",
            "boto",
            "urllib",
            "httpx",
            "paramiko",
        ):
            self.assertNotIn(forbidden, imported)
        self.assertNotIn("physical_arvan_s3_role_profiles", source)
        self.assertNotIn("physical_wal_v1", source)
        self.assertNotIn("preflight", source)
        self.assertNotIn("connect(", source)
        self.assertNotIn("open(", source)
        self.assertNotIn("fi-to-ir", source)
        self.assertNotIn("ir-to-fi", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
