"""Adversarial tests for the pure V2R provider-evidence admission boundary.

These tests deliberately use only locally generated signing keys and canonical
bytes.  They make no provider, credential, Object Storage, socket, filesystem
or deployment call.
"""

from __future__ import annotations

import ast
import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_provider_route_iam_object_lock_evidence as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_provider_route_iam_object_lock_evidence.py"
)


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


class PhysicalWalV2rProviderRouteIamObjectLockEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.host_authority = Ed25519PrivateKey.generate()
        self.provider_authority = Ed25519PrivateKey.generate()
        self.normal_v2_provider_authority = Ed25519PrivateKey.generate()
        self.admission_configs = []
        self.admissions = []
        self.configs = []
        self.raw_evidences = []
        self.verified = []
        for number, policy in enumerate(
            mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES,
            start=1,
        ):
            admission_config = self._admission_config(policy, number)
            admission = mailbox.admit_physical_wal_v2r_witness_roundtrip_control_mailbox(
                config=admission_config,
                host_role_assertion=self._host_assertion(admission_config, policy, number),
                now=NOW,
            )
            config = self._config(admission_config, number)
            raw = self._provider_evidence(config, admission, number)
            verified = subject.verify_physical_wal_v2r_provider_route_iam_object_lock_evidence(
                config=config,
                admission=admission,
                provider_evidence=raw,
                now=NOW,
            )
            self.admission_configs.append(admission_config)
            self.admissions.append(admission)
            self.configs.append(config)
            self.raw_evidences.append(raw)
            self.verified.append(verified)

    def _admission_config(self, policy, number: int):
        return mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(
            host_id=f"v2r-provider-evidence-{policy.local_site}-host-{number:02d}",
            local_site=policy.local_site,
            local_role=policy.local_role,
            release_sha256=_hash("release"),
            deployment_binding_sha256=_hash("deployment"),
            delivery_binding_sha256=_hash("delivery"),
            v2r_iam_catalog_sha256=_hash("v2r-iam-catalog"),
            role_credential_identity_sha256=_hash(f"credential:{number}"),
            non_v2r_credential_identity_sha256s=tuple(
                _hash(f"legacy:{item}") for item in range(12)
            ),
            host_role_authority_public_key=_public(self.host_authority),
            enabled=True,
            maximum_evidence_age_seconds=60,
        )

    def _host_assertion(self, config, policy, number: int) -> bytes:
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
            "provider_route_iam_attestation_sha256": _hash(f"route-attestation:{number}"),
            "object_lock_retention_proof_sha256": _hash(f"lock-proof:{number}"),
            "assertion_id": f"v2r-provider-host-assertion-{number:03d}",
            "assertion_nonce": "A" * 22,
            "issued_at": "2026-08-01T12:00:00Z",
            "expires_at": "2026-08-01T12:00:30Z",
        }
        signature = self.host_authority.sign(
            mailbox._DOMAIN + mailbox._canonical(unsigned, "TEST_INVALID")
        )
        return mailbox._canonical(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")},
            "TEST_INVALID",
        )

    def _config(self, admission_config, number: int, **changes):
        values = {
            "admission_config": admission_config,
            "provider_evidence_authority_public_key": _public(self.provider_authority),
            "provider_endpoint_sha256": _hash("provider-endpoint"),
            "provider_bucket_sha256": _hash("provider-bucket"),
            "provider_region_sha256": _hash("provider-region"),
            "object_lock_minimum_retention_seconds": 86_400 + number,
            "normal_v2_provider_evidence_authority_public_key_sha256": hashlib.sha256(
                _public(self.normal_v2_provider_authority)
            ).hexdigest(),
            "normal_v2_mailbox_prefix": "physical-wal-v2-witness-roundtrip-delivery-v1/",
            "normal_v2_iam_catalog_sha256": _hash("normal-v2-iam-catalog"),
            "enabled": True,
            "maximum_evidence_age_seconds": 60,
        }
        values.update(changes)
        return subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig(**values)

    def _provider_evidence(self, config, admission, number: int, **changes) -> bytes:
        actions = (
            subject._PUBLISH_PROVIDER_ACTIONS
            if admission.direction == "publish"
            else subject._CONSUME_PROVIDER_ACTIONS
        )
        unsigned = {
            "schema": subject.PHYSICAL_WAL_V2R_PROVIDER_ROUTE_IAM_OBJECT_LOCK_EVIDENCE_SCHEMA,
            "version": 1,
            "protocol_domain": "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1",
            "mailbox_prefix": "physical-wal-v2r-reverse/",
            "local_site": admission.local_site,
            "local_role": admission.local_role,
            "mailbox": admission.mailbox,
            "direction": admission.direction,
            "object_prefix": admission.object_prefix,
            "least_privilege_actions": list(admission.least_privilege_actions),
            "provider_kind": "arvan-s3-compatible-v1",
            "provider_endpoint_sha256": config.provider_endpoint_sha256,
            "provider_bucket_sha256": config.provider_bucket_sha256,
            "provider_region_sha256": config.provider_region_sha256,
            "allowed_provider_actions": list(actions),
            "versioning_enabled": True,
            "object_lock_enabled": True,
            "object_lock_mode": "COMPLIANCE",
            "object_lock_minimum_retention_seconds": config.object_lock_minimum_retention_seconds,
            "role_credential_identity_sha256": admission.role_credential_identity_sha256,
            "role_iam_policy_sha256": admission.role_iam_policy_sha256,
            "provider_route_iam_attestation_sha256": admission.provider_route_iam_attestation_sha256,
            "object_lock_retention_proof_sha256": admission.object_lock_retention_proof_sha256,
            "deployment_binding_sha256": admission.deployment_binding_sha256,
            "delivery_binding_sha256": admission.delivery_binding_sha256,
            "v2r_iam_catalog_sha256": config.admission_config.v2r_iam_catalog_sha256,
            "evidence_id": f"v2r-provider-route-evidence-{number:03d}",
            "evidence_nonce": "E" * 22,
            "issued_at": "2026-08-01T12:00:00Z",
            "expires_at": "2026-08-01T12:00:30Z",
        }
        unsigned.update(changes)
        signature = self.provider_authority.sign(
            subject._DOMAIN + subject._canonical(unsigned, "TEST_INVALID")
        )
        return subject._canonical(
            {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")},
            "TEST_INVALID",
        )

    def _verify(self, *, number: int = 1, raw=None, config=None, admission=None):
        index = number - 1
        return subject.verify_physical_wal_v2r_provider_route_iam_object_lock_evidence(
            config=self.configs[index] if config is None else config,
            admission=self.admissions[index] if admission is None else admission,
            provider_evidence=self.raw_evidences[index] if raw is None else raw,
            now=NOW,
        )

    def test_exact_eight_role_prefix_action_and_object_lock_matrix_is_non_operational(self) -> None:
        matrix = subject.verify_physical_wal_v2r_provider_route_iam_object_lock_evidence_matrix(
            evidences=tuple(self.verified),
            configs=tuple(self.configs),
            admissions=tuple(self.admissions),
            admission_configs=tuple(self.admission_configs),
            now=NOW,
        )
        self.assertEqual(_hash("deployment"), matrix.deployment_binding_sha256)
        self.assertEqual(8, len({item.role_credential_identity_sha256 for item in self.verified}))
        for item, policy in zip(
            self.verified,
            mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES,
            strict=True,
        ):
            self.assertEqual(policy.local_role, item.local_role)
            self.assertEqual(policy.object_prefix, item.object_prefix)
            self.assertEqual(policy.least_privilege_actions, item.least_privilege_actions)
            self.assertFalse(item.is_operational)
            self.assertFalse(item.writer_authorized)
            self.assertFalse(item.phase5_authorized)
            self.assertFalse(item.full_matrix_authorized)
        self.assertFalse(matrix.is_operational)
        self.assertFalse(matrix.execution_authorized)
        self.assertFalse(matrix.full_matrix_executed)

    def test_signed_role_prefix_action_route_and_object_lock_substitutions_fail_closed(self) -> None:
        for field, value in (
            ("local_site", "wa-fi"),
            ("object_prefix", "physical-wal-v2r-reverse/witness-to-fi/"),
            ("least_privilege_actions", ["object:list-fixed-prefix"]),
            ("allowed_provider_actions", ["s3:DeleteObject"]),
            ("provider_endpoint_sha256", _hash("other-endpoint")),
            ("role_iam_policy_sha256", _hash("other-iam")),
            ("object_lock_retention_proof_sha256", _hash("other-lock")),
            ("versioning_enabled", False),
            ("object_lock_mode", "GOVERNANCE"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
                "CROSS_PIN_MISMATCH",
            ):
                self._verify(raw=self._provider_evidence(self.configs[0], self.admissions[0], 1, **{field: value}))

    def test_default_off_stale_normal_v2_alias_and_provider_signer_reuse_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "CONFIG_INVALID",
        ):
            self._verify(config=replace(self.configs[0], enabled=False))
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "NORMAL_V2_SIGNER_REUSED",
        ):
            self._verify(
                config=replace(
                    self.configs[0],
                    normal_v2_provider_evidence_authority_public_key_sha256=hashlib.sha256(
                        _public(self.provider_authority)
                    ).hexdigest(),
                )
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "NORMAL_V2_REUSED",
        ):
            self._verify(
                config=replace(
                    self.configs[0],
                    normal_v2_iam_catalog_sha256=self.admission_configs[0].v2r_iam_catalog_sha256,
                )
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "STALE",
        ):
            self._verify(
                raw=self._provider_evidence(
                    self.configs[0], self.admissions[0], 1, expires_at="2026-08-01T12:00:00Z"
                )
            )

    def test_capability_is_nonserializable_reverified_and_tamper_checked(self) -> None:
        verified = self.verified[0]
        self.assertIsNot(
            verified,
            subject.require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence(
                evidence=verified,
                config=self.configs[0],
                admission=self.admissions[0],
                now=NOW,
            ),
        )
        with self.assertRaises(TypeError):
            subject.VerifiedPhysicalWalV2rProviderRouteIamObjectLockEvidence(
                configuration_sha256="x", capability=object()
            )
        object.__setattr__(verified, "object_prefix", "tampered/")
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "CAPABILITY_INVALID",
        ):
            subject.require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence(
                evidence=verified,
                config=self.configs[0],
                admission=self.admissions[0],
                now=NOW,
            )

    def test_matrix_requires_complete_ordered_admission_bound_roles(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "MATRIX_INCOMPLETE",
        ):
            subject.verify_physical_wal_v2r_provider_route_iam_object_lock_evidence_matrix(
                evidences=tuple(self.verified[:7]),
                configs=tuple(self.configs[:7]),
                admissions=tuple(self.admissions[:7]),
                admission_configs=tuple(self.admission_configs[:7]),
                now=NOW,
            )
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rProviderRouteIamObjectLockEvidenceError,
            "MATRIX_ADMISSION_INVALID",
        ):
            subject.verify_physical_wal_v2r_provider_route_iam_object_lock_evidence_matrix(
                evidences=tuple(self.verified),
                configs=tuple(self.configs),
                admissions=tuple([self.admissions[0], *self.admissions[0:1], *self.admissions[2:]]),
                admission_configs=tuple([self.admission_configs[0], *self.admission_configs[0:1], *self.admission_configs[2:]]),
                now=NOW,
            )

    def test_source_isolated_from_provider_runtime_and_normal_v2_modules(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        self.assertEqual(
            {
                "__future__",
                "base64",
                "binascii",
                "dataclasses",
                "datetime",
                "hashlib",
                "json",
                "re",
                "typing",
                "weakref",
                "cryptography.exceptions",
                "cryptography.hazmat.primitives.asymmetric.ed25519",
                "core",
            },
            {name.split(".", 1)[0] if name.startswith("core.") else name for name in imports},
        )
        text = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("boto3", "socket", "subprocess", "requests", "urllib", "Path(", "open("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
