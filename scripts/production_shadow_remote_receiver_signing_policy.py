"""Pure signing-policy contracts for remote convergence receiver provenance.

This module is deliberately stdlib-only and performs no key generation,
credential access, filesystem access, or network I/O.
It defines the canonical public Ed25519 policy and signed-receipt envelopes
that a later, release-bound controller and receiver may use.  The only
signature operation is an injected verifier callback; this module never loads
an Ed25519 implementation or a private key.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping
from uuid import UUID


POLICY_SCHEMA = "production-shadow-remote-receiver-signing-policy-v1"
RECEIPT_SCHEMA = "production-shadow-remote-receiver-signed-receipt-v1"
ALGORITHM = "ed25519"
ROLES = frozenset({"webapp_ir", "witness"})
MAX_POLICY_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
OPERATION_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$")

POLICY_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "key_id",
        "public_key_base64",
        "public_key_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "not_before",
        "expires_at",
        "receiver_sha256",
        "worker_sha256",
        "policy_sha256",
    }
)
OBJECT_STORAGE_FIELDS = frozenset(
    {
        "provider",
        "bucket",
        "artifact_kind",
        "object_key",
        "version_id",
        "readback_version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "age_recipient_sha256",
        "private",
        "versioned",
    }
)
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "key_id",
        "policy_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "expected_host",
        "phase_started_at",
        "request_sha256",
        "worker_attestation_sha256",
        "worker_attestation_file_sha256",
        "object_storage",
        "observed_at",
        "signed_payload_sha256",
        "signature_base64",
        "signature_sha256",
        "receipt_sha256",
    }
)


class RemoteReceiverSigningPolicyError(ValueError):
    """The public signing policy or receipt has no exact canonical binding."""


@dataclass(frozen=True)
class SigningPolicy:
    key_id: str
    public_key: bytes
    campaign_id: str
    operation_id: str
    release_sha: str
    release_tree_sha: str
    role: str
    not_before: datetime
    expires_at: datetime
    receiver_sha256: str
    worker_sha256: str
    policy_sha256: str

    def document(self) -> dict[str, str]:
        document = {
            "schema": POLICY_SCHEMA,
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "public_key_base64": base64.b64encode(self.public_key).decode("ascii"),
            "public_key_sha256": hashlib.sha256(self.public_key).hexdigest(),
            "campaign_id": self.campaign_id,
            "operation_id": self.operation_id,
            "release_sha": self.release_sha,
            "release_tree_sha": self.release_tree_sha,
            "role": self.role,
            "not_before": _timestamp_text(self.not_before),
            "expires_at": _timestamp_text(self.expires_at),
            "receiver_sha256": self.receiver_sha256,
            "worker_sha256": self.worker_sha256,
            "policy_sha256": self.policy_sha256,
        }
        return document


@dataclass(frozen=True)
class SignedReceipt:
    document: Mapping[str, Any]
    signature_payload: bytes
    signature: bytes


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise RemoteReceiverSigningPolicyError("JSON object has duplicate fields")
        document[key] = value
    return document


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RemoteReceiverSigningPolicyError("value is not canonical JSON") from exc


def _sha256(value: bytes | Mapping[str, Any]) -> str:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        raise RemoteReceiverSigningPolicyError(f"{label} must be a nonzero SHA-256")
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise RemoteReceiverSigningPolicyError(f"{label} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise RemoteReceiverSigningPolicyError(f"{label} must be a canonical UUID") from exc
    if value != str(parsed) or parsed.int == 0:
        raise RemoteReceiverSigningPolicyError(f"{label} must be a nonzero canonical UUID")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RemoteReceiverSigningPolicyError(f"{label} must be a UTC RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RemoteReceiverSigningPolicyError(f"{label} must be a UTC RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RemoteReceiverSigningPolicyError(f"{label} must be a UTC RFC3339 timestamp")
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat().replace("+00:00", "Z") != value:
        raise RemoteReceiverSigningPolicyError(f"{label} must be a canonical UTC RFC3339 timestamp")
    return normalized


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RemoteReceiverSigningPolicyError("timestamp is invalid")
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _verification_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RemoteReceiverSigningPolicyError("verification time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _base64(value: Any, *, expected_bytes: int, label: str) -> bytes:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise RemoteReceiverSigningPolicyError(f"{label} is invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise RemoteReceiverSigningPolicyError(f"{label} is invalid") from exc
    if len(raw) != expected_bytes or base64.b64encode(raw).decode("ascii") != value:
        raise RemoteReceiverSigningPolicyError(f"{label} is invalid")
    return raw


def policy_payload(document: Mapping[str, Any]) -> bytes:
    if not isinstance(document, Mapping):
        raise RemoteReceiverSigningPolicyError("signing policy is invalid")
    return canonical_json_bytes({key: value for key, value in document.items() if key != "policy_sha256"})


def parse_policy_payload(payload: bytes) -> SigningPolicy:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_POLICY_BYTES or not payload.endswith(b"\n"):
        raise RemoteReceiverSigningPolicyError("signing policy payload is invalid")
    try:
        document = json.loads(payload[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteReceiverSigningPolicyError("signing policy payload is invalid") from exc
    if not isinstance(document, dict) or canonical_json_bytes(document) + b"\n" != payload:
        raise RemoteReceiverSigningPolicyError("signing policy payload is not canonical")
    return validate_policy_document(document)


def validate_policy_document(value: Mapping[str, Any]) -> SigningPolicy:
    if not isinstance(value, Mapping) or set(value) != POLICY_FIELDS:
        raise RemoteReceiverSigningPolicyError("signing policy fields are not exact")
    document = dict(value)
    if document.get("schema") != POLICY_SCHEMA or document.get("algorithm") != ALGORITHM:
        raise RemoteReceiverSigningPolicyError("signing policy schema or algorithm differs")
    key_id = document.get("key_id")
    if not isinstance(key_id, str) or IDENTIFIER_RE.fullmatch(key_id) is None:
        raise RemoteReceiverSigningPolicyError("signing policy key id is invalid")
    public_key = _base64(document.get("public_key_base64"), expected_bytes=32, label="signing policy public key")
    if document.get("public_key_sha256") != hashlib.sha256(public_key).hexdigest():
        raise RemoteReceiverSigningPolicyError("signing policy public key digest differs")
    campaign_id = _canonical_uuid(document.get("campaign_id"), label="campaign id")
    operation_id = _canonical_uuid(document.get("operation_id"), label="operation id")
    if campaign_id == operation_id:
        raise RemoteReceiverSigningPolicyError("campaign and operation ids must differ")
    release_sha = document.get("release_sha")
    release_tree_sha = document.get("release_tree_sha")
    role = document.get("role")
    if (
        not isinstance(release_sha, str)
        or SHA40_RE.fullmatch(release_sha) is None
        or not isinstance(release_tree_sha, str)
        or SHA40_RE.fullmatch(release_tree_sha) is None
        or role not in ROLES
    ):
        raise RemoteReceiverSigningPolicyError("signing policy release or role differs")
    not_before = _timestamp(document.get("not_before"), label="signing policy not-before")
    expires_at = _timestamp(document.get("expires_at"), label="signing policy expiry")
    if expires_at <= not_before:
        raise RemoteReceiverSigningPolicyError("signing policy expiry must follow not-before")
    receiver_sha256 = _nonzero_sha256(document.get("receiver_sha256"), label="receiver source")
    worker_sha256 = _nonzero_sha256(document.get("worker_sha256"), label="worker source")
    if receiver_sha256 == worker_sha256:
        raise RemoteReceiverSigningPolicyError("receiver and worker source digests must differ")
    if document.get("policy_sha256") != _sha256(policy_payload(document)):
        raise RemoteReceiverSigningPolicyError("signing policy digest differs")
    return SigningPolicy(
        key_id=key_id,
        public_key=public_key,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        role=role,
        not_before=not_before,
        expires_at=expires_at,
        receiver_sha256=receiver_sha256,
        worker_sha256=worker_sha256,
        policy_sha256=str(document["policy_sha256"]),
    )


def receipt_signing_payload(document: Mapping[str, Any]) -> bytes:
    if not isinstance(document, Mapping):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt is invalid")
    excluded = {"signed_payload_sha256", "signature_base64", "signature_sha256", "receipt_sha256"}
    return canonical_json_bytes({key: value for key, value in document.items() if key not in excluded})


def receipt_payload(document: Mapping[str, Any]) -> bytes:
    if not isinstance(document, Mapping):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt is invalid")
    return canonical_json_bytes({key: value for key, value in document.items() if key != "receipt_sha256"})


def parse_receipt_payload(payload: bytes, *, policy: SigningPolicy) -> SignedReceipt:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_RECEIPT_BYTES or not payload.endswith(b"\n"):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt payload is invalid")
    try:
        document = json.loads(payload[:-1].decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RemoteReceiverSigningPolicyError("signed receiver receipt payload is invalid") from exc
    if not isinstance(document, dict) or canonical_json_bytes(document) + b"\n" != payload:
        raise RemoteReceiverSigningPolicyError("signed receiver receipt payload is not canonical")
    return validate_receipt_document(document, policy=policy)


def _validate_object_storage(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != OBJECT_STORAGE_FIELDS:
        raise RemoteReceiverSigningPolicyError("signed receiver Object Storage fields are not exact")
    document = dict(value)
    for field in ("provider", "bucket", "artifact_kind", "object_key", "version_id"):
        item = document.get(field)
        if not isinstance(item, str) or not item or len(item) > 1024 or item != item.strip():
            raise RemoteReceiverSigningPolicyError("signed receiver Object Storage identity is invalid")
    if (
        document.get("readback_version_id") != document["version_id"]
        or document.get("private") is not True
        or document.get("versioned") is not True
        or isinstance(document.get("ciphertext_bytes"), bool)
        or not isinstance(document.get("ciphertext_bytes"), int)
        or not 1 <= document["ciphertext_bytes"] <= MAX_RECEIPT_BYTES
    ):
        raise RemoteReceiverSigningPolicyError("signed receiver Object Storage binding differs")
    _nonzero_sha256(document.get("ciphertext_sha256"), label="Object Storage ciphertext")
    _nonzero_sha256(document.get("age_recipient_sha256"), label="Object Storage recipient")
    return document


def validate_receipt_document(value: Mapping[str, Any], *, policy: SigningPolicy) -> SignedReceipt:
    if not isinstance(policy, SigningPolicy):
        raise RemoteReceiverSigningPolicyError("signing policy is invalid")
    if not isinstance(value, Mapping) or set(value) != RECEIPT_FIELDS:
        raise RemoteReceiverSigningPolicyError("signed receiver receipt fields are not exact")
    document = dict(value)
    expected = {
        "schema": RECEIPT_SCHEMA,
        "algorithm": ALGORITHM,
        "key_id": policy.key_id,
        "policy_sha256": policy.policy_sha256,
        "campaign_id": policy.campaign_id,
        "operation_id": policy.operation_id,
        "release_sha": policy.release_sha,
        "release_tree_sha": policy.release_tree_sha,
        "role": policy.role,
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt policy binding differs")
    for field in (
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "request_sha256",
        "worker_attestation_sha256",
        "worker_attestation_file_sha256",
    ):
        _nonzero_sha256(document.get(field), label=field)
    phase = document.get("phase")
    operation = document.get("operation")
    if not isinstance(phase, str) or IDENTIFIER_RE.fullmatch(phase) is None:
        raise RemoteReceiverSigningPolicyError("signed receiver phase is invalid")
    if not isinstance(operation, str) or OPERATION_RE.fullmatch(operation) is None:
        raise RemoteReceiverSigningPolicyError("signed receiver operation is invalid")
    expected_host = document.get("expected_host")
    if not isinstance(expected_host, str) or HOST_RE.fullmatch(expected_host) is None:
        raise RemoteReceiverSigningPolicyError("signed receiver expected host is invalid")
    phase_started_at = _timestamp(document.get("phase_started_at"), label="phase start")
    observed_at = _timestamp(document.get("observed_at"), label="observation time")
    if observed_at < phase_started_at:
        raise RemoteReceiverSigningPolicyError("signed receiver observation predates phase start")
    _validate_object_storage(document.get("object_storage"))
    signing_payload = receipt_signing_payload(document)
    if document.get("signed_payload_sha256") != _sha256(signing_payload):
        raise RemoteReceiverSigningPolicyError("signed receiver payload digest differs")
    signature = _base64(document.get("signature_base64"), expected_bytes=64, label="signed receiver signature")
    if document.get("signature_sha256") != hashlib.sha256(signature).hexdigest():
        raise RemoteReceiverSigningPolicyError("signed receiver signature digest differs")
    if document.get("receipt_sha256") != _sha256(receipt_payload(document)):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt digest differs")
    return SignedReceipt(document=document, signature_payload=signing_payload, signature=signature)


def verify_receipt_payload(
    payload: bytes,
    *,
    policy: SigningPolicy,
    expected_request_sha256: str,
    verify_ed25519: Callable[[bytes, bytes, bytes], object],
    now: datetime,
) -> SignedReceipt:
    """Verify one canonical receipt through an injected Ed25519 verifier.

    ``expected_request_sha256`` is the caller's exact, fresh receiver request
    binding.  It prevents a receipt valid for one request from being replayed
    against another request that happens to share the same short-lived policy.
    ``verify_ed25519`` receives ``(public_key, signature, canonical_payload)``
    and must return exactly ``True`` or ``None`` on success.  It is invoked
    only after canonical parsing, policy/receipt identity binding, replay
    binding, policy validity, and receipt observation time have all been
    checked.  A caller must inject a release-bound cryptographic verifier;
    this foundation deliberately has no third-party verification dependency of
    its own.
    """

    if not isinstance(policy, SigningPolicy):
        raise RemoteReceiverSigningPolicyError("signing policy is invalid")
    if not callable(verify_ed25519):
        raise RemoteReceiverSigningPolicyError("Ed25519 verifier is unavailable")
    expected_request_sha256 = _nonzero_sha256(
        expected_request_sha256,
        label="expected receiver request",
    )
    verification_time = _verification_time(now)
    # Revalidate the reconstructed public policy before interpreting its
    # expiry or using its key.  This prevents callers from fabricating an
    # unchecked dataclass instance around otherwise well-formed receipt data.
    policy = validate_policy_document(policy.document())
    receipt = parse_receipt_payload(payload, policy=policy)
    if receipt.document["request_sha256"] != expected_request_sha256:
        raise RemoteReceiverSigningPolicyError(
            "signed receiver receipt request binding differs"
        )
    observed_at = _timestamp(receipt.document["observed_at"], label="observation time")
    if (
        observed_at < policy.not_before
        or observed_at > policy.expires_at
        or verification_time < policy.not_before
        or verification_time > policy.expires_at
    ):
        raise RemoteReceiverSigningPolicyError("signed receiver receipt is outside policy validity")
    try:
        outcome = verify_ed25519(policy.public_key, receipt.signature, receipt.signature_payload)
    except Exception as exc:
        raise RemoteReceiverSigningPolicyError("signed receiver signature verification failed") from exc
    if outcome is not None and outcome is not True:
        raise RemoteReceiverSigningPolicyError("signed receiver signature verification failed")
    return receipt
