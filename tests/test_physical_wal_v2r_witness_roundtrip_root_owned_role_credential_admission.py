"""Adversarial tests for the no-secret V2R fixed-file credential boundary.

The dependent V2R proof verifiers are patched only after their typed config
objects have been supplied.  These tests never create an S3/provider client,
open a socket, or expose a credential from the resulting admission.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tempfile
import unittest
from contextlib import ExitStack

from core import physical_wal_v2r_witness_roundtrip_control_mailbox_admission as mailbox
from core import physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission as bundle
from core import physical_wal_v2r_witness_roundtrip_provider_route_iam_object_lock_evidence as provider
from core import physical_wal_v2r_witness_roundtrip_root_owned_role_credential_admission as subject


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wal_v2r_witness_roundtrip_root_owned_role_credential_admission.py"
)


class PhysicalWalV2rRootOwnedRoleCredentialAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "physical-wal-v2r"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.policy = mailbox.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_CONTROL_MAILBOX_POLICIES[0]
        self.access_key = "V2R-ACCESS-KEY-ONLY-FOR-LOCAL-TEST"
        self.identity = subject._identity(self.access_key)
        self.admission_config = (
            mailbox.PhysicalWalV2rWitnessRoundtripControlMailboxAdmissionConfig(
                host_id="v2r-credential-admission-host-001",
                local_site=self.policy.local_site,
                local_role=self.policy.local_role,
                release_sha256="a" * 64,
                deployment_binding_sha256="b" * 64,
                delivery_binding_sha256="c" * 64,
                v2r_iam_catalog_sha256="d" * 64,
                role_credential_identity_sha256=self.identity,
                non_v2r_credential_identity_sha256s=tuple(
                    hashlib.sha256(f"legacy:{number}".encode()).hexdigest()
                    for number in range(12)
                ),
                host_role_authority_public_key=b"P" * 32,
                enabled=True,
            )
        )
        self.provider_config = (
            provider.PhysicalWalV2rProviderRouteIamObjectLockEvidenceConfig(
                admission_config=self.admission_config,
                provider_evidence_authority_public_key=b"Q" * 32,
                provider_endpoint_sha256="e" * 64,
                provider_bucket_sha256="f" * 64,
                provider_region_sha256="1" * 64,
                object_lock_minimum_retention_seconds=86_400,
                normal_v2_provider_evidence_authority_public_key_sha256="2" * 64,
                normal_v2_mailbox_prefix="normal-v2-deny-pin/",
                normal_v2_iam_catalog_sha256="3" * 64,
                enabled=True,
            )
        )
        self.bundle_config = (
            bundle.PhysicalWalV2rWitnessRoundtripFullBundleManifestAdmissionConfig(
                release_sha256="a" * 64,
                deployment_binding_sha256="b" * 64,
                delivery_binding_sha256="c" * 64,
                v2r_iam_catalog_sha256="d" * 64,
                bundle_authority_public_key=b"R" * 32,
                enabled=True,
            )
        )
        self.config = subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig(
            admission_config=self.admission_config,
            provider_evidence_config=self.provider_config,
            full_bundle_config=self.bundle_config,
            enabled=True,
        )
        self.admission = SimpleNamespace(
            local_site=self.policy.local_site,
            local_role=self.policy.local_role,
            mailbox=self.policy.mailbox,
            direction=self.policy.direction,
            object_prefix=self.policy.object_prefix,
            least_privilege_actions=self.policy.least_privilege_actions,
            role_credential_identity_sha256=self.identity,
            role_iam_policy_sha256="4" * 64,
            provider_route_iam_attestation_sha256="5" * 64,
            object_lock_retention_proof_sha256="6" * 64,
        )
        self.evidence = SimpleNamespace(
            local_site=self.policy.local_site,
            local_role=self.policy.local_role,
            mailbox=self.policy.mailbox,
            object_prefix=self.policy.object_prefix,
            role_credential_identity_sha256=self.identity,
            evidence_sha256="7" * 64,
        )
        self.full_bundle = SimpleNamespace(full_bundle_sha256="8" * 64)

    def _write(self, **changes: object) -> Path:
        values = {
            "schema": "gold-trade-physical-wal-v2r-role-credential-v1",
            "protocol_domain": "gold-trade-physical-wal-v2r-witness-reverse-carrier-v1",
            "local_site": self.policy.local_site,
            "local_role": self.policy.local_role,
            "mailbox": self.policy.mailbox,
            "object_prefix": self.policy.object_prefix,
            "access_key": self.access_key,
            "secret_key": "TEST-SECRET-MUST-NOT-LEAVE-THIS-FILE",
        }
        values.update(changes)
        path = self.root / "wa-ir-v2r-exporter.json"
        path.write_bytes(json.dumps(values, sort_keys=True, separators=(",", ":")).encode())
        path.chmod(0o600)
        return path

    def _calls(self):
        role = {
            "local_site": self.admission.local_site,
            "local_role": self.admission.local_role,
            "mailbox": self.admission.mailbox,
            "direction": self.admission.direction,
            "object_prefix": self.admission.object_prefix,
            "role_credential_identity_sha256": self.admission.role_credential_identity_sha256,
            "role_iam_policy_sha256": self.admission.role_iam_policy_sha256,
            "provider_route_iam_attestation_sha256": self.admission.provider_route_iam_attestation_sha256,
            "object_lock_retention_proof_sha256": self.admission.object_lock_retention_proof_sha256,
        }
        return (
            mock.patch.object(
                subject._mailbox,
                "require_verified_physical_wal_v2r_witness_roundtrip_control_mailbox_admission",
                return_value=self.admission,
            ),
            mock.patch.object(
                subject._provider,
                "require_verified_physical_wal_v2r_provider_route_iam_object_lock_evidence",
                return_value=self.evidence,
            ),
            mock.patch.object(
                subject._bundle,
                "require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle",
                return_value=self.full_bundle,
            ),
            mock.patch.object(
                subject._bundle,
                "require_verified_physical_wal_v2r_witness_roundtrip_public_full_bundle_site_manifest_slice",
                return_value=(self.full_bundle, (role,)),
            ),
        )

    def _admit(self):
        patches = self._calls()
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    subject,
                    "PHYSICAL_WAL_V2R_ROOT_OWNED_ROLE_CREDENTIAL_ROOT",
                    self.root,
                )
            )
            for patch in patches:
                stack.enter_context(patch)
            return subject.admit_root_owned_physical_wal_v2r_witness_roundtrip_role_credential(
                config=self.config,
                admission=object(),
                provider_evidence=object(),
                full_bundle=object(),
                now=NOW,
            )

    def test_exact_fixed_v2r_file_is_private_no_secret_and_non_operational(self) -> None:
        self._write()
        result = self._admit()
        self.assertIs(
            result,
            subject.require_verified_root_owned_physical_wal_v2r_witness_roundtrip_role_credential_admission(
                credential_admission=result, config=self.config
            ),
        )
        self.assertEqual(self.identity, result.role_credential_identity_sha256)
        self.assertFalse(result.is_operational)
        self.assertFalse(result.phase5_authorized)
        self.assertFalse(result.full_matrix_executed)
        self.assertNotIn("TEST-SECRET", repr(result))
        self.assertNotIn("access_key", {item.name for item in fields(result)})
        self.assertNotIn("secret_key", {item.name for item in fields(result)})
        with self.assertRaises(TypeError):
            result.__reduce_ex__(4)

    def test_disabled_wrong_identity_scope_or_linked_file_fail_closed(self) -> None:
        self._write()
        disabled = subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionConfig()
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionError,
            "CONFIG_INVALID",
        ):
            subject.admit_root_owned_physical_wal_v2r_witness_roundtrip_role_credential(
                config=disabled,
                admission=object(), provider_evidence=object(), full_bundle=object(), now=NOW,
            )
        with self.subTest("normal-or-recovery-identity-is-not-a-v2r-file-identity"):
            self._write(access_key="NORMAL-OR-RECOVERY-IDENTITY")
            with self.assertRaisesRegex(
                subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionError,
                "IDENTITY_MISMATCH",
            ):
                self._admit()
        path = self._write()
        replacement = self.root / "replacement.json"
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        path.unlink()
        path.symlink_to(replacement)
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionError,
            "FILE_INVALID",
        ):
            self._admit()

    def test_non_v2r_role_cannot_select_a_legacy_path(self) -> None:
        with self.assertRaisesRegex(
            subject.PhysicalWalV2rRootOwnedRoleCredentialAdmissionError,
            "ROLE_INVALID",
        ):
            subject._fixed_path(local_role="fi-publisher")

    def test_module_has_no_provider_client_or_legacy_transport_import(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        forbidden = (
            "boto3", "botocore", "requests", "socket",
            "physical_wal_v2_witness_roundtrip", "physical_arvan_s3",
        )
        for item in forbidden:
            self.assertFalse(any(item in name for name in imports), item)


if __name__ == "__main__":
    unittest.main()
