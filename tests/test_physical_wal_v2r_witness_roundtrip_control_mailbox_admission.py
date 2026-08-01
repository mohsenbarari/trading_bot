from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as admission


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = Ed25519PrivateKey.generate()

    def _config(self, policy, *, identity: str, **changes):
        values = dict(host_id="v2r-host-0001", local_site=policy.local_site, local_role=policy.local_role, release_sha256="1" * 64, deployment_binding_sha256="2" * 64, delivery_binding_sha256="3" * 64, v2r_iam_catalog_sha256="4" * 64, role_credential_identity_sha256=identity, non_v2r_credential_identity_sha256s=tuple(f"{n:064x}" for n in range(1, 13)), host_role_authority_public_key=_public(self.authority), enabled=True, maximum_evidence_age_seconds=60)
        values.update(changes)
        return admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(**values)

    def _assertion(self, config, policy, **changes):
        unsigned = dict(schema="gold-trade-physical-wal-v2r-control-mailbox-host-role-assertion-v1", version=1, protocol_domain="gold-trade-physical-wal-v2r-witness-reverse-carrier-v1", mailbox_prefix="physical-wal-v2r-reverse/", host_id=config.host_id, local_site=policy.local_site, local_role=policy.local_role, mailbox=policy.mailbox, direction=policy.direction, object_prefix=policy.object_prefix, least_privilege_actions=list(policy.least_privilege_actions), policy_sha256=admission._policy_sha(policy), release_sha256=config.release_sha256, deployment_binding_sha256=config.deployment_binding_sha256, delivery_binding_sha256=config.delivery_binding_sha256, v2r_iam_catalog_sha256=config.v2r_iam_catalog_sha256, role_credential_identity_sha256=config.role_credential_identity_sha256, role_iam_policy_sha256="5" * 64, provider_route_iam_attestation_sha256="6" * 64, object_lock_retention_proof_sha256="7" * 64, assertion_id="v2r-control-mailbox-000001", assertion_nonce="N" * 22, issued_at="2026-08-01T12:00:00Z", expires_at="2026-08-01T12:00:30Z")
        unsigned.update(changes)
        sig = self.authority.sign(admission._DOMAIN + admission._canonical(unsigned, "test"))
        return admission._canonical({**unsigned, "signature_base64": base64.b64encode(sig).decode("ascii")}, "test")

    def test_exact_eight_role_site_prefix_and_least_privilege_matrix(self):
        expected = (("wa-ir", "wa-ir-v2r-exporter", "ir-to-witness", "publish", "physical-wal-v2r-reverse/ir-to-witness/", ("object:create-only-fixed-key", "object:read-own-exact-version-receipt")), ("witness", "witness-v2r-reverse-ingress", "ir-to-witness", "consume", "physical-wal-v2r-reverse/ir-to-witness/", ("object:list-fixed-prefix", "object:read-exact-version")), ("witness", "witness-v2r-reverse-egress", "witness-to-fi", "publish", "physical-wal-v2r-reverse/witness-to-fi/", ("object:create-only-fixed-key", "object:read-own-exact-version-receipt")), ("wa-fi", "wa-fi-v2r-recovery-inbox", "witness-to-fi", "consume", "physical-wal-v2r-reverse/witness-to-fi/", ("object:list-fixed-prefix", "object:read-exact-version")), ("wa-fi", "wa-fi-v2r-ack-outbox", "fi-to-witness", "publish", "physical-wal-v2r-reverse/fi-to-witness/", ("object:create-only-fixed-key", "object:read-own-exact-version-receipt")), ("witness", "witness-v2r-ack-ingress", "fi-to-witness", "consume", "physical-wal-v2r-reverse/fi-to-witness/", ("object:list-fixed-prefix", "object:read-exact-version")), ("witness", "witness-v2r-return-egress", "witness-to-ir", "publish", "physical-wal-v2r-reverse/witness-to-ir/", ("object:create-only-fixed-key", "object:read-own-exact-version-receipt")), ("wa-ir", "wa-ir-v2r-return-inbox", "witness-to-ir", "consume", "physical-wal-v2r-reverse/witness-to-ir/", ("object:list-fixed-prefix", "object:read-exact-version")))
        actual = tuple((p.local_site, p.local_role, p.mailbox, p.direction, p.object_prefix, p.least_privilege_actions) for p in admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES)
        self.assertEqual(expected, actual)

    def test_each_role_requires_fresh_signed_exact_assertion_and_non_alias_identity(self):
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        config = self._config(policy, identity="8" * 64)
        granted = admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=config, host_role_assertion=self._assertion(config, policy), now=NOW)
        self.assertEqual(policy.object_prefix, granted.object_prefix)
        for field, value in (("object_prefix", "physical-wal-v2r-reverse/witness-to-fi/"), ("local_site", "wa-fi")):
            with self.subTest(field=field), self.assertRaisesRegex(admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, "ASSERTION_(CROSS_PIN|SIGNATURE)_"):
                admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=config, host_role_assertion=self._assertion(config, policy, **{field: value}), now=NOW)
        with self.assertRaisesRegex(admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, "CREDENTIAL_NON_ALIAS"):
            alias_config = self._config(policy, identity=f"{1:064x}")
            admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
                config=alias_config,
                host_role_assertion=self._assertion(alias_config, policy),
                now=NOW,
            )

    def test_complete_matrix_requires_eight_unique_credential_identities_and_common_bindings(self):
        configs, grants = [], []
        for n, policy in enumerate(admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES, start=8):
            config = self._config(policy, identity=(hex(n)[2:] * 64)[:64])
            configs.append(config)
            grants.append(admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=config, host_role_assertion=self._assertion(config, policy), now=NOW))
        matrix = admission.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(admissions=tuple(grants), configs=tuple(configs), now=NOW)
        self.assertEqual("2" * 64, matrix.deployment_binding_sha256)
        duplicate_config = self._config(
            admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[1],
            identity=configs[0].role_credential_identity_sha256,
        )
        duplicate_grant = admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
            config=duplicate_config,
            host_role_assertion=self._assertion(
                duplicate_config,
                admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[1],
            ),
            now=NOW,
        )
        with self.assertRaisesRegex(admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, "ROLE_MATRIX_CREDENTIAL_ALIAS"):
            admission.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(admissions=tuple([grants[0], duplicate_grant, *grants[2:]]), configs=tuple([configs[0], duplicate_config, *configs[2:]]), now=NOW)

    def test_default_off_expiry_and_forged_capability_fail_closed(self):
        policy = admission.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        config = self._config(policy, identity="8" * 64)
        with self.assertRaisesRegex(admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, "CONFIG_INVALID"):
            admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=self._config(policy, identity="8" * 64, enabled=False), host_role_assertion=self._assertion(config, policy), now=NOW)
        expired = self._assertion(config, policy, expires_at="2026-08-01T12:00:00Z")
        with self.assertRaisesRegex(admission.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionError, "ASSERTION_STALE"):
            admission.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=config, host_role_assertion=expired, now=NOW)
        with self.assertRaises(TypeError):
            admission.VerifiedPhysicalWalV2rWitnessRoundtripControlMailboxAdmission(configuration_sha256="x", capability=object())


if __name__ == "__main__":
    unittest.main()
