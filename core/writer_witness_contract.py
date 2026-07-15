"""Signed, time-bounded proof for the global WebApp writer term."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.runtime_sites import WEBAPP_SITES


WITNESS_PROOF_VERSION = 1
WITNESS_AUTHORITY = "webapp"
SIGNED_FIELDS = (
    "version",
    "authority",
    "holder_site",
    "writer_epoch",
    "lease_id",
    "issued_at",
    "expires_at",
    "witness_transition_id",
)
PROOF_FIELDS = frozenset({*SIGNED_FIELDS, "signature"})


class WitnessProofError(RuntimeError):
    """Raised when a witness proof is malformed, stale, or untrusted."""


@dataclass(frozen=True)
class ValidatedWitnessLeaseProof:
    holder_site: str
    writer_epoch: int
    lease_id: str
    issued_at: datetime
    expires_at: datetime
    witness_transition_id: str
    proof_hash: str
    canonical_payload: dict[str, Any]


def witness_timing_configuration_is_safe(
    *,
    lease_duration_seconds: int,
    renew_interval_seconds: int,
    safety_margin_seconds: int,
    max_clock_skew_seconds: int,
) -> bool:
    duration = int(lease_duration_seconds)
    renewal = int(renew_interval_seconds)
    margin = int(safety_margin_seconds)
    skew = int(max_clock_skew_seconds)
    return (
        duration >= 30
        and renewal > 0
        and skew >= 0
        and margin > skew
        and renewal + margin + skew < duration
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise WitnessProofError(f"witness proof requires {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WitnessProofError(f"witness proof {field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WitnessProofError(f"witness proof {field} must include a timezone")
    return _utc(parsed)


def _decode_key(value: str, *, expected_length: int, label: str) -> bytes:
    try:
        decoded = base64.b64decode(str(value or "").strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise WitnessProofError(f"{label} is not valid base64") from exc
    if len(decoded) != expected_length:
        raise WitnessProofError(f"{label} must decode to {expected_length} bytes")
    return decoded


def witness_public_key_is_valid(value: str | None) -> bool:
    try:
        raw = _decode_key(value or "", expected_length=32, label="witness public key")
        Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, WitnessProofError):
        return False
    return True


def _canonical_unsigned(payload: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    unsigned = {field: payload.get(field) for field in SIGNED_FIELDS}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return unsigned, encoded


def sign_witness_lease_proof(
    *,
    holder_site: str,
    writer_epoch: int,
    lease_id: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_transition_id: str,
    private_key_base64: str,
) -> dict[str, Any]:
    if holder_site not in WEBAPP_SITES:
        raise WitnessProofError(f"unsupported holder_site={holder_site!r}")
    if isinstance(writer_epoch, bool) or not isinstance(writer_epoch, int) or writer_epoch < 1:
        raise WitnessProofError("witness proof writer_epoch must be a positive integer")
    if (
        not isinstance(lease_id, str)
        or not lease_id
        or lease_id != lease_id.strip()
        or len(lease_id) > 64
    ):
        raise WitnessProofError("witness proof lease_id is required and must be <= 64 characters")
    if (
        not isinstance(witness_transition_id, str)
        or not witness_transition_id
        or witness_transition_id != witness_transition_id.strip()
        or len(witness_transition_id) > 64
    ):
        raise WitnessProofError(
            "witness proof transition id is required and must be <= 64 characters"
        )
    unsigned = {
        "version": WITNESS_PROOF_VERSION,
        "authority": WITNESS_AUTHORITY,
        "holder_site": holder_site,
        "writer_epoch": int(writer_epoch),
        "lease_id": str(lease_id),
        "issued_at": _utc(issued_at).isoformat(),
        "expires_at": _utc(expires_at).isoformat(),
        "witness_transition_id": str(witness_transition_id),
    }
    _, encoded = _canonical_unsigned(unsigned)
    private_key = Ed25519PrivateKey.from_private_bytes(
        _decode_key(private_key_base64, expected_length=32, label="witness private key")
    )
    signature = private_key.sign(encoded)
    return {**unsigned, "signature": base64.b64encode(signature).decode("ascii")}


def validate_witness_lease_proof(
    payload: dict[str, Any],
    *,
    public_key_base64: str,
    expected_site: str,
    expected_epoch: int | None = None,
    now: datetime | None = None,
    safety_margin_seconds: int = 15,
    max_clock_skew_seconds: int = 5,
    max_lifetime_seconds: int = 180,
) -> ValidatedWitnessLeaseProof:
    if expected_site not in WEBAPP_SITES:
        raise WitnessProofError(f"unsupported expected_site={expected_site!r}")
    if not isinstance(payload, dict):
        raise WitnessProofError("witness proof must be a JSON object")
    if set(payload) != PROOF_FIELDS:
        raise WitnessProofError("witness proof fields do not match the versioned contract")
    if type(payload.get("version")) is not int or payload.get("version") != WITNESS_PROOF_VERSION:
        raise WitnessProofError("unsupported witness proof version")
    if payload.get("authority") != WITNESS_AUTHORITY:
        raise WitnessProofError("witness proof authority is not webapp")
    if payload.get("holder_site") != expected_site:
        raise WitnessProofError("witness proof holder does not match the local site")
    if type(payload.get("writer_epoch")) is not int:
        raise WitnessProofError("witness proof writer_epoch must be an integer")
    writer_epoch = payload["writer_epoch"]
    if writer_epoch < 1:
        raise WitnessProofError("witness proof writer_epoch must be positive")
    if expected_epoch is not None and writer_epoch != int(expected_epoch):
        raise WitnessProofError("witness proof epoch does not match the expected writer term")
    if not isinstance(payload.get("lease_id"), str):
        raise WitnessProofError("witness proof lease_id must be a string")
    if not isinstance(payload.get("witness_transition_id"), str):
        raise WitnessProofError("witness proof transition id must be a string")
    lease_id = payload["lease_id"].strip()
    transition_id = payload["witness_transition_id"].strip()
    if lease_id != payload["lease_id"] or transition_id != payload["witness_transition_id"]:
        raise WitnessProofError("witness proof identifiers must not contain outer whitespace")
    if not lease_id or len(lease_id) > 64:
        raise WitnessProofError("witness proof lease_id is required and must be <= 64 characters")
    if not transition_id or len(transition_id) > 64:
        raise WitnessProofError(
            "witness proof transition id is required and must be <= 64 characters"
        )

    issued_at = _parse_timestamp(payload.get("issued_at"), "issued_at")
    expires_at = _parse_timestamp(payload.get("expires_at"), "expires_at")
    current = _utc(now or datetime.now(timezone.utc))
    skew = timedelta(seconds=max(0, int(max_clock_skew_seconds)))
    margin = timedelta(seconds=max(0, int(safety_margin_seconds)))
    maximum_lifetime = timedelta(seconds=max(1, int(max_lifetime_seconds)))
    if issued_at > current + skew:
        raise WitnessProofError("witness proof was issued too far in the future")
    if expires_at <= issued_at:
        raise WitnessProofError("witness proof expiry must be after issue time")
    if expires_at - issued_at > maximum_lifetime:
        raise WitnessProofError("witness proof lifetime exceeds the configured maximum")
    if expires_at <= current + margin:
        raise WitnessProofError("witness proof is expired or inside the local safety margin")

    unsigned, encoded = _canonical_unsigned(payload)
    signature = _decode_key(
        str(payload.get("signature") or ""),
        expected_length=64,
        label="witness proof signature",
    )
    public_key = Ed25519PublicKey.from_public_bytes(
        _decode_key(public_key_base64, expected_length=32, label="witness public key")
    )
    try:
        public_key.verify(signature, encoded)
    except InvalidSignature as exc:
        raise WitnessProofError("witness proof signature is invalid") from exc

    canonical_payload = {**unsigned, "signature": payload["signature"]}
    canonical = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ValidatedWitnessLeaseProof(
        holder_site=expected_site,
        writer_epoch=writer_epoch,
        lease_id=lease_id,
        issued_at=issued_at,
        expires_at=expires_at,
        witness_transition_id=transition_id,
        proof_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        canonical_payload=canonical_payload,
    )
