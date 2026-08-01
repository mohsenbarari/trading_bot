"""Adversarial tests for the Witness-mediated four-host probe grammar.

These tests deliberately use semantic readbacks, Ed25519 test keys, and the
existing durable-admission fixture only.  They create no S3 client, socket,
credential file, subprocess, or peer-to-peer delivery path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_immutability_live_probe_runtime as probe
from core import physical_arvan_s3_four_role_immutability_preflight as immutable
from core import physical_arvan_s3_four_role_immutability_witness_orchestration as orchestration
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as failback
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)


CAMPAIGN = "four-role-witness-orchestration-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 22, 0, 0, tzinfo=timezone.utc)
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_immutability_witness_orchestration.py"
)


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


class PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.preflight_binding = immutable.PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
            region="ir-thr-at1",
            bucket="private-four-role-witness",
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
            preflight_binding=self.preflight_binding,
            live_iam_binding=self.fixture.live_iam_binding,
            failback_binding=self.failback_binding,
            witness_public_key=_public_key(self.fixture.witness_signer),
        )

    def _publisher_readback(
        self,
        request: probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest,
    ) -> probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback:
        bucket = (
            probe.PhysicalArvanS3FourRoleImmutabilityBucketReadback(
                acl_posture="private-canonical-owner-only-v1",
                versioning_status="Enabled",
                retention_mode="s3-object-lock-compliance-v1",
                retention_days=90,
            )
            if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE
            else None
        )
        return probe.PhysicalArvanS3FourRoleImmutabilityPublisherReadback(
            schema=probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=request.probe_nonce_sha256,
            object_key=request.object_key,
            object_version_id=(
                "version-normal-witness-001"
                if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE
                else "version-reverse-witness-001"
            ),
            content_sha256=("a" if request.role == profiles.ARVAN_S3_FI_PUBLISHER_ROLE else "b")
            * 64,
            content_bytes=512,
            retention_until=request.retention_not_before,
            create_only_outcome="create-only-succeeded",
            overwrite_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_readback=bucket,
        )

    def _receiver_readback(
        self,
        request: probe.PhysicalArvanS3FourRoleImmutabilityReceiverProbeRequest,
    ) -> probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback:
        version = request.immutable_version
        return probe.PhysicalArvanS3FourRoleImmutabilityReceiverReadback(
            schema=probe.PHYSICAL_ARVAN_S3_FOUR_ROLE_IMMUTABILITY_LIVE_PROBE_SCHEMA,
            direction=request.direction,
            role=request.role,
            identity_sha256=request.identity_sha256,
            probe_nonce_sha256=version.probe_nonce_sha256,
            object_key=version.object_key,
            object_version_id=version.object_version_id,
            exact_head_version_id=version.object_version_id,
            exact_get_version_id=version.object_version_id,
            exact_get_content_sha256=version.content_sha256,
            exact_get_content_bytes=version.content_bytes,
            put_outcome="access-denied",
            object_removal_outcome="access-denied",
            version_removal_outcome="access-denied",
            bucket_enumeration_outcome="access-denied",
            version_enumeration_outcome="access-denied",
        )

    def _initial(self):
        raw = orchestration.issue_physical_arvan_s3_four_role_immutability_initial_witness_approval(
            binding=self.binding,
            admission=self.fixture.live_iam_durable_admission,
            operation_nonce_sha256="a" * 64,
            normal_probe_nonce_sha256="b" * 64,
            issued_at=NOW,
            witness_signer=self.fixture.witness_signer,
        )
        return raw, orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            raw,
            binding=self.binding,
            observed_at=NOW,
        )

    def _seal_and_verify(self, approval, *, at: datetime):
        request = approval.approval.request
        if type(request) is probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest:
            readback = self._publisher_readback(request)
        else:
            readback = self._receiver_readback(request)
        raw = orchestration.seal_physical_arvan_s3_four_role_immutability_role_receipt(
            approval=approval,
            binding=self.binding,
            observed_at=at,
            local_readback=readback,
            role_signer=self.fixture.role_signers[request.role],
        )
        return raw, orchestration.verify_physical_arvan_s3_four_role_immutability_role_receipt(
            raw,
            binding=self.binding,
            approval=approval,
            observed_at=at,
        )

    def test_full_four_role_chain_is_signed_and_witness_mediated(self) -> None:
        _initial_raw, initial = self._initial()
        self.assertEqual(profiles.ARVAN_S3_FI_PUBLISHER_ROLE, initial.approval.stage)
        _fi_raw, fi_receipt = self._seal_and_verify(initial, at=NOW + timedelta(seconds=1))

        ir_request_raw = orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
            binding=self.binding,
            prior_receipt=fi_receipt,
            issued_at=NOW + timedelta(seconds=2),
            witness_signer=self.fixture.witness_signer,
        )
        ir_request = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            ir_request_raw,
            binding=self.binding,
            observed_at=NOW + timedelta(seconds=2),
        )
        self.assertEqual(profiles.ARVAN_S3_IR_RECEIVER_ROLE, ir_request.approval.stage)
        self.assertEqual(fi_receipt.raw_sha256, ir_request.approval.prior_receipt_sha256)
        self.assertEqual(fi_receipt.raw_sha256, ir_request.approval.normal_publisher_receipt_sha256)
        _ir_read_raw, ir_read_receipt = self._seal_and_verify(
            ir_request, at=NOW + timedelta(seconds=3)
        )

        ir_publish_raw = orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
            binding=self.binding,
            prior_receipt=ir_read_receipt,
            issued_at=NOW + timedelta(seconds=4),
            reverse_probe_nonce_sha256="c" * 64,
            witness_signer=self.fixture.witness_signer,
        )
        ir_publish = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            ir_publish_raw,
            binding=self.binding,
            observed_at=NOW + timedelta(seconds=4),
        )
        self.assertEqual(profiles.ARVAN_S3_IR_PUBLISHER_ROLE, ir_publish.approval.stage)
        _ir_publish_raw, ir_publish_receipt = self._seal_and_verify(
            ir_publish, at=NOW + timedelta(seconds=5)
        )

        fi_read_raw = orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
            binding=self.binding,
            prior_receipt=ir_publish_receipt,
            issued_at=NOW + timedelta(seconds=6),
            witness_signer=self.fixture.witness_signer,
        )
        fi_read = orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
            fi_read_raw,
            binding=self.binding,
            observed_at=NOW + timedelta(seconds=6),
        )
        self.assertEqual(profiles.ARVAN_S3_FI_RECEIVER_ROLE, fi_read.approval.stage)
        _fi_read_raw, fi_read_receipt = self._seal_and_verify(fi_read, at=NOW + timedelta(seconds=7))
        self.assertEqual(profiles.ARVAN_S3_FI_RECEIVER_ROLE, fi_read_receipt.stage)
        observation = orchestration.build_physical_arvan_s3_four_role_immutability_witness_mediated_preflight_observation(
            binding=self.binding,
            admission=self.fixture.live_iam_durable_admission,
            fi_publisher_receipt=fi_receipt,
            ir_receiver_receipt=ir_read_receipt,
            ir_publisher_receipt=ir_publish_receipt,
            fi_receiver_receipt=fi_read_receipt,
            observed_at=NOW + timedelta(seconds=7),
        )
        self.assertEqual("four-role-immutable-observed", observation.status)
        self.assertEqual(
            fi_receipt.readback.object_key,
            observation.normal_direction.immutable_version.object_key,
        )
        self.assertEqual(
            ir_publish_receipt.readback.object_key,
            observation.reverse_direction.immutable_version.object_key,
        )
        with self.assertRaisesRegex(
            orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError,
            "CHAIN_COMPLETE",
        ):
            orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
                binding=self.binding,
                prior_receipt=fi_read_receipt,
                issued_at=NOW + timedelta(seconds=8),
                witness_signer=self.fixture.witness_signer,
            )

    def test_initial_request_is_only_fi_publisher_and_bounded_to_admission(self) -> None:
        _raw, initial = self._initial()
        request = initial.approval.request
        self.assertIsInstance(request, probe.PhysicalArvanS3FourRoleImmutabilityPublisherProbeRequest)
        self.assertEqual("fi-publisher", request.role)
        self.assertEqual(self.fixture.live_iam_durable_admission.expires_at, initial.approval.expires_at)
        self.assertEqual("physical-wal", request.object_storage_namespace)
        self.assertIn("fi-publisher-to-ir-receiver", request.object_key)

    def test_wrong_role_signer_is_rejected_before_receipt_is_sealed(self) -> None:
        _raw, initial = self._initial()
        with self.assertRaisesRegex(
            orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError,
            "ROLE_SIGNER_NOT_PINNED",
        ):
            orchestration.seal_physical_arvan_s3_four_role_immutability_role_receipt(
                approval=initial,
                binding=self.binding,
                observed_at=NOW + timedelta(seconds=1),
                local_readback=self._publisher_readback(initial.approval.request),
                role_signer=Ed25519PrivateKey.generate(),
            )

    def test_tampered_or_noncanonical_approval_is_fail_closed(self) -> None:
        raw, _initial = self._initial()
        tampered = raw.replace(b'"stage":"fi-publisher"', b'"stage":"ir-publisher"')
        with self.assertRaises(orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError):
            orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                tampered,
                binding=self.binding,
                observed_at=NOW,
            )
        duplicate = raw[:-1] + b',"stage":"fi-publisher"}'
        with self.assertRaises(orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError):
            orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                duplicate,
                binding=self.binding,
                observed_at=NOW,
            )

    def test_validly_signed_short_retention_floor_is_rejected(self) -> None:
        """A compromised/misconfigured Witness signer cannot weaken the floor.

        This is deliberately re-signed with the genuine fixture Witness key:
        a normal signature check alone is not considered sufficient proof that
        the explicit 300-second cross-host bound was retained.
        """

        raw, _initial = self._initial()
        sealed = json.loads(raw.decode("ascii"))
        unsigned = {key: value for key, value in sealed.items() if key != "witness_signature"}
        unsigned["request"]["retention_not_before"] = (
            NOW + timedelta(days=90)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        short_floor = orchestration._seal(
            unsigned=unsigned,
            signer=self.fixture.witness_signer,
            signer_field="witness_signer",
            signature_field="witness_signature",
            kind="witness-approved-role-local-request",
        )
        with self.assertRaisesRegex(
            orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError,
            "APPROVAL_CHAIN_INVALID",
        ):
            orchestration.verify_physical_arvan_s3_four_role_immutability_witness_approval(
                short_floor,
                binding=self.binding,
                observed_at=NOW,
            )

    def test_next_stage_rejects_unexpected_or_missing_reverse_nonce(self) -> None:
        _raw, initial = self._initial()
        _receipt_raw, fi_receipt = self._seal_and_verify(initial, at=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(
            orchestration.PhysicalArvanS3FourRoleImmutabilityWitnessOrchestrationError,
            "NONCE_UNEXPECTED",
        ):
            orchestration.issue_physical_arvan_s3_four_role_immutability_next_witness_approval(
                binding=self.binding,
                prior_receipt=fi_receipt,
                issued_at=NOW + timedelta(seconds=2),
                reverse_probe_nonce_sha256="c" * 64,
                witness_signer=self.fixture.witness_signer,
            )

    def test_source_has_no_peer_transport_or_collector_callback_surface(self) -> None:
        import ast

        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse({"socket", "subprocess", "requests", "urllib", "boto3"} & imported)
        self.assertNotIn("readback_adapter", MODULE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
