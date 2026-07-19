"""Versioned immutable event envelope for three-site replication and relay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from core.config import settings
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import PHYSICAL_SITES, SITE_BOT_FI, SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.sync_metadata import build_sync_metadata


DR_EVENT_PROTOCOL_VERSION = 1
DR_EVENT_SCHEMA_VERSION = 1
EVENT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ENVELOPE_FIELDS = frozenset(
    {
        "protocol_version",
        "event_id",
        "origin_authority",
        "origin_physical_site",
        "producer_epoch",
        "producer_sequence",
        "aggregate_type",
        "aggregate_id",
        "aggregate_db_id",
        "aggregate_version",
        "operation",
        "canonical_payload",
        "canonical_payload_hash",
        "schema_version",
        "causation_id",
        "idempotency_key",
        "writer_epoch",
        "tombstone",
        "created_at",
    }
)


class DrEventProtocolError(RuntimeError):
    """Raised for malformed, conflicting, or unsafe DR event state."""


@dataclass(frozen=True)
class ValidatedDrEnvelope:
    payload: dict[str, Any]
    envelope_hash: str

    @property
    def event_id(self) -> str:
        return str(self.payload["event_id"])

    @property
    def origin_physical_site(self) -> str:
        return str(self.payload["origin_physical_site"])

    @property
    def producer_epoch(self) -> int:
        return int(self.payload["producer_epoch"])

    @property
    def producer_sequence(self) -> int:
        return int(self.payload["producer_sequence"])


@dataclass(frozen=True)
class ReceiptDecision:
    action: str
    missing_from: int | None = None
    missing_to: int | None = None
    reason: str | None = None


def canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DrEventProtocolError("DR payload is not canonical-JSON compatible") from exc


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def envelope_hash(payload: dict[str, Any]) -> str:
    return sha256_json(payload)


def validate_envelope(payload: Any) -> ValidatedDrEnvelope:
    if not isinstance(payload, dict) or set(payload) != ENVELOPE_FIELDS:
        raise DrEventProtocolError("DR envelope fields do not match protocol v1")
    if type(payload.get("protocol_version")) is not int or payload["protocol_version"] != 1:
        raise DrEventProtocolError("unsupported DR event protocol version")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise DrEventProtocolError("unsupported DR payload schema version")
    if not EVENT_ID_RE.fullmatch(str(payload.get("event_id") or "")):
        raise DrEventProtocolError("DR event_id must be a canonical UUID")
    if payload.get("origin_physical_site") not in PHYSICAL_SITES:
        raise DrEventProtocolError("DR origin physical site is invalid")
    if payload.get("origin_authority") not in {"foreign", "webapp"}:
        raise DrEventProtocolError("DR origin authority is invalid")
    expected_authority = (
        "foreign" if payload.get("origin_physical_site") == SITE_BOT_FI else "webapp"
    )
    if payload.get("origin_authority") != expected_authority:
        raise DrEventProtocolError("DR origin authority does not match its physical producer")
    for key in ("producer_epoch", "producer_sequence"):
        if type(payload.get(key)) is not int or payload[key] < 1:
            raise DrEventProtocolError(f"{key} must be a positive integer")
    if payload.get("operation") not in {"INSERT", "UPDATE", "DELETE"}:
        raise DrEventProtocolError("DR operation is invalid")
    if payload.get("tombstone") is not (payload.get("operation") == "DELETE"):
        raise DrEventProtocolError("DR tombstone flag must exactly match DELETE")
    if not str(payload.get("aggregate_type") or "") or not str(payload.get("aggregate_id") or ""):
        raise DrEventProtocolError("DR aggregate identity is missing")
    expected_payload_hash = sha256_json(payload.get("canonical_payload"))
    if payload.get("canonical_payload_hash") != expected_payload_hash:
        raise DrEventProtocolError("DR canonical payload hash mismatch")
    writer_epoch = payload.get("writer_epoch")
    if payload.get("origin_authority") == "webapp":
        if type(writer_epoch) is not int or writer_epoch < 1:
            raise DrEventProtocolError("WebApp DR event requires a positive writer_epoch")
        if writer_epoch != payload.get("producer_epoch"):
            raise DrEventProtocolError("WebApp producer_epoch must equal writer_epoch")
    elif writer_epoch is not None:
        raise DrEventProtocolError("foreign-authority DR event must not carry writer_epoch")
    created_at = payload.get("created_at")
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrEventProtocolError("DR created_at is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DrEventProtocolError("DR created_at must include timezone")
    return ValidatedDrEnvelope(payload=json.loads(canonical_json_bytes(payload)), envelope_hash=envelope_hash(payload))


def decide_receipt(
    *,
    contiguous_sequence: int,
    incoming: ValidatedDrEnvelope,
    existing_event_hash: str | None = None,
    existing_sequence_hash: str | None = None,
) -> ReceiptDecision:
    sequence = incoming.producer_sequence
    if existing_event_hash is not None:
        if existing_event_hash == incoming.envelope_hash:
            return ReceiptDecision("duplicate")
        return ReceiptDecision("quarantine", reason="same_event_id_different_hash")
    if existing_sequence_hash is not None and existing_sequence_hash != incoming.envelope_hash:
        return ReceiptDecision("quarantine", reason="same_sequence_different_hash")
    expected = int(contiguous_sequence) + 1
    if sequence < expected:
        return ReceiptDecision("quarantine", reason="sequence_rewind_without_receipt")
    if sequence > expected:
        return ReceiptDecision("blocked_gap", missing_from=expected, missing_to=sequence - 1)
    return ReceiptDecision("apply")


def transport_peers(local_site: str) -> tuple[str, ...]:
    """Return the deliberately sparse physical DR transport graph.

    WebApp-FI is the only recovery hub.  Keeping Bot-FI and WebApp-IR
    non-adjacent is an explicit trust- and DPI-boundary, not an omission.
    """

    if local_site == SITE_WEBAPP_FI:
        return (SITE_BOT_FI, SITE_WEBAPP_IR)
    if local_site == SITE_BOT_FI:
        return (SITE_WEBAPP_FI,)
    if local_site == SITE_WEBAPP_IR:
        return (SITE_WEBAPP_FI,)
    raise DrEventProtocolError("local site is outside the fixed topology")


def initial_delivery_destinations(
    origin_site: str, *, aggregate_type: str | None = None
) -> tuple[str, ...]:
    # WebApp-local Messenger/session payloads are replicated only between the
    # two WebApp sites. They are neither useful nor authorized at Bot-FI.
    if aggregate_type is not None:
        from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES

        if aggregate_type in WEBAPP_DR_REPLICA_TABLES:
            if origin_site == SITE_WEBAPP_FI:
                return (SITE_WEBAPP_IR,)
            if origin_site == SITE_WEBAPP_IR:
                return (SITE_WEBAPP_FI,)
            return ()
    return transport_peers(origin_site)


def relay_destinations(
    origin_site: str,
    local_site: str,
    *,
    aggregate_type: str | None = None,
) -> tuple[str, ...]:
    if origin_site not in PHYSICAL_SITES or local_site not in PHYSICAL_SITES:
        raise DrEventProtocolError("relay site is outside the fixed topology")
    if local_site != SITE_WEBAPP_FI:
        return ()
    if aggregate_type is not None:
        from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES

        if aggregate_type in WEBAPP_DR_REPLICA_TABLES:
            return ()
    if origin_site == SITE_BOT_FI:
        return (SITE_WEBAPP_IR,)
    if origin_site == SITE_WEBAPP_IR:
        return (SITE_BOT_FI,)
    return ()


def validate_transport_path(*, origin_site: str, sender_site: str, destination_site: str) -> None:
    """Reject invented, reflected, or non-hub relay paths."""

    if destination_site not in transport_peers(sender_site):
        raise DrEventProtocolError("DR sender/destination are not adjacent in the fixed topology")
    if sender_site == origin_site:
        return
    if sender_site != SITE_WEBAPP_FI:
        raise DrEventProtocolError("only WebApp-FI may relay an original event")
    if destination_site not in relay_destinations(origin_site, sender_site):
        raise DrEventProtocolError("DR relay path does not match the original producer")


def _next_producer_sequence(connection, *, authority: str, site: str, epoch: int) -> int:
    row = connection.execute(
        text(
            """
            INSERT INTO dr_producer_cursors (
                origin_authority, origin_physical_site, producer_epoch, last_sequence
            ) VALUES (:authority, :site, :epoch, 1)
            ON CONFLICT (origin_authority, origin_physical_site, producer_epoch)
            DO UPDATE SET last_sequence = dr_producer_cursors.last_sequence + 1,
                          updated_at = clock_timestamp()
            RETURNING last_sequence
            """
        ),
        {"authority": authority, "site": site, "epoch": epoch},
    ).scalar_one()
    return int(row)


def append_local_dr_event(
    connection,
    *,
    table_name: str,
    record_id: Any,
    operation: str,
    data: dict[str, Any],
    change_log_id: int | None,
) -> str | None:
    """Append business mutation plus immutable DR event in one transaction."""

    if not bool(getattr(settings, "dr_event_protocol_enabled", False)):
        return None
    identity = resolve_runtime_identity(settings)
    if identity.compatibility_inferred:
        raise DrEventProtocolError("DR event production requires explicit physical identity")
    from core.writer_fencing import current_writer_fence_context

    writer_context = current_writer_fence_context()
    if identity.is_webapp_authority:
        if writer_context is None or writer_context.physical_site != identity.physical_site:
            raise DrEventProtocolError("WebApp DR event lacks a writer-term capability")
        producer_epoch = int(writer_context.writer_epoch)
        writer_epoch: int | None = producer_epoch
    else:
        producer_epoch = int(getattr(settings, "dr_producer_epoch", 0))
        writer_epoch = None
    if producer_epoch < 1:
        raise DrEventProtocolError("DR producer epoch must be configured and positive")
    sequence = _next_producer_sequence(
        connection,
        authority=identity.logical_authority,
        site=identity.physical_site,
        epoch=producer_epoch,
    )
    metadata = build_sync_metadata(
        table_name,
        record_id,
        operation,
        data,
        change_log_id=change_log_id,
        source_server=identity.legacy_server_mode,
    )
    now = datetime.now(timezone.utc).isoformat()
    event_id = str(uuid4())
    payload = json.loads(canonical_json_bytes(data))
    envelope = {
        "protocol_version": DR_EVENT_PROTOCOL_VERSION,
        "event_id": event_id,
        "origin_authority": identity.logical_authority,
        "origin_physical_site": identity.physical_site,
        "producer_epoch": producer_epoch,
        "producer_sequence": sequence,
        "aggregate_type": table_name,
        "aggregate_id": str(metadata["aggregate_id"]),
        "aggregate_db_id": str(record_id) if record_id is not None else None,
        "aggregate_version": metadata.get("authoritative_version"),
        "operation": str(operation).upper(),
        "canonical_payload": payload,
        "canonical_payload_hash": sha256_json(payload),
        "schema_version": DR_EVENT_SCHEMA_VERSION,
        "causation_id": str(change_log_id) if change_log_id is not None else None,
        "idempotency_key": metadata.get("command_idempotency_id"),
        "writer_epoch": writer_epoch,
        "tombstone": str(operation).upper() == "DELETE",
        "created_at": now,
    }
    validated = validate_envelope(envelope)
    connection.execute(
        text(
            """
            INSERT INTO dr_events (
                event_id, protocol_version, origin_authority, origin_physical_site,
                producer_epoch, producer_sequence, aggregate_type, aggregate_id,
                aggregate_db_id, aggregate_version, operation, canonical_payload,
                canonical_payload_hash, envelope_hash, schema_version, causation_id,
                idempotency_key, writer_epoch, tombstone, created_at
            ) VALUES (
                :event_id, :protocol_version, :origin_authority, :origin_physical_site,
                :producer_epoch, :producer_sequence, :aggregate_type, :aggregate_id,
                :aggregate_db_id, :aggregate_version, :operation, CAST(:canonical_payload AS JSONB),
                :canonical_payload_hash, :envelope_hash, :schema_version, :causation_id,
                :idempotency_key, :writer_epoch, :tombstone, CAST(:created_at AS TIMESTAMPTZ)
            )
            """
        ),
        {
            **{key: value for key, value in envelope.items() if key != "canonical_payload"},
            "canonical_payload": canonical_json_bytes(payload).decode("utf-8"),
            "envelope_hash": validated.envelope_hash,
        },
    )
    for destination in initial_delivery_destinations(
        identity.physical_site, aggregate_type=table_name
    ):
        connection.execute(
            text(
                """
                INSERT INTO dr_event_deliveries (event_id, destination_site, status, attempt_count)
                VALUES (:event_id, :destination, 'pending', 0)
                """
            ),
            {"event_id": event_id, "destination": destination},
        )
    return event_id
