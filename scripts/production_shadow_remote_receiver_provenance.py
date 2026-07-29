"""Pure redacted provenance adapter for signed remote receiver attestations.

The adapter has no transport, filesystem, key-management, or manifest access.
It accepts canonical public policy and receipt bytes from a future reviewed
integration, compares them to explicit expected bindings, invokes an injected
Ed25519 verifier only after those checks, and emits a redacted structural
record for a later source-set consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Any, Callable, Mapping

from scripts import production_shadow_remote_receiver_attestation as ATTESTATION
from scripts import production_shadow_remote_receiver_signing_policy as POLICY


PROVENANCE_SCHEMA = "production-shadow-convergence-remote-receiver-attestation-v1"
TRANSPORT = "object-storage-private-versioned-age"
PROVENANCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "worker_request_sha256",
        "worker_attestation_sha256",
        "worker_attestation_file_sha256",
        "transport",
        "object_storage",
        "observed_at",
        "presigned_url_persisted",
        "presigned_url_logged",
        "contains_secret_material",
        "direct_fi_to_ir_transfer",
        "receiver_attestation_sha256",
    }
)


class RemoteReceiverProvenanceError(ValueError):
    """Signed remote receiver provenance cannot be proven."""


@dataclass(frozen=True)
class ExpectedRemoteReceiverProvenance:
    campaign_id: str
    operation_id: str
    release_sha: str
    release_tree_sha: str
    role: str
    manifest_sha256: str
    plan_sha256: str
    approval_sha256: str
    phase: str
    operation: str
    expected_host: str
    phase_started_at: str
    request_sha256: str
    worker_attestation_sha256: str
    worker_attestation_file_sha256: str


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return POLICY.canonical_json_bytes(value)


def _digest(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _canonical_json(
            {key: value for key, value in document.items() if key != "receiver_attestation_sha256"}
        )
    ).hexdigest()


def _expected_mapping(expected: ExpectedRemoteReceiverProvenance) -> dict[str, str]:
    if not isinstance(expected, ExpectedRemoteReceiverProvenance):
        raise RemoteReceiverProvenanceError("expected remote receiver provenance is invalid")
    return {
        "campaign_id": expected.campaign_id,
        "operation_id": expected.operation_id,
        "release_sha": expected.release_sha,
        "release_tree_sha": expected.release_tree_sha,
        "role": expected.role,
        "manifest_sha256": expected.manifest_sha256,
        "plan_sha256": expected.plan_sha256,
        "approval_sha256": expected.approval_sha256,
        "phase": expected.phase,
        "operation": expected.operation,
        "expected_host": expected.expected_host,
        "phase_started_at": expected.phase_started_at,
        "request_sha256": expected.request_sha256,
        "worker_attestation_sha256": expected.worker_attestation_sha256,
        "worker_attestation_file_sha256": expected.worker_attestation_file_sha256,
    }


def _assert_expected(
    receipt: Mapping[str, Any],
    *,
    policy: POLICY.SigningPolicy,
    expected: ExpectedRemoteReceiverProvenance,
) -> None:
    values = _expected_mapping(expected)
    policy_values = {
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
    }
    if any(values[key] != item for key, item in policy_values.items()):
        raise RemoteReceiverProvenanceError("expected provenance differs from public signing policy")
    if any(receipt.get(key) != item for key, item in values.items()):
        raise RemoteReceiverProvenanceError("signed receiver receipt differs from expected provenance")


def _redacted_record(receipt: Mapping[str, Any]) -> dict[str, Any]:
    document = {
        "schema": PROVENANCE_SCHEMA,
        "status": "received",
        "campaign_id": receipt["campaign_id"],
        "operation_id": receipt["operation_id"],
        "release_sha": receipt["release_sha"],
        "release_tree_sha": receipt["release_tree_sha"],
        "manifest_sha256": receipt["manifest_sha256"],
        "plan_sha256": receipt["plan_sha256"],
        "approval_sha256": receipt["approval_sha256"],
        "phase": receipt["phase"],
        "operation": receipt["operation"],
        "role": receipt["role"],
        "expected_host": receipt["expected_host"],
        "phase_started_at": receipt["phase_started_at"],
        "worker_request_sha256": receipt["request_sha256"],
        "worker_attestation_sha256": receipt["worker_attestation_sha256"],
        "worker_attestation_file_sha256": receipt["worker_attestation_file_sha256"],
        "transport": TRANSPORT,
        "object_storage": dict(receipt["object_storage"]),
        "observed_at": receipt["observed_at"],
        "presigned_url_persisted": False,
        "presigned_url_logged": False,
        "contains_secret_material": False,
        "direct_fi_to_ir_transfer": False,
        "receiver_attestation_sha256": "0" * 64,
    }
    document["receiver_attestation_sha256"] = _digest(document)
    if set(document) != PROVENANCE_FIELDS:
        raise RemoteReceiverProvenanceError("redacted receiver provenance fields are not exact")
    return document


def verify_remote_receiver_provenance(
    *,
    policy_payload: bytes,
    attestation_payload: bytes,
    expected: ExpectedRemoteReceiverProvenance,
    now: datetime,
    verify_ed25519: Callable[[bytes, bytes, bytes], object],
) -> dict[str, Any]:
    """Verify one receipt and return only its redacted source-set-shaped record."""

    if not callable(verify_ed25519):
        raise RemoteReceiverProvenanceError("remote receiver signature verifier is unavailable")
    try:
        policy = POLICY.parse_policy_payload(policy_payload)
        parsed = ATTESTATION.parse_attestation_payload(attestation_payload, policy=policy)
        _assert_expected(parsed.document, policy=policy, expected=expected)
        verified = ATTESTATION.verify_attestation_payload(
            attestation_payload,
            policy=policy,
            expected_request_sha256=expected.request_sha256,
            now=now,
            verify_ed25519=verify_ed25519,
        )
    except (POLICY.RemoteReceiverSigningPolicyError, ATTESTATION.RemoteReceiverAttestationError) as exc:
        raise RemoteReceiverProvenanceError("remote receiver provenance verification failed") from exc
    return _redacted_record(verified.document)
