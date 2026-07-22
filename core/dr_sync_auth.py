"""Pairwise, source/destination-bound authentication for DR event transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time
from typing import Any

from core.runtime_sites import PHYSICAL_SITES


DR_SYNC_PROTOCOL = "dr-sync-v1"
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


class DrSyncAuthError(RuntimeError):
    """Raised when DR transport identity or authentication is invalid."""


@dataclass(frozen=True)
class PairwiseDrKey:
    key_id: str
    source_site: str
    destination_site: str
    secret: str


@dataclass(frozen=True)
class ValidatedDrRequest:
    key_id: str
    source_site: str
    destination_site: str
    nonce: str
    timestamp: int
    request_hash: str


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DrSyncAuthError(f"duplicate DR key configuration field: {key}")
        result[key] = value
    return result


def parse_pairwise_keys(raw: str | None) -> dict[str, PairwiseDrKey]:
    try:
        payload = json.loads(raw or "", object_pairs_hook=_reject_duplicate_json_pairs)
    except (json.JSONDecodeError, DrSyncAuthError) as exc:
        raise DrSyncAuthError("DR pairwise key configuration is not valid strict JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise DrSyncAuthError("DR pairwise key configuration must be a non-empty list")
    result: dict[str, PairwiseDrKey] = {}
    directed_pairs: set[tuple[str, str]] = set()
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"key_id", "source_site", "destination_site", "secret"}:
            raise DrSyncAuthError("DR pairwise key entry fields are invalid")
        key = PairwiseDrKey(**{name: str(item[name]) for name in item})
        if not key.key_id or len(key.key_id) > 64 or key.key_id in result:
            raise DrSyncAuthError("DR pairwise key_id is empty, duplicate, or too long")
        if key.source_site not in PHYSICAL_SITES or key.destination_site not in PHYSICAL_SITES:
            raise DrSyncAuthError("DR pairwise key contains an unknown site")
        if key.source_site == key.destination_site:
            raise DrSyncAuthError("DR pairwise key must bind two different sites")
        if len(key.secret.encode("utf-8")) < 32:
            raise DrSyncAuthError("DR pairwise secret must be at least 32 bytes")
        pair = (key.source_site, key.destination_site)
        if pair in directed_pairs:
            raise DrSyncAuthError("DR directed site pair has more than one active key")
        directed_pairs.add(pair)
        result[key.key_id] = key
    return result


def canonical_request_bytes(
    *, method: str,
    path: str,
    body: bytes,
    timestamp: int,
    nonce: str,
    key_id: str,
    source_site: str,
    destination_site: str,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    lines = (
        DR_SYNC_PROTOCOL,
        method.upper(),
        path,
        body_hash,
        str(timestamp),
        nonce,
        key_id,
        source_site,
        destination_site,
    )
    return "\n".join(lines).encode("utf-8")


def sign_request(**kwargs: Any) -> str:
    secret = str(kwargs.pop("secret"))
    return hmac.new(secret.encode("utf-8"), canonical_request_bytes(**kwargs), hashlib.sha256).hexdigest()


def canonical_acknowledgement_bytes(payload: dict[str, Any]) -> bytes:
    return (
        DR_SYNC_PROTOCOL.encode("ascii")
        + b"\nACK\n"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def sign_acknowledgement(*, payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_acknowledgement_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def acknowledgement_signature_is_valid(
    *, payload: dict[str, Any], signature: str, secret: str
) -> bool:
    expected = sign_acknowledgement(payload=payload, secret=secret)
    return hmac.compare_digest(str(signature), expected)


def verify_request(
    *,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    keys: dict[str, PairwiseDrKey],
    expected_destination_site: str,
    now: int | None = None,
    max_age_seconds: int = 30,
) -> ValidatedDrRequest:
    key_id = str(headers.get("x-dr-key-id") or "")
    source = str(headers.get("x-dr-source-site") or "")
    destination = str(headers.get("x-dr-destination-site") or "")
    nonce = str(headers.get("x-dr-nonce") or "")
    signature = str(headers.get("x-dr-signature") or "")
    protocol = str(headers.get("x-dr-protocol") or "")
    try:
        timestamp = int(headers.get("x-dr-timestamp") or "")
    except ValueError as exc:
        raise DrSyncAuthError("DR request timestamp is invalid") from exc
    if protocol != DR_SYNC_PROTOCOL:
        raise DrSyncAuthError("DR transport protocol is missing or unsupported")
    if destination != expected_destination_site:
        raise DrSyncAuthError("DR request destination does not match the local physical site")
    key = keys.get(key_id)
    if key is None or key.source_site != source or key.destination_site != destination:
        raise DrSyncAuthError("DR key identity does not match source/destination")
    if not NONCE_RE.fullmatch(nonce):
        raise DrSyncAuthError("DR request nonce is malformed")
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max(1, int(max_age_seconds)):
        raise DrSyncAuthError("DR request timestamp is outside the replay window")
    canonical = canonical_request_bytes(
        method=method,
        path=path,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        key_id=key_id,
        source_site=source,
        destination_site=destination,
    )
    expected = hmac.new(key.secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise DrSyncAuthError("DR request signature is invalid")
    return ValidatedDrRequest(
        key_id=key_id,
        source_site=source,
        destination_site=destination,
        nonce=nonce,
        timestamp=timestamp,
        request_hash=hashlib.sha256(canonical).hexdigest(),
    )
