"""Authenticated envelopes for the Full Matrix Object Storage control plane.

The protocol deliberately carries a closed operation name and a bounded JSON
context.  It never carries shell text, argv, environment variables, file
contents, or operator-selected paths.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


REQUEST_SCHEMA = "three-site-full-matrix-object-storage-request-v1"
RESPONSE_SCHEMA = "three-site-full-matrix-object-storage-response-v1"
ROLE = "webapp_ir"
OPERATIONS = frozenset(
    {
        "host_snapshot",
        "scenario_execute",
        "scenario_observe",
        "recover_faults",
        "cleanup_iteration",
        "finalize_campaign",
        "timing_clock",
        "timing_snapshot",
        "timing_emit",
        "timing_cleanup",
        "recovery_delivery_fault",
        "recovery_delivery_resume_emit",
        "origin_local_probe",
        "customer_actor_matrix",
        "cross_writer_session_verify",
        "failover_site_operation",
    }
)
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}\Z")
# Timing snapshots contain redacted journal metadata for a bounded matrix
# sample set.  They are encrypted and signed on the private versioned bucket;
# two MiB is sufficient for 100 samples/route while still preventing the
# control plane from becoming a bulk-transfer channel.
MAX_CONTEXT_BYTES = 2 * 1024 * 1024
MAX_CLOCK_SKEW_SECONDS = 30
MAX_REQUEST_LIFETIME_SECONDS = 600


class ObjectStorageProtocolError(RuntimeError):
    """An Object Storage control envelope failed closed validation."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectStorageProtocolError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ObjectStorageProtocolError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ObjectStorageProtocolError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ObjectStorageProtocolError(f"{label} timestamp lacks a timezone")
    return parsed.astimezone(timezone.utc)


def public_key_id(public_key_raw: bytes) -> str:
    if len(public_key_raw) != 32:
        raise ObjectStorageProtocolError("Ed25519 public key length is invalid")
    return hashlib.sha256(public_key_raw).hexdigest()


def _decode_public_key(value: str) -> tuple[Ed25519PublicKey, bytes]:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ObjectStorageProtocolError("Ed25519 public key is malformed") from exc
    if len(raw) != 32:
        raise ObjectStorageProtocolError("Ed25519 public key length is invalid")
    return Ed25519PublicKey.from_public_bytes(raw), raw


def _decode_signature(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ObjectStorageProtocolError("Ed25519 signature is malformed")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ObjectStorageProtocolError("Ed25519 signature is malformed") from exc
    if len(raw) != 64:
        raise ObjectStorageProtocolError("Ed25519 signature length is invalid")
    return raw


def public_key_b64(public_key_raw: bytes) -> str:
    if len(public_key_raw) != 32:
        raise ObjectStorageProtocolError("Ed25519 public key length is invalid")
    return base64.b64encode(public_key_raw).decode("ascii")


def signature_b64(private_key: Ed25519PrivateKey, payload: Mapping[str, Any]) -> str:
    return base64.b64encode(private_key.sign(canonical_bytes(payload))).decode("ascii")


def _validate_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObjectStorageProtocolError("request context must be an object")
    encoded = canonical_bytes(value)
    if len(encoded) > MAX_CONTEXT_BYTES:
        raise ObjectStorageProtocolError("request context exceeds its fixed bound")
    forbidden = {
        "argv",
        "command",
        "commands",
        "cwd",
        "env",
        "environment",
        "path",
        "script",
        "shell",
        "stdin",
    }
    pending: list[Any] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if any(str(key).lower() in forbidden for key in current):
                raise ObjectStorageProtocolError(
                    "request context contains an execution surface"
                )
            pending.extend(current.values())
        elif isinstance(current, list):
            if len(current) > 1024:
                raise ObjectStorageProtocolError("request context list is too large")
            pending.extend(current)
        elif current is None or isinstance(current, (bool, int, float)):
            continue
        elif isinstance(current, str):
            if len(current) > 8192 or "\x00" in current:
                raise ObjectStorageProtocolError("request context string is unsafe")
        else:
            raise ObjectStorageProtocolError("request context contains an invalid value")
    return dict(value)


def _common_request_fields(value: Mapping[str, Any]) -> None:
    if (
        value.get("schema") != REQUEST_SCHEMA
        or value.get("role") != ROLE
        or value.get("operation") not in OPERATIONS
        or UUID_RE.fullmatch(str(value.get("request_id") or "")) is None
        or SHA40_RE.fullmatch(str(value.get("release_sha") or "")) is None
        or type(value.get("sequence")) is not int
        or int(value["sequence"]) < 1
        or type(value.get("attempt")) is not int
        or int(value["attempt"]) < 1
        or SAFE_NAME_RE.fullmatch(str(value.get("campaign_id") or "")) is None
        or SAFE_NAME_RE.fullmatch(str(value.get("controller_key_id") or "")) is None
    ):
        raise ObjectStorageProtocolError("request identity is invalid")


def build_request(
    *,
    private_key: Ed25519PrivateKey,
    controller_key_id: str,
    request_id: str,
    campaign_id: str,
    release_sha: str,
    sequence: int,
    attempt: int,
    operation: str,
    context: Mapping[str, Any],
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "role": ROLE,
        "request_id": request_id,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "sequence": sequence,
        "attempt": attempt,
        "operation": operation,
        "context": _validate_context(dict(context)),
        "issued_at": issued_at.astimezone(timezone.utc).isoformat(),
        "expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        "controller_key_id": controller_key_id,
    }
    _common_request_fields(unsigned)
    return {**unsigned, "signature": signature_b64(private_key, unsigned)}


def verify_request(
    value: Any,
    *,
    controller_public_key_b64: str,
    expected_release_sha: str,
    expected_campaign_id: str,
    minimum_sequence: int,
    now: datetime,
) -> dict[str, Any]:
    fields = {
        "schema",
        "role",
        "request_id",
        "campaign_id",
        "release_sha",
        "sequence",
        "attempt",
        "operation",
        "context",
        "issued_at",
        "expires_at",
        "controller_key_id",
        "signature",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ObjectStorageProtocolError("request fields are invalid")
    _common_request_fields(value)
    _validate_context(value["context"])
    if (
        value["release_sha"] != expected_release_sha
        or value["campaign_id"] != expected_campaign_id
        or int(value["sequence"]) < minimum_sequence
    ):
        raise ObjectStorageProtocolError("request binding or sequence is invalid")
    issued_at = parse_timestamp(value["issued_at"], label="request issued_at")
    expires_at = parse_timestamp(value["expires_at"], label="request expires_at")
    current = now.astimezone(timezone.utc)
    lifetime = (expires_at - issued_at).total_seconds()
    if (issued_at - current).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
        raise ObjectStorageProtocolError("request is future-dated")
    if current > expires_at or not 1 <= lifetime <= MAX_REQUEST_LIFETIME_SECONDS:
        raise ObjectStorageProtocolError("request is expired or has an unsafe lifetime")
    public_key, raw_key = _decode_public_key(controller_public_key_b64)
    if value["controller_key_id"] != public_key_id(raw_key):
        raise ObjectStorageProtocolError("request controller key id differs")
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    try:
        public_key.verify(_decode_signature(value["signature"]), canonical_bytes(unsigned))
    except InvalidSignature as exc:
        raise ObjectStorageProtocolError("request signature is invalid") from exc
    return dict(value)


def build_response(
    *,
    private_key: Ed25519PrivateKey,
    agent_key_id: str,
    request: Mapping[str, Any],
    request_sha256: str,
    status: str,
    result: Mapping[str, Any],
    completed_at: datetime,
) -> dict[str, Any]:
    if (
        status not in {"passed", "failed"}
        or SHA256_RE.fullmatch(request_sha256) is None
        or not isinstance(result, Mapping)
        or len(canonical_bytes(result)) > MAX_CONTEXT_BYTES
    ):
        raise ObjectStorageProtocolError("response result is invalid")
    unsigned: dict[str, Any] = {
        "schema": RESPONSE_SCHEMA,
        "role": ROLE,
        "request_id": request["request_id"],
        "campaign_id": request["campaign_id"],
        "release_sha": request["release_sha"],
        "sequence": request["sequence"],
        "attempt": request["attempt"],
        "operation": request["operation"],
        "request_sha256": request_sha256,
        "status": status,
        "result": dict(result),
        "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        "agent_key_id": agent_key_id,
    }
    return {**unsigned, "signature": signature_b64(private_key, unsigned)}


def verify_response(
    value: Any,
    *,
    agent_public_key_b64: str,
    request: Mapping[str, Any],
    request_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "role",
        "request_id",
        "campaign_id",
        "release_sha",
        "sequence",
        "attempt",
        "operation",
        "request_sha256",
        "status",
        "result",
        "completed_at",
        "agent_key_id",
        "signature",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ObjectStorageProtocolError("response fields are invalid")
    for key in (
        "request_id",
        "campaign_id",
        "release_sha",
        "sequence",
        "attempt",
        "operation",
    ):
        if value.get(key) != request.get(key):
            raise ObjectStorageProtocolError("response request binding differs")
    if (
        value.get("schema") != RESPONSE_SCHEMA
        or value.get("role") != ROLE
        or value.get("request_sha256") != request_sha256
        or value.get("status") not in {"passed", "failed"}
        or not isinstance(value.get("result"), dict)
        or len(canonical_bytes(value["result"])) > MAX_CONTEXT_BYTES
    ):
        raise ObjectStorageProtocolError("response identity/result is invalid")
    parse_timestamp(value["completed_at"], label="response completed_at")
    public_key, raw_key = _decode_public_key(agent_public_key_b64)
    if value.get("agent_key_id") != public_key_id(raw_key):
        raise ObjectStorageProtocolError("response agent key id differs")
    unsigned = {key: item for key, item in value.items() if key != "signature"}
    try:
        public_key.verify(_decode_signature(value["signature"]), canonical_bytes(unsigned))
    except InvalidSignature as exc:
        raise ObjectStorageProtocolError("response signature is invalid") from exc
    return dict(value)
