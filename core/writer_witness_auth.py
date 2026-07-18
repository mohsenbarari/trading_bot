"""Pairwise HMAC authentication for the private writer-witness control API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac

from core.runtime_sites import WEBAPP_SITES


WITNESS_AUTH_VERSION = 1
WITNESS_TRANSITION_PATH = "/v1/writer-witness/transitions"
WITNESS_STATUS_PATH = "/v1/writer-witness/status"
HEADER_KEY_ID = "X-Writer-Witness-Key-Id"
HEADER_SITE = "X-Writer-Witness-Site"
HEADER_TIMESTAMP = "X-Writer-Witness-Timestamp"
HEADER_REQUEST_ID = "X-Writer-Witness-Request-Id"
HEADER_SIGNATURE = "X-Writer-Witness-Signature"


class WitnessAuthenticationError(RuntimeError):
    """Raised when a private witness request cannot be authenticated."""

    def __init__(self, message: str, *, code: str = "witness_auth_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WitnessClientCredential:
    key_id: str
    site: str
    secret: str
    not_after: datetime | None = None


@dataclass(frozen=True)
class VerifiedWitnessCaller:
    key_id: str
    site: str
    request_id: str
    timestamp: int
    credential_not_after: datetime | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_witness_request(
    *,
    method: str,
    path: str,
    timestamp: int,
    request_id: str,
    site: str,
    body: bytes,
) -> bytes:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (
            f"writer-witness-auth-v{WITNESS_AUTH_VERSION}",
            method.strip().upper(),
            path.strip(),
            str(int(timestamp)),
            request_id.strip(),
            site.strip().lower(),
            body_hash,
        )
    ).encode("utf-8")


def sign_witness_request(
    *,
    credential: WitnessClientCredential,
    method: str,
    path: str,
    body: bytes,
    request_id: str,
    timestamp: int,
) -> dict[str, str]:
    key_id = credential.key_id.strip()
    site = credential.site.strip().lower()
    secret = credential.secret
    request_id = request_id.strip()
    if not key_id or len(key_id) > 64:
        raise WitnessAuthenticationError("witness key id is invalid")
    if site not in WEBAPP_SITES:
        raise WitnessAuthenticationError("witness credential site is invalid")
    if not request_id or len(request_id) > 64:
        raise WitnessAuthenticationError("witness request id is invalid")
    if len(secret.encode("utf-8")) < 32:
        raise WitnessAuthenticationError("witness HMAC secret must be at least 32 bytes")
    canonical = canonical_witness_request(
        method=method,
        path=path,
        timestamp=timestamp,
        request_id=request_id,
        site=site,
        body=body,
    )
    signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return {
        HEADER_KEY_ID: key_id,
        HEADER_SITE: site,
        HEADER_TIMESTAMP: str(int(timestamp)),
        HEADER_REQUEST_ID: request_id,
        HEADER_SIGNATURE: signature,
        "Content-Type": "application/json",
    }


def verify_witness_request(
    *,
    credentials: dict[str, WitnessClientCredential],
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    now: datetime,
    max_age_seconds: int = 15,
    max_future_skew_seconds: int = 5,
) -> VerifiedWitnessCaller:
    key_id = str(headers.get(HEADER_KEY_ID.lower()) or headers.get(HEADER_KEY_ID) or "").strip()
    site = str(headers.get(HEADER_SITE.lower()) or headers.get(HEADER_SITE) or "").strip().lower()
    request_id = str(
        headers.get(HEADER_REQUEST_ID.lower()) or headers.get(HEADER_REQUEST_ID) or ""
    ).strip()
    signature = str(
        headers.get(HEADER_SIGNATURE.lower()) or headers.get(HEADER_SIGNATURE) or ""
    ).strip().lower()
    timestamp_text = str(
        headers.get(HEADER_TIMESTAMP.lower()) or headers.get(HEADER_TIMESTAMP) or ""
    ).strip()
    credential = credentials.get(key_id)
    if credential is None or credential.site != site or site not in WEBAPP_SITES:
        raise WitnessAuthenticationError("unknown witness client credential")
    current = _utc(now)
    if credential.not_after is not None and current >= _utc(credential.not_after):
        raise WitnessAuthenticationError(
            "witness client credential has expired",
            code="witness_campaign_expired",
        )
    if not request_id or len(request_id) > 64:
        raise WitnessAuthenticationError("witness request id is invalid")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise WitnessAuthenticationError("witness request timestamp is invalid") from exc
    current_timestamp = int(current.timestamp())
    age = current_timestamp - timestamp
    if age > max(1, int(max_age_seconds)):
        raise WitnessAuthenticationError(
            "witness request timestamp is stale",
            code="witness_auth_stale",
        )
    if age < -max(0, int(max_future_skew_seconds)):
        raise WitnessAuthenticationError(
            "witness request timestamp is too far in the future",
            code="witness_auth_future",
        )
    canonical = canonical_witness_request(
        method=method,
        path=path,
        timestamp=timestamp,
        request_id=request_id,
        site=site,
        body=body,
    )
    expected = hmac.new(
        credential.secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    if len(signature) != 64 or not hmac.compare_digest(signature, expected):
        raise WitnessAuthenticationError("witness request signature is invalid")
    return VerifiedWitnessCaller(
        key_id=credential.key_id,
        site=site,
        request_id=request_id,
        timestamp=timestamp,
        credential_not_after=credential.not_after,
    )
