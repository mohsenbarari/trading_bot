"""Static non-alias gate for the unfinished Phase-5 V4R mailbox plane.

This test deliberately does not model an S3 client, credential, or deployment.
It records the current fail-closed boundary: recovery-data identities, normal
V2 mailbox identities, and the evidence-only V2R carrier are three different
planes.  A future V2R deployment implementation must replace the final
absence assertion with its own complete eight-role admission/runtime tests;
it must not silently extend a normal-V2 module.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core import physical_arvan_s3_role_profiles as recovery_profiles
from core import physical_wal_v2_witness_roundtrip_mailbox_admission as normal_v2
from core import physical_wal_v2r_witness_roundtrip_contract as v2r


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"


class PhysicalFullMatrixV4rPhase5ReverseControlMailboxGapTests(unittest.TestCase):
    def test_data_recovery_normal_v2_and_v2r_role_sets_are_exact_and_disjoint(self) -> None:
        """No existing credential/profile role can be relabelled as V2R control."""

        recovery_roles = {
            recovery_profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
            recovery_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            recovery_profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
            recovery_profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        }
        self.assertEqual(
            {"fi-publisher", "ir-receiver", "ir-publisher", "fi-receiver"},
            recovery_roles,
        )

        normal_roles = {
            policy.local_role
            for policy in normal_v2.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
        }
        self.assertEqual(
            {
                "fi-writer-source-outbox",
                "witness-fi-ingress",
                "witness-ir-egress",
                "ir-standby-ack-inbox",
                "ir-durable-ack-outbox",
                "witness-ir-ingress",
                "witness-fi-egress",
                "fi-writer-ack-inbox",
            },
            normal_roles,
        )

        v2r_roles = {
            role
            for hop in v2r._HOPS.values()
            for role in hop[2:]
        }
        self.assertEqual(
            {
                "wa-ir-v2r-exporter",
                "witness-v2r-reverse-ingress",
                "witness-v2r-reverse-egress",
                "wa-fi-v2r-recovery-inbox",
                "wa-fi-v2r-ack-outbox",
                "witness-v2r-ack-ingress",
                "witness-v2r-return-egress",
                "wa-ir-v2r-return-inbox",
            },
            v2r_roles,
        )
        self.assertTrue(recovery_roles.isdisjoint(normal_roles))
        self.assertTrue(recovery_roles.isdisjoint(v2r_roles))
        self.assertTrue(normal_roles.isdisjoint(v2r_roles))

        normal_prefixes = {
            policy.object_prefix
            for policy in normal_v2.PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_MAILBOX_POLICIES
        }
        self.assertTrue(
            all(
                prefix.startswith("physical-wal-v2-witness-roundtrip-delivery-v1/")
                for prefix in normal_prefixes
            )
        )
        self.assertNotIn(
            v2r.PHYSICAL_WAL_V2R_WITNESS_ROUNDTRIP_MAILBOX_PREFIX,
            normal_prefixes,
        )

    def test_normal_v2_deployment_surfaces_cannot_import_the_v2r_carrier(self) -> None:
        """The only safe migration is a fresh V2R control-plane generation."""

        normal_surfaces = (
            "physical_wal_v2_witness_roundtrip_mailbox_admission.py",
            "physical_wal_v2_witness_roundtrip_delivery_contract.py",
            "physical_wal_v2_witness_roundtrip_delivery_runtime.py",
            "physical_wal_v2_witness_roundtrip_s3_mailbox_adapter.py",
            "physical_wal_v2_witness_roundtrip_arvan_s3v4_scope.py",
            "physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher.py",
            "physical_wal_v2_witness_roundtrip_deployment_plan.py",
            "physical_wal_v2_witness_roundtrip_full_bundle_issuer.py",
            "physical_wal_v2_witness_roundtrip_full_bundle_deployment_reference.py",
        )
        for filename in normal_surfaces:
            with self.subTest(filename=filename):
                tree = ast.parse((CORE / filename).read_text(encoding="utf-8"))
                imports = {
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in node.names
                }
                self.assertFalse(
                    any("physical_wal_v2r_witness_roundtrip" in name for name in imports),
                    msg=f"normal V2 surface {filename} must not become a V2R alias",
                )

    def test_v2r_control_plane_is_a_fresh_admission_profile_seam_not_a_v2_alias(self) -> None:
        """Profiles remain pure evidence, never provider/adapter/runtime code."""

        current = {
            path.name
            for path in CORE.glob("physical_wal_v2r_witness_roundtrip_*.py")
        }
        self.assertEqual(
            {
                "physical_wal_v2r_witness_roundtrip_contract.py",
                "physical_wal_v2r_witness_roundtrip_durable_anti_replay.py",
                "physical_wal_v2r_witness_roundtrip_control_mailbox_admission.py",
                "physical_wal_v2r_witness_roundtrip_control_mailbox_profile.py",
                "physical_wal_v2r_witness_roundtrip_full_bundle_manifest_admission.py",
                "physical_wal_v2r_witness_roundtrip_public_full_bundle_issuer.py",
                "physical_wal_v2r_witness_roundtrip_public_site_manifest_renderer.py",
                "physical_wal_v2r_witness_roundtrip_public_site_manifest_set_preparation.py",
                "physical_wal_v2r_witness_roundtrip_provider_route_iam_object_lock_evidence.py",
                "physical_wal_v2r_witness_roundtrip_root_owned_role_credential_admission.py",
            },
            current,
        )


if __name__ == "__main__":
    unittest.main()
