"""Small cryptographic fixture for one fresh durable four-role admission.

This is test-only scaffolding.  It exercises the same signed aggregate,
root-owned Witness-ledger admission, and opaque-admission verifier used by
production code; it never opens credentials or contacts Object Storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_arvan_s3_four_role_live_iam_evidence as evidence
from core import physical_arvan_s3_four_role_live_iam_durable_admission_bridge as admission_bridge
from core import physical_arvan_s3_four_role_live_iam_witness_ledger_runtime as ledger_runtime
from core import physical_ir_to_fi_object_storage_failback_preflight as preflight


@dataclass(frozen=True)
class FourRoleLiveIamDurableAdmissionFixture:
    live_iam_binding: evidence.PhysicalArvanS3FourRoleLiveIamEvidenceBinding
    live_iam_durable_admission: (
        admission_bridge.VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission
    )
    # Test-only signing material for downstream grammar tests.  It is created
    # in a temporary fixture and never represents a configured host key.
    witness_signer: Ed25519PrivateKey
    role_signers: dict[str, Ed25519PrivateKey]


def _public_key(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _outcomes(role: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    return (
        [{"operation": item, "outcome": "allowed"} for item in evidence._ROLE_ALLOWED[role]],
        [{"operation": item, "outcome": "denied"} for item in evidence._ROLE_DENIED[role]],
    )


def make_four_role_live_iam_durable_admission_fixture(
    *,
    binding: preflight.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> FourRoleLiveIamDurableAdmissionFixture:
    """Build an opaque durable admission bound to one supplied public route."""

    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise ValueError("test fixture requires an aware time")
    now = observed_at.astimezone(timezone.utc)
    witness = Ed25519PrivateKey.generate()
    signers = {
        "fi-publisher": Ed25519PrivateKey.generate(),
        "ir-receiver": Ed25519PrivateKey.generate(),
        "ir-publisher": Ed25519PrivateKey.generate(),
        "fi-receiver": Ed25519PrivateKey.generate(),
    }
    live_binding = evidence.build_physical_arvan_s3_four_role_live_iam_evidence_binding(
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        normal_route_scope_sha256=binding.normal_route_scope_sha256,
        reverse_route_scope_sha256=binding.reverse_route_scope_sha256,
        four_role_binding_sha256=binding.route_binding_sha256,
        fi_publisher_identity_sha256=binding.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=binding.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=binding.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=binding.fi_receiver_identity_sha256,
        fi_publisher_signer_public_key=_public_key(signers["fi-publisher"]),
        ir_receiver_signer_public_key=_public_key(signers["ir-receiver"]),
        ir_publisher_signer_public_key=_public_key(signers["ir-publisher"]),
        fi_receiver_signer_public_key=_public_key(signers["fi-receiver"]),
    )
    issued_at = now - timedelta(seconds=30)
    expires_at = now + timedelta(seconds=120)

    def direction(
        publisher_role: str,
        *,
        offset: int,
    ) -> tuple[
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamPublisherObservation,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessForward,
        evidence.VerifiedPhysicalArvanS3FourRoleLiveIamReceiverObservation,
    ]:
        receiver_role = evidence._RECEIVER_BY_DIRECTION[
            evidence._DIRECTION_BY_PUBLISHER[publisher_role]
        ]
        publisher_allowed, publisher_denied = _outcomes(publisher_role)
        receiver_allowed, receiver_denied = _outcomes(receiver_role)
        started = issued_at + timedelta(seconds=offset)
        locator = evidence.make_physical_arvan_s3_live_iam_probe_locator(
            binding=live_binding,
            nonce=permit.nonce,
            publisher_role=publisher_role,
            object_version_id=f"version-{publisher_role}-{offset}",
            content_sha256=("a" if publisher_role == "fi-publisher" else "b") * 64,
            content_bytes=80 + offset,
        )
        publisher_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_publisher_observation(
            binding=live_binding,
            nonce_permit=permit,
            publisher_role=publisher_role,
            observed_at=started,
            probe_locator=locator,
            allowed_operation_outcomes=publisher_allowed,
            denied_operation_outcomes=publisher_denied,
            role_signer=signers[publisher_role],
        )
        publisher = evidence.verify_physical_arvan_s3_four_role_live_iam_publisher_observation(
            publisher_raw,
            binding=live_binding,
            nonce_permit=permit,
            observed_at=started,
        )
        forwarded_at = started + timedelta(seconds=1)
        forward_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_witness_forward(
            binding=live_binding,
            nonce_permit=permit,
            publisher_observation=publisher,
            forwarded_at=forwarded_at,
            witness_signer=witness,
        )
        forward = evidence.verify_physical_arvan_s3_four_role_live_iam_witness_forward(
            forward_raw,
            binding=live_binding,
            nonce_permit=permit,
            witness_public_key=_public_key(witness),
            observed_at=forwarded_at,
        )
        received_at = forwarded_at + timedelta(seconds=1)
        receiver_raw = evidence.seal_physical_arvan_s3_four_role_live_iam_receiver_observation(
            binding=live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
            allowed_operation_outcomes=receiver_allowed,
            denied_operation_outcomes=receiver_denied,
            role_signer=signers[receiver_role],
        )
        receiver = evidence.verify_physical_arvan_s3_four_role_live_iam_receiver_observation(
            receiver_raw,
            binding=live_binding,
            nonce_permit=permit,
            witness_forward=forward,
            observed_at=received_at,
        )
        return publisher, forward, receiver

    with tempfile.TemporaryDirectory(prefix="four-role-live-iam-fixture-") as temporary:
        state_root = Path(temporary)
        os.chmod(state_root, 0o700)
        runtime = ledger_runtime.open_physical_arvan_s3_four_role_live_iam_witness_ledger_runtime(
            ledger_runtime.PhysicalArvanS3FourRoleLiveIamWitnessLedgerRuntimeConfig(
                state_root=state_root,
                evidence_binding=live_binding,
                enabled=True,
            )
        )
        _state, permit_raw = (
            ledger_runtime.issue_physical_arvan_s3_four_role_live_iam_witness_ledger_nonce_permit(
                runtime=runtime,
                nonce="1" * 64,
                issued_at=issued_at,
                expires_at=expires_at,
                witness_signer=witness,
            )
        )
        permit = evidence.verify_physical_arvan_s3_four_role_live_iam_nonce_permit(
            permit_raw,
            binding=live_binding,
            witness_public_key=_public_key(witness),
            observed_at=now,
        )
        normal_publisher, normal_forward, normal_receiver = direction("fi-publisher", offset=1)
        reverse_publisher, reverse_forward, reverse_receiver = direction("ir-publisher", offset=10)
        _state, aggregate_raw = (
            ledger_runtime.seal_physical_arvan_s3_four_role_live_iam_witness_ledger_aggregate(
                runtime=runtime,
                nonce_permit=permit,
                normal_publisher_observation=normal_publisher,
                normal_witness_forward=normal_forward,
                normal_receiver_observation=normal_receiver,
                reverse_publisher_observation=reverse_publisher,
                reverse_witness_forward=reverse_forward,
                reverse_receiver_observation=reverse_receiver,
                committed_at=issued_at + timedelta(seconds=20),
                witness_signer=witness,
            )
        )
        durable_admission = (
            admission_bridge.admit_physical_arvan_s3_four_role_live_iam_durable_aggregate(
                runtime=runtime,
                aggregate=aggregate_raw,
                witness_public_key=_public_key(witness),
                live_iam_binding=live_binding,
                failback_binding=binding,
                observed_at=now,
            )
        )
    return FourRoleLiveIamDurableAdmissionFixture(
        live_iam_binding=live_binding,
        live_iam_durable_admission=durable_admission,
        witness_signer=witness,
        role_signers=dict(signers),
    )
