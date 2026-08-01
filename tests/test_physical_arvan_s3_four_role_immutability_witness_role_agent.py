"""Local-only tests for the root Witness role agent.

The collector is mocked at its semantic boundary.  No S3 credential, SDK,
network transport, peer endpoint, or provider call is reached.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from cryptography.hazmat.primitives import serialization

from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as probe
from core import physical_arvan_s3_four_role_immutability_role_local_collector as collector
from core import physical_arvan_s3_four_role_immutability_witness_orchestration as orchestration
from core import physical_arvan_s3_four_role_immutability_witness_role_agent as agent_module
from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_role_local_identity as identity_module
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as failback
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


CAMPAIGN = "four-role-witness-agent-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 22, 30, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_witness_role_agent.py"
)


def _public_key(signer: object) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _private_bytes(signer: object) -> bytes:
    return signer.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


class PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.role = profiles.ARVAN_S3_FI_PUBLISHER_ROLE
        self.failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256="4" * 64,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            fi_publisher_identity_sha256="5" * 64,
            ir_receiver_identity_sha256="6" * 64,
            ir_publisher_identity_sha256="7" * 64,
            fi_receiver_identity_sha256="8" * 64,
        )
        self.fixture = make_four_role_live_iam_durable_admission_fixture(
            binding=self.failback_binding,
            observed_at=NOW,
        )
        preflight_binding = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-four-role-witness-agent",
            bucket_access_posture="private",
            normal_object_storage_namespace="physical-wal",
            reverse_object_storage_namespace="physical-failback",
            minimum_retention_days=90,
            normal_route_scope_sha256=self.fixture.live_iam_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.fixture.live_iam_binding.reverse_route_scope_sha256,
            four_role_route_binding_sha256=self.fixture.live_iam_binding.four_role_binding_sha256,
            fi_publisher_identity_sha256=self.fixture.live_iam_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.fixture.live_iam_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.fixture.live_iam_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.fixture.live_iam_binding.fi_receiver_identity_sha256,
        )
        self.binding = orchestration.build_physical_arvan_s3_four_role_immutability_witness_binding(
            preflight_binding=preflight_binding,
            live_iam_binding=self.fixture.live_iam_binding,
            failback_binding=self.failback_binding,
            witness_public_key=_public_key(self.fixture.witness_signer),
        )

    def _approval(self) -> bytes:
        return orchestration.issue_physical_arvan_s3_four_role_immutability_initial_witness_approval(
            binding=self.binding,
            admission=self.fixture.live_iam_durable_admission,
            operation_nonce_sha256="a" * 64,
            normal_probe_nonce_sha256="b" * 64,
            issued_at=NOW,
            witness_signer=self.fixture.witness_signer,
        )

    def _projection(self) -> identity_module.ArvanS3RoleLocalIdentityProjection:
        return identity_module.ArvanS3RoleLocalIdentityProjection(
            schema=identity_module.PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            role=self.role,
            identity_sha256=self.binding.preflight_binding.fi_publisher_identity_sha256,
            action_profile=profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
        )

    def _readback(
        self, request: probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest
    ) -> probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
        return probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
            schema=probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=request.probe_nonce_sha256,
            object_key=request.object_key,
            object_version_id="version-agent-normal-001",
            content_sha256="a" * 64,
            content_bytes=256,
            retention_until=request.retention_not_before,
            create_only_outcome="create-only-succeeded",
            overwrite_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_readback=probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
                acl_posture="private-canonical-owner-only-v1",
                versioning_status="Enabled",
                retention_mode="s3-object-lock-compliance-v1",
                retention_days=90,
            ),
        )

    def _agent(self, *, state_root: Path, key_path: Path):
        # The real class is required by the agent's exact type check; its two
        # external methods are patched below at the semantic boundary.
        local_collector = object.__new__(
            collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector
        )
        agent = agent_module.RootOwnedPhysicalArvanS3FourRoleImmutabilityWitnessRoleAgent(
            agent_module.PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentConfig(
                role=self.role,
                binding=self.binding,
                collector=local_collector,
                enabled=True,
            )
        )
        return local_collector, agent

    def test_root_agent_runs_one_local_collector_then_persists_signed_receipt(self) -> None:
        approval = self._approval()
        with tempfile.TemporaryDirectory(prefix="four-role-witness-agent-") as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            os.chmod(state_root, 0o700)
            key_path = root / "role.key"
            key_path.write_bytes(_private_bytes(self.fixture.role_signers[self.role]))
            os.chmod(key_path, 0o600)
            local_collector, agent = self._agent(state_root=state_root, key_path=key_path)
            with (
                mock.patch.object(agent_module.os, "geteuid", return_value=0),
                mock.patch.object(agent_module, "_host_now", return_value=NOW + timedelta(seconds=1)),
                mock.patch.dict(
                    agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE,
                    {self.role: state_root},
                ),
                mock.patch.dict(
                    agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE,
                    {self.role: key_path},
                ),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "identity_projection",
                    return_value=self._projection(),
                ) as projected,
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "collect",
                    side_effect=self._readback,
                ) as collected,
            ):
                receipt_raw = agent.execute(approval=approval)
                verified_approval = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                    approval, binding=self.binding, observed_at=NOW + timedelta(seconds=1)
                )
                receipt = orchestration.verify_physical_arvan_s3_four_role_immutability_role_receipt(
                    receipt_raw,
                    binding=self.binding,
                    approval=verified_approval,
                    observed_at=NOW + timedelta(seconds=1),
                )
                self.assertEqual(self.role, receipt.stage)
                projected.assert_called_once_with()
                collected.assert_called_once_with(verified_approval.approval.request)
                self.assertTrue((state_root / (verified_approval.approval.raw_sha256 + ".reserved")).is_file())
                self.assertTrue((state_root / (verified_approval.approval.raw_sha256 + ".receipt")).is_file())
                with self.assertRaisesRegex(
                    agent_module.PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError,
                    "REPLAY_OR_IN_PROGRESS",
                ):
                    agent.execute(approval=approval)
                collected.assert_called_once()

    def test_expired_delivery_on_host_clock_never_opens_local_identity(self) -> None:
        approval = self._approval()
        with tempfile.TemporaryDirectory(prefix="four-role-witness-agent-") as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            os.chmod(state_root, 0o700)
            key_path = root / "role.key"
            key_path.write_bytes(_private_bytes(self.fixture.role_signers[self.role]))
            os.chmod(key_path, 0o600)
            local_collector, agent = self._agent(state_root=state_root, key_path=key_path)
            with (
                mock.patch.object(agent_module.os, "geteuid", return_value=0),
                mock.patch.object(agent_module, "_host_now", return_value=NOW + timedelta(seconds=121)),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE, {self.role: state_root}),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE, {self.role: key_path}),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "identity_projection",
                ) as projected,
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "collect",
                ) as collected,
            ):
                # The public API has no caller-supplied time.  The patched
                # private host clock is beyond the approval expiry, so even a
                # previously unseen approval cannot reach the collector.
                with self.assertRaises(agent_module.PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError):
                    agent.execute(approval=approval)
                projected.assert_not_called()
                collected.assert_not_called()

    def test_clock_expiry_after_durable_reservation_never_reaches_collector(self) -> None:
        approval = self._approval()
        verified = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            approval, binding=self.binding, observed_at=NOW
        )
        with tempfile.TemporaryDirectory(prefix="four-role-witness-agent-") as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            os.chmod(state_root, 0o700)
            key_path = root / "role.key"
            key_path.write_bytes(_private_bytes(self.fixture.role_signers[self.role]))
            os.chmod(key_path, 0o600)
            local_collector, agent = self._agent(state_root=state_root, key_path=key_path)
            with (
                mock.patch.object(agent_module.os, "geteuid", return_value=0),
                mock.patch.object(
                    agent_module,
                    "_host_now",
                    side_effect=[NOW + timedelta(seconds=1), NOW + timedelta(seconds=121)],
                ),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE, {self.role: state_root}),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE, {self.role: key_path}),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "identity_projection",
                    return_value=self._projection(),
                ),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "collect",
                ) as collected,
            ):
                with self.assertRaises(agent_module.PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError):
                    agent.execute(approval=approval)
                collected.assert_not_called()
            self.assertTrue(
                (state_root / (verified.approval.raw_sha256 + ".reserved")).is_file()
            )

    def test_missing_pinned_signer_fails_before_reservation_or_collector(self) -> None:
        approval = self._approval()
        verified = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            approval, binding=self.binding, observed_at=NOW
        )
        with tempfile.TemporaryDirectory(prefix="four-role-witness-agent-") as temporary:
            root = Path(temporary)
            os.chmod(root, 0o700)
            state_root = root / "state"
            state_root.mkdir(mode=0o700)
            os.chmod(state_root, 0o700)
            missing_key = root / "missing.key"
            local_collector, agent = self._agent(state_root=state_root, key_path=missing_key)
            with (
                mock.patch.object(agent_module.os, "geteuid", return_value=0),
                mock.patch.object(agent_module, "_host_now", return_value=NOW + timedelta(seconds=1)),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_STATE_DIR_BY_ROLE, {self.role: state_root}),
                mock.patch.dict(agent_module.FIXED_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_WITNESS_ROLE_AGENT_ATTESTATION_KEY_FILE_BY_ROLE, {self.role: missing_key}),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "identity_projection",
                    return_value=self._projection(),
                ),
                mock.patch.object(
                    collector.RootOwnedPhysicalArvanS3FourRoleImmutabilityRoleLocalCollector,
                    "collect",
                ) as collected,
            ):
                with self.assertRaisesRegex(
                    agent_module.PhysicalArvanS3FourRoleImmutabilityWitnessRoleAgentError,
                    "KEY_UNAVAILABLE",
                ):
                    agent.execute(approval=approval)
                collected.assert_not_called()
            self.assertFalse(
                (state_root / (verified.approval.raw_sha256 + ".reserved")).exists()
            )

    def test_source_has_no_peer_transport_or_generic_callback_surface(self) -> None:
        import ast

        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"socket", "subprocess", "requests", "urllib", "boto3"} & imported)
        self.assertNotIn("readback_adapter", source)


if __name__ == "__main__":
    unittest.main()
