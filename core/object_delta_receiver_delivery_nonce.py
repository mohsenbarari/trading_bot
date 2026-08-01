"""Pure durable-nonce contract for a future Object-delta receiver.

Signed delivery packets carry a short-lived nonce.  In-memory verification is
not enough: a retry after a process crash could otherwise turn one immutable
Object into a second database apply.  A future receiver must insert the
receipt derived here in the same transaction as its import receipt and cursor.

This module deliberately performs no database, Object Storage, age, clock, or
network I/O.  Its caller must already have verified a packet, fetched and
decrypted the exact Object version, and parsed the matching batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    MAX_DELTA_PAYLOAD_BYTES,
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    AppendOnlySyncDeltaBatch,
)
from core.object_delta_delivery_control_packet import (
    MAX_CONTROL_PACKET_TTL,
    VerifiedObjectDeltaDeliveryControlPacket,
    revalidate_verified_object_delta_delivery_control_packet,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE, BUCKET_RE


RECEIVER_DELIVERY_NONCE_CLAIM_SCHEMA = "gold-trade-object-delta-receiver-delivery-nonce-v1"
RECEIVER_DELIVERY_NONCE_ACTION_CONSUME = "consume"
RECEIVER_DELIVERY_NONCE_ACTION_REPLAY = "replay"

_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")


class ObjectDeltaReceiverDeliveryNonceError(ValueError):
    """A delivery nonce cannot be durably consumed without ambiguity."""


@dataclass(frozen=True)
class ObjectDeltaReceiverDeliveryNonceReceipt:
    """Exact local state that consumes one controller packet nonce.

    The database uniqueness boundary is ``(controller_key_id, nonce)``.
    ``packet_claim_sha256`` covers every signed value that changes the import
    decision, so reuse of the pair with different contents fails closed.
    """

    controller_key_id: str
    nonce: str
    packet_claim_sha256: str
    bucket: str
    source_site: str
    destination_site: str
    destination_age_recipient: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    writer_epoch: int
    writer_lease_id: str
    first_sequence: int
    last_sequence: int
    batch_sha256: str
    object_key: str
    object_version_id: str
    expires_at: datetime


@dataclass(frozen=True)
class ObjectDeltaReceiverDeliveryNoncePlan:
    """Caller-owned transaction decision for a nonce insert or exact replay."""

    action: str
    receipt_to_insert: ObjectDeltaReceiverDeliveryNonceReceipt | None


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ObjectDeltaReceiverDeliveryNonceError(
            "delivery nonce claim cannot be canonically encoded"
        ) from exc


def _require_text(value: object, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaReceiverDeliveryNonceError(f"delivery nonce {label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaReceiverDeliveryNonceError(f"delivery nonce {label} is invalid")
    return value


def _require_site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        raise ObjectDeltaReceiverDeliveryNonceError(f"delivery nonce {label} is invalid")
    return value


def _utc_whole_seconds(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObjectDeltaReceiverDeliveryNonceError(f"delivery nonce {label} is invalid")
    normalized = value.astimezone(timezone.utc)
    if normalized.microsecond != 0:
        raise ObjectDeltaReceiverDeliveryNonceError(
            f"delivery nonce {label} must use whole seconds"
        )
    return normalized


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObjectDeltaReceiverDeliveryNonceError(f"delivery nonce {label} is invalid")
    return value.astimezone(timezone.utc)


def _packet_claim(packet: VerifiedObjectDeltaDeliveryControlPacket) -> dict[str, Any]:
    """Normalize every verified packet value that can affect an import."""

    try:
        packet = revalidate_verified_object_delta_delivery_control_packet(packet)
    except Exception as exc:
        raise ObjectDeltaReceiverDeliveryNonceError("verified delivery packet is required") from exc
    issued_at = _utc_whole_seconds(packet.issued_at, label="issued_at")
    expires_at = _utc_whole_seconds(packet.expires_at, label="expires_at")
    if expires_at <= issued_at or expires_at - issued_at > MAX_CONTROL_PACKET_TTL:
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce expiry window is invalid")
    source_site = _require_site(packet.source_site, label="source site")
    destination_site = _require_site(packet.destination_site, label="destination site")
    if source_site == destination_site:
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce route is invalid")
    return {
        "schema": RECEIVER_DELIVERY_NONCE_CLAIM_SCHEMA,
        "controller_key_id": _require_text(packet.controller_key_id, label="controller key id", pattern=_KEY_ID_RE),
        "nonce": _require_text(packet.nonce, label="packet nonce", pattern=_NONCE_RE),
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bucket": _require_text(packet.bucket, label="bucket", pattern=BUCKET_RE),
        "source_site": source_site,
        "destination_site": destination_site,
        "destination_age_recipient": _require_text(
            packet.destination_age_recipient,
            label="destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        ),
        "campaign_id": _require_text(packet.campaign_id, label="campaign", pattern=CAMPAIGN_ID_RE),
        "release_sha": _require_text(packet.release_sha, label="release", pattern=RELEASE_SHA_RE),
        "writer_epoch": _require_positive_int(packet.writer_epoch, label="writer epoch"),
        "writer_lease_id": _require_text(packet.writer_lease_id, label="writer lease", pattern=LEASE_ID_RE),
        "stream_generation_id": _require_text(
            packet.stream_generation_id,
            label="stream generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        "first_sequence": _require_positive_int(packet.first_sequence, label="first sequence"),
        "last_sequence": _require_positive_int(packet.last_sequence, label="last sequence"),
        "prior_chain_sha256": _require_text(packet.prior_chain_sha256, label="prior chain SHA-256", pattern=SHA256_RE),
        "batch_sha256": _require_text(packet.batch_sha256, label="batch SHA-256", pattern=SHA256_RE),
        "payload_sha256": _require_text(packet.payload_sha256, label="payload SHA-256", pattern=SHA256_RE),
        "object_key": _require_text(packet.object_key, label="Object key", pattern=OBJECT_KEY_RE),
        "object_version_id": _require_text(packet.object_version_id, label="Object version", pattern=VERSION_ID_RE),
        "ciphertext_sha256": _require_text(packet.ciphertext_sha256, label="ciphertext SHA-256", pattern=SHA256_RE),
        "ciphertext_bytes": _require_positive_int(
            packet.ciphertext_bytes,
            label="ciphertext bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES + 1024 * 1024,
        ),
    }


def _assert_packet_matches_batch(
    packet: VerifiedObjectDeltaDeliveryControlPacket,
    batch: AppendOnlySyncDeltaBatch,
) -> None:
    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise ObjectDeltaReceiverDeliveryNonceError("validated Object-delta batch is required")
    expected = (
        batch.source_site,
        batch.destination_site,
        batch.campaign_id,
        batch.release_sha,
        batch.writer_term.epoch,
        batch.writer_term.lease_id,
        batch.stream.generation_id,
        batch.stream.first_sequence,
        batch.stream.last_sequence,
        batch.prior_chain_sha256,
        batch.batch_sha256,
        batch.payload_sha256,
        batch.immutable_receipt.object_key,
        batch.immutable_receipt.version_id,
        batch.immutable_receipt.ciphertext_sha256,
        batch.immutable_receipt.ciphertext_bytes,
    )
    actual = (
        packet.source_site,
        packet.destination_site,
        packet.campaign_id,
        packet.release_sha,
        packet.writer_epoch,
        packet.writer_lease_id,
        packet.stream_generation_id,
        packet.first_sequence,
        packet.last_sequence,
        packet.prior_chain_sha256,
        packet.batch_sha256,
        packet.payload_sha256,
        packet.object_key,
        packet.object_version_id,
        packet.ciphertext_sha256,
        packet.ciphertext_bytes,
    )
    if actual != expected:
        raise ObjectDeltaReceiverDeliveryNonceError(
            "verified delivery packet does not match the validated batch"
        )


def expected_object_delta_receiver_delivery_nonce_receipt(
    *,
    packet: VerifiedObjectDeltaDeliveryControlPacket,
    batch: AppendOnlySyncDeltaBatch,
    observed_at: datetime,
) -> ObjectDeltaReceiverDeliveryNonceReceipt:
    """Build the receipt that must share the import receipt/cursor transaction."""

    claim = _packet_claim(packet)
    observed = _utc(observed_at, label="observed_at")
    issued_at = _utc_whole_seconds(packet.issued_at, label="issued_at")
    expires_at = _utc_whole_seconds(packet.expires_at, label="expires_at")
    if observed < issued_at or observed >= expires_at:
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce packet is not currently valid")
    _assert_packet_matches_batch(packet, batch)
    return ObjectDeltaReceiverDeliveryNonceReceipt(
        controller_key_id=claim["controller_key_id"],
        nonce=claim["nonce"],
        packet_claim_sha256=hashlib.sha256(_canonical_json_bytes(claim)).hexdigest(),
        bucket=claim["bucket"],
        source_site=claim["source_site"],
        destination_site=claim["destination_site"],
        destination_age_recipient=claim["destination_age_recipient"],
        campaign_id=claim["campaign_id"],
        release_sha=claim["release_sha"],
        stream_generation_id=claim["stream_generation_id"],
        writer_epoch=claim["writer_epoch"],
        writer_lease_id=claim["writer_lease_id"],
        first_sequence=claim["first_sequence"],
        last_sequence=claim["last_sequence"],
        batch_sha256=claim["batch_sha256"],
        object_key=claim["object_key"],
        object_version_id=claim["object_version_id"],
        expires_at=_utc_whole_seconds(packet.expires_at, label="expires_at"),
    )


def validate_object_delta_receiver_delivery_nonce_receipt(
    value: object,
) -> ObjectDeltaReceiverDeliveryNonceReceipt:
    """Normalize a persisted nonce receipt before a transaction consumes it.

    The claim digest is intentionally opaque at this layer: only
    :func:`expected_object_delta_receiver_delivery_nonce_receipt` can derive
    it from a verified control packet and decoded batch.  Persistence code
    uses this validator to reject malformed ORM rows and manually assembled
    values before issuing locks or SQL.
    """

    if not isinstance(value, ObjectDeltaReceiverDeliveryNonceReceipt):
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce receipt is invalid")
    source_site = _require_site(value.source_site, label="source site")
    destination_site = _require_site(value.destination_site, label="destination site")
    if source_site == destination_site:
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce route is invalid")
    first_sequence = _require_positive_int(value.first_sequence, label="first sequence")
    last_sequence = _require_positive_int(value.last_sequence, label="last sequence")
    if last_sequence < first_sequence:
        raise ObjectDeltaReceiverDeliveryNonceError("delivery nonce sequence range is invalid")
    return ObjectDeltaReceiverDeliveryNonceReceipt(
        controller_key_id=_require_text(
            value.controller_key_id,
            label="controller key id",
            pattern=_KEY_ID_RE,
        ),
        nonce=_require_text(value.nonce, label="packet nonce", pattern=_NONCE_RE),
        packet_claim_sha256=_require_text(
            value.packet_claim_sha256,
            label="packet claim SHA-256",
            pattern=SHA256_RE,
        ),
        bucket=_require_text(value.bucket, label="bucket", pattern=BUCKET_RE),
        source_site=source_site,
        destination_site=destination_site,
        destination_age_recipient=_require_text(
            value.destination_age_recipient,
            label="destination age recipient",
            pattern=AGE_RECIPIENT_RE,
        ),
        campaign_id=_require_text(value.campaign_id, label="campaign", pattern=CAMPAIGN_ID_RE),
        release_sha=_require_text(value.release_sha, label="release", pattern=RELEASE_SHA_RE),
        stream_generation_id=_require_text(
            value.stream_generation_id,
            label="stream generation",
            pattern=STREAM_GENERATION_ID_RE,
        ),
        writer_epoch=_require_positive_int(value.writer_epoch, label="writer epoch"),
        writer_lease_id=_require_text(
            value.writer_lease_id,
            label="writer lease",
            pattern=LEASE_ID_RE,
        ),
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        batch_sha256=_require_text(value.batch_sha256, label="batch SHA-256", pattern=SHA256_RE),
        object_key=_require_text(value.object_key, label="Object key", pattern=OBJECT_KEY_RE),
        object_version_id=_require_text(
            value.object_version_id,
            label="Object version",
            pattern=VERSION_ID_RE,
        ),
        expires_at=_utc_whole_seconds(value.expires_at, label="expires_at"),
    )


def plan_object_delta_receiver_delivery_nonce_consumption(
    *,
    expected: ObjectDeltaReceiverDeliveryNonceReceipt,
    existing: ObjectDeltaReceiverDeliveryNonceReceipt | None,
) -> ObjectDeltaReceiverDeliveryNoncePlan:
    """Choose one immutable insert or an exact nonce replay.

    The caller must look up ``existing`` under the packet nonce's database
    uniqueness key inside the same transaction that owns the receiver cursor
    and import receipt.  A different signed packet reusing a nonce never
    becomes an implicit replay.
    """

    expected = validate_object_delta_receiver_delivery_nonce_receipt(expected)
    if existing is None:
        return ObjectDeltaReceiverDeliveryNoncePlan(
            action=RECEIVER_DELIVERY_NONCE_ACTION_CONSUME,
            receipt_to_insert=expected,
        )
    existing = validate_object_delta_receiver_delivery_nonce_receipt(existing)
    if existing != expected:
        raise ObjectDeltaReceiverDeliveryNonceError(
            "existing delivery nonce receipt conflicts with the packet"
        )
    return ObjectDeltaReceiverDeliveryNoncePlan(
        action=RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
        receipt_to_insert=None,
    )
