"""Versioned immutable event envelope for three-site replication and relay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from core.config import settings
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import PHYSICAL_SITES, SITE_BOT_FI, SITE_WEBAPP_FI, SITE_WEBAPP_IR
from core.sync_metadata import build_sync_metadata


DR_EVENT_PROTOCOL_VERSION = 2
DR_EVENT_SCHEMA_VERSION = 1
EVENT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
V1_ENVELOPE_FIELDS = frozenset(
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
TRANSACTION_FIELDS = frozenset(
    {
        "transaction_id", "transaction_position", "transaction_size", "transaction_hash",
        "destination_streams",
    }
)
ENVELOPE_FIELDS = V1_ENVELOPE_FIELDS | TRANSACTION_FIELDS


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

    def destination_stream(self, destination_site: str) -> dict[str, Any]:
        if int(self.payload["protocol_version"]) == 1:
            return {
                "sequence": self.producer_sequence,
                "transaction_id": self.event_id,
                "transaction_position": 1,
                "transaction_size": 1,
                "transaction_hash": self.envelope_hash,
            }
        stream = self.payload["destination_streams"].get(destination_site)
        if not isinstance(stream, dict):
            raise DrEventProtocolError("DR event is not authorized for this destination")
        return dict(stream)


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
    if not isinstance(payload, dict):
        raise DrEventProtocolError("DR envelope must be an object")
    protocol_version = payload.get("protocol_version")
    expected_fields = V1_ENVELOPE_FIELDS if protocol_version == 1 else ENVELOPE_FIELDS
    if set(payload) != expected_fields:
        raise DrEventProtocolError("DR envelope fields do not match its protocol version")
    if type(protocol_version) is not int or protocol_version not in {1, 2}:
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
    if protocol_version == 2:
        try:
            transaction_id = str(UUID(str(payload.get("transaction_id"))))
        except ValueError as exc:
            raise DrEventProtocolError("DR transaction_id must be a UUID") from exc
        if transaction_id != payload.get("transaction_id"):
            raise DrEventProtocolError("DR transaction_id is not canonical")
        position = payload.get("transaction_position")
        size = payload.get("transaction_size")
        if type(position) is not int or type(size) is not int or position < 1 or size < 1 or position > size:
            raise DrEventProtocolError("DR transaction position/size is invalid")
        if not HASH_RE.fullmatch(str(payload.get("transaction_hash") or "")):
            raise DrEventProtocolError("DR transaction hash is invalid")
        streams = payload.get("destination_streams")
        if not isinstance(streams, dict) or not streams:
            raise DrEventProtocolError("DR destination streams are missing")
        expected_stream_fields = {
            "sequence", "transaction_id", "transaction_position",
            "transaction_size", "transaction_hash",
        }
        for destination, stream in streams.items():
            if (
                destination not in PHYSICAL_SITES
                or destination == payload.get("origin_physical_site")
                or not isinstance(stream, dict)
                or set(stream) != expected_stream_fields
            ):
                raise DrEventProtocolError("DR destination stream identity is invalid")
            if (
                type(stream.get("sequence")) is not int
                or stream["sequence"] < 1
                or stream.get("transaction_id") != transaction_id
                or type(stream.get("transaction_position")) is not int
                or type(stream.get("transaction_size")) is not int
                or stream["transaction_position"] < 1
                or stream["transaction_size"] < 1
                or stream["transaction_position"] > stream["transaction_size"]
                or not HASH_RE.fullmatch(str(stream.get("transaction_hash") or ""))
                or stream["transaction_hash"] == "0" * 64
            ):
                raise DrEventProtocolError("DR destination stream transaction is invalid")
    created_at = payload.get("created_at")
    try:
        parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrEventProtocolError("DR created_at is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DrEventProtocolError("DR created_at must include timezone")
    return ValidatedDrEnvelope(payload=json.loads(canonical_json_bytes(payload)), envelope_hash=envelope_hash(payload))


def transaction_hash_from_envelopes(envelopes: list[dict[str, Any]]) -> str:
    """Hash the ordered immutable members without circular envelope hashes."""

    ordered = sorted(envelopes, key=lambda item: int(item["transaction_position"]))
    members = [
        {
            "event_id": item["event_id"],
            "producer_sequence": item["producer_sequence"],
            "transaction_position": item["transaction_position"],
            "aggregate_type": item["aggregate_type"],
            "aggregate_id": item["aggregate_id"],
            "aggregate_db_id": item["aggregate_db_id"],
            "aggregate_version": item["aggregate_version"],
            "operation": item["operation"],
            "canonical_payload_hash": item["canonical_payload_hash"],
            "schema_version": item["schema_version"],
            "writer_epoch": item["writer_epoch"],
            "tombstone": item["tombstone"],
        }
        for item in ordered
    ]
    return sha256_json(members)


def destination_transaction_hash(
    envelopes: list[dict[str, Any]], *, destination_site: str
) -> str:
    """Hash only members authorized for one destination in local stream order."""

    visible: list[dict[str, Any]] = []
    for envelope in envelopes:
        stream = (envelope.get("destination_streams") or {}).get(destination_site)
        if not isinstance(stream, dict):
            continue
        member = dict(envelope)
        member["transaction_position"] = int(stream["transaction_position"])
        visible.append(member)
    if not visible:
        raise DrEventProtocolError("destination transaction has no visible members")
    return transaction_hash_from_envelopes(visible)


def decide_receipt(
    *,
    contiguous_sequence: int,
    incoming: ValidatedDrEnvelope,
    existing_event_hash: str | None = None,
    existing_sequence_hash: str | None = None,
    incoming_sequence: int | None = None,
) -> ReceiptDecision:
    sequence = incoming.producer_sequence if incoming_sequence is None else int(incoming_sequence)
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


def ultimate_delivery_destinations(
    origin_site: str, *, aggregate_type: str
) -> tuple[str, ...]:
    """All sites entitled to the event, independent of the sparse relay path."""

    from core.dr_data_policy import WEBAPP_DR_REPLICA_TABLES

    if aggregate_type in WEBAPP_DR_REPLICA_TABLES:
        if origin_site == SITE_WEBAPP_FI:
            return (SITE_WEBAPP_IR,)
        if origin_site == SITE_WEBAPP_IR:
            return (SITE_WEBAPP_FI,)
        return ()
    ordered_sites = (SITE_BOT_FI, SITE_WEBAPP_FI, SITE_WEBAPP_IR)
    if origin_site not in ordered_sites:
        raise DrEventProtocolError("origin site is outside the fixed topology")
    return tuple(site for site in ordered_sites if site != origin_site)


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


def _next_destination_sequence(
    connection, *, authority: str, site: str, epoch: int, destination_site: str
) -> int:
    row = connection.execute(
        text(
            """
            INSERT INTO dr_destination_cursors (
                origin_authority, origin_physical_site, producer_epoch,
                destination_site, last_sequence
            ) VALUES (:authority, :site, :epoch, :destination_site, 1)
            ON CONFLICT (
                origin_authority, origin_physical_site, producer_epoch, destination_site
            ) DO UPDATE SET
                last_sequence = dr_destination_cursors.last_sequence + 1,
                updated_at = clock_timestamp()
            RETURNING last_sequence
            """
        ),
        {
            "authority": authority,
            "site": site,
            "epoch": epoch,
            "destination_site": destination_site,
        },
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
    transaction_id: str,
    transaction_position: int,
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
    # PostgreSQL independently reconstructs the complete envelope at commit.
    # Fixed precision keeps both canonical timestamp encodings byte-identical.
    now = datetime.now(timezone.utc)
    canonical_created_at = now.isoformat(timespec="microseconds")
    event_id = str(uuid4())
    payload = json.loads(canonical_json_bytes(data))
    destination_streams = {
        destination: {
            "sequence": _next_destination_sequence(
                connection,
                authority=identity.logical_authority,
                site=identity.physical_site,
                epoch=producer_epoch,
                destination_site=destination,
            ),
            "transaction_id": transaction_id,
            "transaction_position": 0,
            "transaction_size": 0,
            "transaction_hash": "0" * 64,
        }
        for destination in ultimate_delivery_destinations(
            identity.physical_site, aggregate_type=table_name
        )
    }
    if not destination_streams:
        raise DrEventProtocolError("DR business event has no authorized destination")
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
        "created_at": canonical_created_at,
        "transaction_id": transaction_id,
        "transaction_position": int(transaction_position),
        # Finalized after the Session flush has exposed every mapper event.
        "transaction_size": 0,
        "transaction_hash": "0" * 64,
        "destination_streams": destination_streams,
    }
    connection.execute(
        text(
            """
            INSERT INTO dr_events (
                event_id, protocol_version, origin_authority, origin_physical_site,
                producer_epoch, producer_sequence, aggregate_type, aggregate_id,
                aggregate_db_id, aggregate_version, operation, canonical_payload,
                canonical_payload_hash, envelope_hash, schema_version, causation_id,
                idempotency_key, writer_epoch, tombstone, created_at,
                transaction_id, transaction_position, transaction_size, transaction_hash,
                destination_streams, source_xid
            ) VALUES (
                :event_id, :protocol_version, :origin_authority, :origin_physical_site,
                :producer_epoch, :producer_sequence, :aggregate_type, :aggregate_id,
                :aggregate_db_id, :aggregate_version, :operation, CAST(:canonical_payload AS JSONB),
                :canonical_payload_hash, :envelope_hash, :schema_version, :causation_id,
                :idempotency_key, :writer_epoch, :tombstone, CAST(:created_at AS TIMESTAMPTZ),
                :transaction_id, :transaction_position, 0, :transaction_hash,
                CAST(:destination_streams AS JSONB), txid_current()
            )
            """
        ),
        {
            **{key: value for key, value in envelope.items() if key != "canonical_payload"},
            "canonical_payload": canonical_json_bytes(payload).decode("utf-8"),
            "destination_streams": canonical_json_bytes(destination_streams).decode("utf-8"),
            "envelope_hash": "0" * 64,
            # asyncpg requires a native datetime for TIMESTAMPTZ binds.  The
            # canonical envelope above retains the byte-stable ISO rendering.
            "created_at": now,
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


def finalize_local_dr_transaction(connection, event_ids: list[str]) -> str:
    if not event_ids:
        raise DrEventProtocolError("cannot finalize an empty DR transaction")
    rows = connection.execute(
        text(
            "SELECT * FROM dr_events WHERE event_id = ANY(:event_ids) "
            "ORDER BY transaction_position FOR UPDATE"
        ),
        {"event_ids": event_ids},
    ).mappings().all()
    if len(rows) != len(event_ids):
        raise DrEventProtocolError("DR transaction finalization lost an event")
    transaction_ids = {row["transaction_id"] for row in rows}
    positions = [int(row["transaction_position"] or 0) for row in rows]
    if len(transaction_ids) != 1 or positions != list(range(1, len(rows) + 1)):
        raise DrEventProtocolError("DR transaction members are not contiguous")
    provisional: list[dict[str, Any]] = []
    size = len(rows)
    for row in rows:
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.astimezone(timezone.utc).isoformat()
        provisional.append(
            {
                key: row[key]
                for key in V1_ENVELOPE_FIELDS
                if key != "created_at"
            }
            | {
                "created_at": created_at,
                "transaction_id": row["transaction_id"],
                "transaction_position": int(row["transaction_position"]),
                "transaction_size": size,
                "transaction_hash": "0" * 64,
                "destination_streams": dict(row["destination_streams"] or {}),
            }
        )
    group_hash = transaction_hash_from_envelopes(provisional)
    destinations = sorted(
        {
            destination
            for envelope in provisional
            for destination in envelope["destination_streams"]
        }
    )
    for destination in destinations:
        members = [
            envelope
            for envelope in provisional
            if destination in envelope["destination_streams"]
        ]
        for position, envelope in enumerate(members, 1):
            stream = dict(envelope["destination_streams"][destination])
            stream.update(
                transaction_position=position,
                transaction_size=len(members),
            )
            envelope["destination_streams"][destination] = stream
        destination_hash = destination_transaction_hash(
            members, destination_site=destination
        )
        for envelope in members:
            envelope["destination_streams"][destination]["transaction_hash"] = (
                destination_hash
            )
    for envelope in provisional:
        envelope["transaction_hash"] = group_hash
        validated = validate_envelope(envelope)
        connection.execute(
            text(
                "UPDATE dr_events SET transaction_size=:size, transaction_hash=:transaction_hash, "
                "destination_streams=CAST(:destination_streams AS JSONB), "
                "envelope_hash=:envelope_hash WHERE event_id=:event_id"
            ),
            {
                "size": size,
                "transaction_hash": group_hash,
                "destination_streams": canonical_json_bytes(
                    envelope["destination_streams"]
                ).decode("utf-8"),
                "envelope_hash": validated.envelope_hash,
                "event_id": envelope["event_id"],
            },
        )
    return group_hash
