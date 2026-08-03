"""Bounded, fail-closed evidence for the same-region durability write gate.

The connectivity controller deliberately cannot assert journal or Blob health.
This module is used by a separate, short-lived control-plane command after it
has independently re-read a committed journal record and a locally stored,
signed Blob receipt acknowledgement.  It does not move data and it never
creates a positive result from a liveness probe alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping

from core.dr_event_protocol import canonical_json_bytes


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE = re.compile(r"^[0-9a-f]{40,64}$")


class DurabilityHealthError(RuntimeError):
    """The independent durability evidence is incomplete or stale."""


@dataclass(frozen=True)
class BlobReceiptEvidence:
    content_hash: str
    destination_site: str
    delivery_status: str
    acknowledged_at: datetime | None
    acknowledgement_hash: str | None
    manifest_state: str
    object_version_id: str | None
    object_ciphertext_hash: str | None
    object_ciphertext_size: int | None
    encryption_key_id: str | None
    encryption_algorithm: str | None


@dataclass(frozen=True)
class DurabilityHealthUpdate:
    evidence_hash: str
    evidence_expires_at: datetime
    updated_by: str


def _utc(value: datetime | None, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise DurabilityHealthError(f"{field} is missing")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha(value: Any, *, field: str) -> str:
    normalized = str(value or "")
    if _SHA256.fullmatch(normalized) is None:
        raise DurabilityHealthError(f"{field} is not a SHA-256")
    return normalized


def _release(value: Any) -> str:
    normalized = str(value or "")
    if _RELEASE.fullmatch(normalized) is None:
        raise DurabilityHealthError("release SHA is invalid")
    return normalized


def build_durability_health_update(
    *,
    connectivity_mode: str,
    connectivity_evidence_hash: str | None,
    connectivity_evidence_expires_at: datetime | None,
    journal_gid: str,
    journal: Mapping[str, Any],
    blob: BlobReceiptEvidence,
    release_sha: str,
    operator: str,
    now: datetime | None = None,
    max_blob_age_seconds: int = 120,
    ttl_seconds: int = 60,
) -> DurabilityHealthUpdate:
    """Validate both evidence planes and return a non-extending state update.

    The journal client has already verified the Bot-FI acknowledgement MAC;
    this function still validates every identity that binds that response to
    the exact current release.  The Blob acknowledgement was verified by the
    WebApp-FI receiver before it entered ``dr_blob_deliveries``.  Requiring
    the source manifest as well binds that remote read-back to its exact
    Object Storage version and ciphertext identity.
    """

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized_operator = str(operator or "").strip()
    if not normalized_operator or len(normalized_operator) > 128:
        raise DurabilityHealthError("durability health operator identity is invalid")
    if int(max_blob_age_seconds) < 10 or int(max_blob_age_seconds) > 300:
        raise DurabilityHealthError("Blob receipt maximum age must be 10..300 seconds")
    if int(ttl_seconds) < 10 or int(ttl_seconds) > 120:
        raise DurabilityHealthError("durability health TTL must be 10..120 seconds")
    if connectivity_mode != "online":
        raise DurabilityHealthError("connectivity evidence is not online")
    connectivity_hash = _sha(connectivity_evidence_hash, field="connectivity evidence hash")
    connectivity_expiry = _utc(
        connectivity_evidence_expires_at, field="connectivity evidence expiry"
    )
    if connectivity_expiry <= current:
        raise DurabilityHealthError("connectivity evidence is expired")

    expected_release = _release(release_sha)
    normalized_gid = str(journal_gid or "")
    if not normalized_gid:
        raise DurabilityHealthError("journal GID is missing")
    if not isinstance(journal, Mapping):
        raise DurabilityHealthError("journal evidence is invalid")
    if (
        journal.get("state") != "committed"
        or str(journal.get("local_transaction_gid") or "") != normalized_gid
        or str(journal.get("prepared_transaction_gid") or "") != normalized_gid
        or str(journal.get("release_sha") or "") != expected_release
    ):
        raise DurabilityHealthError("journal evidence does not bind a committed current-release GID")
    journal_transaction_hash = _sha(
        journal.get("transaction_hash"), field="journal transaction hash"
    )
    journal_ciphertext_hash = _sha(
        journal.get("ciphertext_hash"), field="journal ciphertext hash"
    )

    if blob.destination_site != "webapp_ir":
        raise DurabilityHealthError("Blob receipt destination must be WebApp-IR")
    content_hash = _sha(blob.content_hash, field="Blob content hash")
    acknowledgement_hash = _sha(
        blob.acknowledgement_hash, field="Blob acknowledgement hash"
    )
    ciphertext_hash = _sha(
        blob.object_ciphertext_hash, field="Blob ciphertext hash"
    )
    if blob.delivery_status != "acknowledged" or blob.manifest_state != "uploaded":
        raise DurabilityHealthError("Blob receipt is not acknowledged against an uploaded manifest")
    if not blob.object_version_id or len(str(blob.object_version_id)) > 255:
        raise DurabilityHealthError("Blob receipt lacks an exact Object Storage version")
    if (
        type(blob.object_ciphertext_size) is not int
        or int(blob.object_ciphertext_size) <= 0
        or not blob.encryption_key_id
        or blob.encryption_algorithm != "AES-256-GCM-v1"
    ):
        raise DurabilityHealthError("Blob receipt cipher identity is invalid")
    acknowledgement_time = _utc(blob.acknowledged_at, field="Blob acknowledgement time")
    if acknowledgement_time > current + timedelta(seconds=5):
        raise DurabilityHealthError("Blob acknowledgement time is in the future")
    if acknowledgement_time < current - timedelta(seconds=int(max_blob_age_seconds)):
        raise DurabilityHealthError("Blob acknowledgement is stale")

    evidence = {
        "schema": "three-site-durability-health-v1",
        "release_sha": expected_release,
        "connectivity_evidence_hash": connectivity_hash,
        "journal": {
            "local_transaction_gid": normalized_gid,
            "transaction_hash": journal_transaction_hash,
            "ciphertext_hash": journal_ciphertext_hash,
        },
        "blob": {
            "content_hash": content_hash,
            "destination_site": blob.destination_site,
            "acknowledgement_hash": acknowledgement_hash,
            "object_version_id": str(blob.object_version_id),
            "object_ciphertext_hash": ciphertext_hash,
            "object_ciphertext_size": int(blob.object_ciphertext_size),
            "encryption_key_id": str(blob.encryption_key_id),
            "encryption_algorithm": blob.encryption_algorithm,
        },
    }
    evidence_hash = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    # A health refresh must never lengthen the independently observed online
    # connectivity window.  Either input going stale therefore closes the
    # write gate without needing a background worker.
    evidence_expires_at = min(
        connectivity_expiry,
        current + timedelta(seconds=int(ttl_seconds)),
    )
    if evidence_expires_at <= current:
        raise DurabilityHealthError("durability health evidence has no remaining lifetime")
    return DurabilityHealthUpdate(
        evidence_hash=evidence_hash,
        evidence_expires_at=evidence_expires_at,
        updated_by=normalized_operator,
    )
