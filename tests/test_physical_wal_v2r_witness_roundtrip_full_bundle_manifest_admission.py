from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host_authority, self.bundle_authority = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        self.configs, self.grants = [], []
        for number, policy in enumerate(mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES, 20):
            config = mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(host_id=f"v2r-public-host-{number:03d}", local_site=policy.local_site, local_role=policy.local_role, release_sha256="1" * 64, deployment_binding_sha256="2" * 64, delivery_binding_sha256="3" * 64, v2r_iam_catalog_sha256="4" * 64, role_credential_identity_sha256=f"{number:064x}", non_v2r_credential_identity_sha256s=tuple(f"{n:064x}" for n in range(1, 13)), host_role_authority_public_key=_public(self.host_authority), enabled=True, maximum_evidence_age_seconds=60)
            assertion = self._assertion(config, policy)
            self.configs.append(config)
            self.grants.append(mailbox.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(config=config, host_role_assertion=assertion, now=NOW))
        self.bundle_config = subject.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig(release_sha256="1" * 64, deployment_binding_sha256="2" * 64, delivery_binding_sha256="3" * 64, v2r_iam_catalog_sha256="4" * 64, bundle_authority_public_key=_public(self.bundle_authority), enabled=True, maximum_evidence_age_seconds=60)

    def _assertion(self, config, policy) -> bytes:
        unsigned = {"schema": "gold-trade-physical-wal-v2r-control-mailbox-host-role-assertion-v1", "version": 1, "protocol_domain": "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1", "mailbox_prefix": "physical-wal-v2r-reverse/", "host_id": config.host_id, "local_site": policy.local_site, "local_role": policy.local_role, "mailbox": policy.mailbox, "direction": policy.direction, "object_prefix": policy.object_prefix, "least_privilege_actions": list(policy.least_privilege_actions), "policy_sha256": mailbox._policy_sha(policy), "release_sha256": config.release_sha256, "deployment_binding_sha256": config.deployment_binding_sha256, "delivery_binding_sha256": config.delivery_binding_sha256, "v2r_iam_catalog_sha256": config.v2r_iam_catalog_sha256, "role_credential_identity_sha256": config.role_credential_identity_sha256, "role_iam_policy_sha256": "5" * 64, "provider_route_iam_attestation_sha256": "6" * 64, "object_lock_retention_proof_sha256": "7" * 64, "assertion_id": f"v2r-public-assertion-{policy.local_role}", "assertion_nonce": "N" * 22, "issued_at": "2026-08-01T12:00:00Z", "expires_at": "2026-08-01T12:00:30Z"}
        signature = self.host_authority.sign(mailbox._DOMAIN + mailbox._canonical(unsigned, "test"))
        return mailbox._canonical({**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}, "test")

    def _wire(self, *, roles=None, expires_at="2026-08-01T12:00:30Z") -> bytes:
        roles = [subject._role_projection(grant) for grant in self.grants] if roles is None else roles
        matrix = mailbox.verify_physical_wal_v2r_witness_roundtrip_control_mailbox_role_matrix(admissions=tuple(self.grants), configs=tuple(self.configs), now=NOW)
        unsigned = {"schema": "gold-trade-physical-wal-v2r-public-full-bundle-v1", "version": 1, "protocol_domain": "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1", "mailbox_prefix": "physical-wal-v2r-reverse/", "bundle_id": "v2r-public-full-bundle-000001", "bundle_nonce": "B" * 22, "release_sha256": "1" * 64, "deployment_binding_sha256": "2" * 64, "delivery_binding_sha256": "3" * 64, "v2r_iam_catalog_sha256": "4" * 64, "role_matrix_sha256": matrix.role_matrix_sha256, "roles": roles, "issued_at": "2026-08-01T12:00:00Z", "expires_at": expires_at}
        signature = self.bundle_authority.sign(subject._BUNDLE_DOMAIN + subject._canonical(unsigned, "test"))
        return subject._canonical({**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}, "test")

    def _bundle(self):
        return subject.admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(full_bundle=self._wire(), config=self.bundle_config, role_admissions=tuple(self.grants), role_configs=tuple(self.configs), now=NOW)

    def test_eight_exact_public_role_pins_admit_but_are_non_operational(self):
        bundle = self._bundle()
        self.assertFalse(bundle.is_operational)
        self.assertFalse(bundle.authorizes_phase5)
        self.assertFalse(bundle.authorizes_full_matrix)
        with self.assertRaises(TypeError):
            subject.VerifiedPhysicalWalV2rWitnessRoundtripPublicFullBundle(configuration_sha256="x", role_digest="x", roles_canonical=b"[]", capability=object())

    def test_role_or_identity_substitution_and_expiry_fail_closed(self):
        roles = [subject._role_projection(grant) for grant in self.grants]
        roles[0] = {**roles[0], "object_prefix": "physical-wal-v2r-reverse/witness-to-fi/"}
        with self.assertRaisesRegex(subject.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError, "ROLE_SUBSTITUTION"):
            subject.admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(full_bundle=self._wire(roles=roles), config=self.bundle_config, role_admissions=tuple(self.grants), role_configs=tuple(self.configs), now=NOW)
        with self.assertRaisesRegex(subject.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError, "STALE"):
            subject.admit_physical_wal_v2r_witness_roundtrip_public_full_bundle(full_bundle=self._wire(expires_at="2026-08-01T12:00:00Z"), config=self.bundle_config, role_admissions=tuple(self.grants), role_configs=tuple(self.configs), now=NOW)

    def test_site_manifests_are_exact_public_bundle_slices(self):
        bundle = self._bundle()
        roles = [subject._role_projection(grant) for grant in self.grants if grant.local_site == "witness"]
        manifest = subject._canonical({"schema": "gold-trade-physical-wal-v2r-public-site-manifest-v1", "version": 1, "site": "witness", "release_sha256": bundle.release_sha256, "deployment_binding_sha256": bundle.deployment_binding_sha256, "delivery_binding_sha256": bundle.delivery_binding_sha256, "v2r_iam_catalog_sha256": bundle.v2r_iam_catalog_sha256, "full_bundle_sha256": bundle.full_bundle_sha256, "roles": roles}, "test")
        config = subject.PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig(expected_site="witness", expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(), enabled=True)
        grant = subject.admit_physical_wal_v2r_witness_roundtrip_site_manifest(manifest=manifest, config=config, full_bundle=bundle, full_bundle_config=self.bundle_config, now=NOW)
        self.assertEqual("witness", grant.site)
        self.assertFalse(grant.is_operational)
        bad = subject._canonical({**__import__("json").loads(manifest), "roles": roles[:3]}, "test")
        with self.assertRaisesRegex(subject.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError, "HASH_MISMATCH|ROLE_SUBSTITUTION"):
            subject.admit_physical_wal_v2r_witness_roundtrip_site_manifest(manifest=bad, config=config, full_bundle=bundle, full_bundle_config=self.bundle_config, now=NOW)
        altered_roles = [{**roles[0], "role_credential_identity_sha256": "f" * 64}, *roles[1:]]
        altered = subject._canonical({**__import__("json").loads(manifest), "roles": altered_roles}, "test")
        altered_config = subject.PhysicalWalV2rWitnessRoundtripSiteManifestAdmissionConfig(expected_site="witness", expected_manifest_sha256=hashlib.sha256(altered).hexdigest(), enabled=True)
        with self.assertRaisesRegex(subject.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionError, "ROLE_SUBSTITUTION"):
            subject.admit_physical_wal_v2r_witness_roundtrip_site_manifest(manifest=altered, config=altered_config, full_bundle=bundle, full_bundle_config=self.bundle_config, now=NOW)


if __name__ == "__main__":
    unittest.main()
