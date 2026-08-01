"""Focused adversarial tests for the live-IAM aggregate/preflight bridge."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pickle
from pathlib import Path
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_live_iam_evidence as evidence
from core import physical_arvan_s3_four_role_live_iam_preflight_gate as gate
from core import physical_ir_to_fi_object_storage_failback_preflight as failback


CAMPAIGN = "four-role-gate-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
NONCE = "1" * 64
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_four_role_live_iam_preflight_gate.py"
)


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": operation, "outcome": "allowed"} for operation in evidence._ROLE_ALLOWED[role]],
        [{"operation": operation, "outcome": "denied"} for operation in evidence._ROLE_DENIED[role]],
    )


class PhysicalArvanS3FourRoleLiveIamPreflightGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.witness = Ed25519PrivateKey.generate()
        self.signers = {
            "fi-publisher": Ed25519PrivateKey.generate(),
            "ir-receiver": Ed25519PrivateKey.generate(),
            "ir-publisher": Ed25519PrivateKey.generate(),
            "fi-receiver": Ed25519PrivateKey.generate(),
        }
        self.live_binding = evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            normal_route_scope_sha256="2" * 64,
            reverse_route_scope_sha256="3" * 64,
            four_role_binding_sha256="4" * 64,
            fi_publisher_identity_sha256="5" * 64,
            ir_receiver_identity_sha256="6" * 64,
            ir_publisher_identity_sha256="7" * 64,
            fi_receiver_identity_sha256="8" * 64,
            fi_publisher_signer_public_key=_public_key(self.signers["fi-publisher"]),
            ir_receiver_signer_public_key=_public_key(self.signers["ir-receiver"]),
            ir_publisher_signer_public_key=_public_key(self.signers["ir-publisher"]),
            fi_receiver_signer_public_key=_public_key(self.signers["fi-receiver"]),
        )
        self.failback_binding = failback.PhysicalIrToFiObjectStorageFailbackBinding(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            route_binding_sha256=self.live_binding.four_role_binding_sha256,
            normal_route_scope_sha256=self.live_binding.normal_route_scope_sha256,
            reverse_route_scope_sha256=self.live_binding.reverse_route_scope_sha256,
            fi_publisher_identity_sha256=self.live_binding.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=self.live_binding.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=self.live_binding.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=self.live_binding.fi_receiver_identity_sha256,
        )

    def _permit(self) -> tuple[
        evidence.PhysicalArvanS3FourRoleLiveIamNonceLedger,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
    ]:
        ledger = evidence.make_physical_arvan_s3_four_role_live_iam_nonce_ledger(
            binding=self.live_binding
        )
        ledger, raw = evidence.issue_physical_arvan_s3_four_role_live_iam_nonce_permit(
            binding=self.live_binding,
            ledger=ledger,
            nonce=NONCE,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            witness_signer=self.witness,
        )
        return ledger, evidence.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            raw,
            binding=self.live_binding,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW,
        )

    def _direction(
        self,
        *,
        publisher_role: str,
        permit: evidence.VerifiedPhysicalArvanS3FourRoleLiveIamNoncePermit,
        offset: int,
    ) -> tuple[
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    ]:
        receiver_role = evidence._RECEIVER_BY_DIRECTION[evidence._DIRECTION_BY_PUBLISHER[publisher_role]]
        pub_allowed, pub_denied = _outcomes(publisher_role)
        receiver_allowed, receiver_denied = _outcomes(receiver_role)
        started = NOW + timedelta(seconds=offset)
        locator = evidence.make_physical_arvan_s3_live_iam_probe_locator(
            binding=self.live_binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=80 + offset,
        )
        publisher_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=started,
            probe_locator=locator,
            allowed_operation_outcomes=pub_allowed,
            denied_operation_outcomes=pub_denied,
            role_signer=self.signers[publisher_role],
        )
        publisher = evidence.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            observed_at=started,
        )
        forwarded_at = started + timedelta(seconds=1)
        forward_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=self.live_binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forwarded_at,
            witness_signer=self.witness,
        )
        forward = evidence.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_public_key=_public_key(self.witness),
            observed_at=forwarded_at,
        )
        received_at = forwarded_at + timedelta(seconds=1)
        receiver_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=self.signers[receiver_role],
        )
        receiver = evidence.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=self.live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
        )
        return publisher, forward, receiver

    def _verified_aggregate(self) -> evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate:
        ledger, permit = self._permit()
        normal_publisher, normal_forward, normal_receiver = self._direction(
            publisher_role="fi-publisher", permit=permit, offset=1
        )
        reverse_publisher, reverse_forward, reverse_receiver = self._direction(
            publisher_role="ir-publisher", permit=permit, offset=10
        )
        ledger, raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            binding=self.live_binding,
            ledger=ledger,
            nonce_permit=permit,
            normal_publisher_observation=normal_publisher,
            normal_witness_forward=normal_forward,
            normal_receiver_observation=normal_receiver,
            reverse_publisher_observation=reverse_publisher,
            reverse_witness_forward=reverse_forward,
            reverse_receiver_observation=reverse_receiver,
            committed_at=NOW + timedelta(seconds=20),
            witness_signer=self.witness,
        )
        return evidence.verify_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            raw,
            binding=self.live_binding,
            ledger=ledger,
            witness_public_key=_public_key(self.witness),
            observed_at=NOW + timedelta(seconds=21),
        )

    def test_good_gate_is_opaque_revalidatable_and_nonserializable(self) -> None:
        aggregate = self._verified_aggregate()
        minted = gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
            aggregate=aggregate,
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW + timedelta(seconds=21),
        )
        self.assertIs(
            minted,
            gate.require_verified_physical_arvan_s3_four_role_live_iam_preflight_gate(
                minted,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(seconds=22),
            ),
        )
        self.assertEqual(aggregate.raw_sha256, minted.aggregate_sha256)
        self.assertEqual(aggregate.nonce, minted.witness_nonce)
        self.assertEqual(self.live_binding.four_role_binding_sha256, minted.four_role_route_binding_sha256)
        with self.assertRaises(TypeError):
            pickle.dumps(minted)

    def test_stale_and_fake_aggregate_fail_before_gate_mint(self) -> None:
        aggregate = self._verified_aggregate()
        with self.assertRaisesRegex(gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError, "AGGREGATE_.*STALE"):
            gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
                aggregate=aggregate,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(minutes=5),
            )
        fake = evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate(
            nonce=aggregate.nonce,
            issued_at=aggregate.issued_at,
            expires_at=aggregate.expires_at,
            committed_at=aggregate.committed_at,
            evidence_binding_sha256=aggregate.evidence_binding_sha256,
            nonce_commitment_sha256=aggregate.nonce_commitment_sha256,
            raw_sha256=aggregate.raw_sha256,
        )
        with self.assertRaisesRegex(gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError, "AGGREGATE_.*NOT_VERIFIED"):
            gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
                aggregate=fake,
                live_iam_binding=self.live_binding,
                failback_binding=self.failback_binding,
                observed_at=NOW + timedelta(seconds=21),
            )

    def test_swapped_identity_and_scope_or_route_mismatch_fail_closed(self) -> None:
        aggregate = self._verified_aggregate()
        swapped_identity = replace(
            self.failback_binding,
            fi_publisher_identity_sha256=self.live_binding.ir_receiver_identity_sha256,
            ir_receiver_identity_sha256=self.live_binding.fi_publisher_identity_sha256,
        )
        with self.assertRaisesRegex(gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError, "IDENTITY_MISMATCH"):
            gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
                aggregate=aggregate,
                live_iam_binding=self.live_binding,
                failback_binding=swapped_identity,
                observed_at=NOW + timedelta(seconds=21),
            )
        for changed in (
            replace(self.failback_binding, normal_route_scope_sha256="9" * 64),
            replace(self.failback_binding, route_binding_sha256="a" * 64),
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError, "ROUTE_BINDING_MISMATCH"):
                    gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
                        aggregate=aggregate,
                        live_iam_binding=self.live_binding,
                        failback_binding=changed,
                        observed_at=NOW + timedelta(seconds=21),
                    )

    def test_directly_constructed_or_mutated_gate_fails_revalidation(self) -> None:
        aggregate = self._verified_aggregate()
        minted = gate.mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
            aggregate=aggregate,
            live_iam_binding=self.live_binding,
            failback_binding=self.failback_binding,
            observed_at=NOW + timedelta(seconds=21),
        )
        fake = gate.VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate(
            schema=minted.schema,
            campaign_id=minted.campaign_id,
            release_sha=minted.release_sha,
            normal_route_scope_sha256=minted.normal_route_scope_sha256,
            reverse_route_scope_sha256=minted.reverse_route_scope_sha256,
            four_role_route_binding_sha256=minted.four_role_route_binding_sha256,
            fi_publisher_identity_sha256=minted.fi_publisher_identity_sha256,
            ir_receiver_identity_sha256=minted.ir_receiver_identity_sha256,
            ir_publisher_identity_sha256=minted.ir_publisher_identity_sha256,
            fi_receiver_identity_sha256=minted.fi_receiver_identity_sha256,
            evidence_binding_sha256=minted.evidence_binding_sha256,
            witness_nonce=minted.witness_nonce,
            witness_nonce_commitment_sha256=minted.witness_nonce_commitment_sha256,
            aggregate_sha256=minted.aggregate_sha256,
            expires_at=minted.expires_at,
        )
        for candidate in (fake, replace(minted, aggregate_sha256="f" * 64)):
            with self.subTest(candidate=candidate):
                with self.assertRaises(gate.PhysicalArvanS3FourRoleLiveIamPreflightGateError):
                    gate.require_verified_physical_arvan_s3_four_role_live_iam_preflight_gate(
                        candidate,
                        live_iam_binding=self.live_binding,
                        failback_binding=self.failback_binding,
                        observed_at=NOW + timedelta(seconds=22),
                    )

    def test_source_has_no_io_sdk_or_paired_client_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(
            imports
            & {
                "boto3",
                "botocore",
                "socket",
                "subprocess",
                "requests",
                "os",
                "pathlib",
                "urllib",
            }
        )
        self.assertNotIn("provider_preflight_evidence_sha256", source)
        self.assertNotIn("paired_client", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
