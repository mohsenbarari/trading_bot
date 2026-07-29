"""Pure signed-attestation envelopes for remote convergence receivers.

This module composes the public signing-policy contract with the minimal
Ed25519 verifier adapter.  It never reads or generates keys, opens files,
contacts a host, or transfers an Object Storage artifact.  A future exact
receiver may inject its narrowly held signing callback; a future controller
may use the verifier after independently obtaining canonical receipt bytes.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any, Callable, Mapping

from scripts import production_shadow_ed25519_verifier as ED25519
from scripts import production_shadow_remote_receiver_signing_policy as POLICY


class RemoteReceiverAttestationError(ValueError):
    """A remote receiver attestation cannot be structurally proven."""


@dataclass(frozen=True)
class ReceiverAttestation:
    document: Mapping[str, Any]
    payload: bytes
    signature_payload: bytes


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_signer(value: object) -> Callable[[bytes], object]:
    if not callable(value):
        raise RemoteReceiverAttestationError("remote receiver signer is unavailable")
    return value


def _signature_bytes(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != 64:
        raise RemoteReceiverAttestationError("remote receiver signature is invalid")
    return value


def _base_document(
    *,
    policy: POLICY.SigningPolicy,
    manifest_sha256: str,
    plan_sha256: str,
    approval_sha256: str,
    phase: str,
    operation: str,
    expected_host: str,
    phase_started_at: str,
    request_sha256: str,
    worker_attestation_sha256: str,
    worker_attestation_file_sha256: str,
    object_storage: Mapping[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    if not isinstance(policy, POLICY.SigningPolicy):
        raise RemoteReceiverAttestationError("remote receiver signing policy is invalid")
    if not isinstance(object_storage, Mapping):
        raise RemoteReceiverAttestationError("remote receiver Object Storage binding is invalid")
    return {
        "schema": POLICY.RECEIPT_SCHEMA,
        "algorithm": POLICY.ALGORITHM,
        "key_id": policy.key_id,
        "policy_sha256": policy.policy_sha256,
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
        "manifest_sha256": manifest_sha256,
        "plan_sha256": plan_sha256,
        "approval_sha256": approval_sha256,
        "phase": phase,
        "operation": operation,
        "expected_host": expected_host,
        "phase_started_at": phase_started_at,
        "request_sha256": request_sha256,
        "worker_attestation_sha256": worker_attestation_sha256,
        "worker_attestation_file_sha256": worker_attestation_file_sha256,
        "object_storage": dict(object_storage),
        "observed_at": observed_at,
        "signed_payload_sha256": "0" * 64,
        "signature_base64": base64.b64encode(b"\0" * 64).decode("ascii"),
        "signature_sha256": hashlib.sha256(b"\0" * 64).hexdigest(),
        "receipt_sha256": "0" * 64,
    }


def build_attestation(
    *,
    policy: POLICY.SigningPolicy,
    manifest_sha256: str,
    plan_sha256: str,
    approval_sha256: str,
    phase: str,
    operation: str,
    expected_host: str,
    phase_started_at: str,
    request_sha256: str,
    worker_attestation_sha256: str,
    worker_attestation_file_sha256: str,
    object_storage: Mapping[str, Any],
    observed_at: str,
    sign_ed25519: Callable[[bytes], object],
) -> ReceiverAttestation:
    """Build one canonical receipt from an injected, role-local signer."""

    signer = _require_signer(sign_ed25519)
    # Validate every envelope field before a signer receives any payload.  The
    # temporary signature is structurally valid but has no trust meaning.
    document = _base_document(
        policy=policy,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        phase=phase,
        operation=operation,
        expected_host=expected_host,
        phase_started_at=phase_started_at,
        request_sha256=request_sha256,
        worker_attestation_sha256=worker_attestation_sha256,
        worker_attestation_file_sha256=worker_attestation_file_sha256,
        object_storage=object_storage,
        observed_at=observed_at,
    )
    signature_payload = POLICY.receipt_signing_payload(document)
    document["signed_payload_sha256"] = _sha256(signature_payload)
    document["receipt_sha256"] = _sha256(POLICY.receipt_payload(document))
    try:
        POLICY.validate_receipt_document(document, policy=policy)
    except POLICY.RemoteReceiverSigningPolicyError as exc:
        raise RemoteReceiverAttestationError("remote receiver attestation fields are invalid") from exc
    try:
        signature = _signature_bytes(signer(signature_payload))
    except RemoteReceiverAttestationError:
        raise
    except Exception as exc:
        raise RemoteReceiverAttestationError("remote receiver signer failed") from exc
    document["signature_base64"] = base64.b64encode(signature).decode("ascii")
    document["signature_sha256"] = _sha256(signature)
    document["receipt_sha256"] = _sha256(POLICY.receipt_payload(document))
    try:
        validated = POLICY.validate_receipt_document(document, policy=policy)
    except POLICY.RemoteReceiverSigningPolicyError as exc:
        raise RemoteReceiverAttestationError("remote receiver attestation binding differs") from exc
    payload = POLICY.canonical_json_bytes(validated.document) + b"\n"
    return ReceiverAttestation(
        document=validated.document,
        payload=payload,
        signature_payload=validated.signature_payload,
    )


def parse_attestation_payload(
    payload: bytes,
    *,
    policy: POLICY.SigningPolicy,
) -> ReceiverAttestation:
    """Parse and structurally bind one canonical receipt without trusting it."""

    try:
        validated = POLICY.parse_receipt_payload(payload, policy=policy)
    except POLICY.RemoteReceiverSigningPolicyError as exc:
        raise RemoteReceiverAttestationError("remote receiver attestation is invalid") from exc
    return ReceiverAttestation(
        document=validated.document,
        payload=payload,
        signature_payload=validated.signature_payload,
    )


def verify_attestation_payload(
    payload: bytes,
    *,
    policy: POLICY.SigningPolicy,
    expected_request_sha256: str,
    now: datetime,
    verify_ed25519: Callable[[bytes, bytes, bytes], object] = ED25519.verify_ed25519,
) -> ReceiverAttestation:
    """Validate and verify one canonical remote receiver attestation."""

    try:
        validated = POLICY.verify_receipt_payload(
            payload,
            policy=policy,
            expected_request_sha256=expected_request_sha256,
            verify_ed25519=verify_ed25519,
            now=now,
        )
    except (POLICY.RemoteReceiverSigningPolicyError, ED25519.Ed25519VerificationError) as exc:
        raise RemoteReceiverAttestationError("remote receiver attestation verification failed") from exc
    return ReceiverAttestation(
        document=validated.document,
        payload=payload,
        signature_payload=validated.signature_payload,
    )
