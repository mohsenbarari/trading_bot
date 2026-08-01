"""Pure validation contract for Object-Storage append-only sync-delta payloads.

This module deliberately validates only canonical ``db_change`` evidence.  It
does not open files, access a database, call Object Storage, authenticate an
HTTP request, or apply a model mutation.  A future producer/importer must
still provide transactional stream cursors and durable receipts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import re
from typing import Any

from core.append_only_sync_delta_batch import (
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    canonical_json_bytes,
)
from core.sync_authority import IRAN_AUTHORITATIVE_SYNC_TABLES
from core.sync_field_policy import sanitize_sync_payload
from core.sync_metadata import build_sync_metadata, build_sync_public_identity
from core.sync_protocol import (
    SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    SYNC_PAYLOAD_SCHEMA_VERSION,
    SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    SYNC_PROTOCOL_VERSION,
    SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    SYNC_REGISTRY_VERSION,
)


OBJECT_DELTA_PAYLOAD_SCHEMA = "gold-trade-object-storage-append-only-sync-delta-payload-v1"
MAX_STREAM_GENERATION_ID_BYTES = 128
MAX_CHANGE_ITEM_HASH_BYTES = 64
KNOWN_SOURCE_SERVERS = frozenset({"foreign", "iran"})
SAFE_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE"})
STREAM_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REGISTRY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")

# This is a pure snapshot of the enabled ``SyncPolicy.SYNC`` registry.  Do not
# import ``core.sync_registry`` here: its current import graph constructs
# runtime settings, which would make this local contract depend on environment
# configuration.  The exact protocol registry fingerprint remains mandatory
# evidence and a future adapter must bind it to its installed release.
OBJECT_DELTA_SYNC_TABLES = frozenset(
    {
        "accountant_relations",
        "admin_broadcast_messages",
        "admin_market_messages",
        "commodities",
        "commodity_aliases",
        "customer_relations",
        "invitations",
        "market_runtime_state",
        "market_schedule_overrides",
        "notifications",
        "offer_publication_states",
        "offer_requests",
        "offers",
        "trades",
        "trade_delivery_receipts",
        "telegram_link_tokens",
        "telegram_admin_broadcasts",
        "telegram_admin_broadcast_receipts",
        "telegram_notification_outbox",
        "trading_settings",
        "user_blocks",
        "user_notification_preferences",
        "users",
    }
)

PAYLOAD_FIELDS = frozenset({"schema", "stream_generation_id", "items"})
SYNC_PROTOCOL_FIELDS = frozenset(
    {
        "protocol_version",
        "min_consumer_protocol_version",
        "payload_schema_version",
        "min_consumer_payload_schema_version",
        "registry_version",
        "min_consumer_registry_version",
        "registry_fingerprint",
        "producer",
    }
)
SYNC_PROTOCOL_PRODUCER_FIELDS = frozenset({"server_mode"})
SYNC_META_FIELDS = frozenset(
    {
        "aggregate_table",
        "aggregate_id",
        "aggregate_db_id",
        "source_server",
        "source_sequence",
        "authority_server",
        "operation",
        "authoritative_version",
        "event_sequence",
        "outbox_id",
        "command_idempotency_id",
    }
)
REQUIRED_ITEM_FIELDS = frozenset(
    {
        "logical_sequence",
        "type",
        "operation",
        "table",
        "id",
        "data",
        "hash",
        "timestamp",
        "change_log_id",
        "sync_protocol",
        "sync_meta",
    }
)
OPTIONAL_ITEM_FIELDS = frozenset({"public_identity"})


class ObjectDeltaPayloadError(ValueError):
    """Raised when a future Object-delta payload is malformed or unsafe."""


@dataclass(frozen=True)
class NormalizedObjectDeltaItem:
    """One pure, policy-checked source event with its logical stream position."""

    logical_sequence: int
    change_log_id: int
    item: dict[str, Any]

    def as_sync_item(self) -> dict[str, Any]:
        """Return an isolated legacy-envelope-compatible copy without sequencing."""

        value = {key: item for key, item in self.item.items() if key != "logical_sequence"}
        return json.loads(
            canonical_json_bytes(value).decode("ascii"),
            object_pairs_hook=_strict_object,
        )


@dataclass(frozen=True)
class NormalizedObjectDeltaPayload:
    """Validated payload metadata only; this object neither transports nor imports."""

    stream_generation_id: str
    items: tuple[NormalizedObjectDeltaItem, ...]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaPayloadError("object delta payload contains duplicate JSON fields")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaPayloadError(f"object delta payload JSON constant is forbidden: {value}")


def _require_mapping(value: object, *, label: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaPayloadError(f"{label} fields are invalid")
    return dict(value)


def _require_item_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ObjectDeltaPayloadError("object delta item is invalid")
    fields = set(value)
    if (
        not REQUIRED_ITEM_FIELDS.issubset(fields)
        or fields - REQUIRED_ITEM_FIELDS - OPTIONAL_ITEM_FIELDS
    ):
        raise ObjectDeltaPayloadError("object delta item fields are invalid")
    return dict(value)


def _require_positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _require_stream_generation_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or STREAM_GENERATION_ID_RE.fullmatch(value) is None:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ObjectDeltaPayloadError(f"{label} is invalid") from exc
    if len(encoded) > MAX_STREAM_GENERATION_ID_BYTES:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _require_source_server(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in KNOWN_SOURCE_SERVERS:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ObjectDeltaPayloadError(f"{label} is invalid") from exc
    if len(encoded) != MAX_CHANGE_ITEM_HASH_BYTES:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    if SHA256_RE.fullmatch(value) is None:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _canonical_json_value(value: object, *, label: str) -> Any:
    """Round-trip a value through canonical JSON without accepting non-JSON data."""

    try:
        encoded = canonical_json_bytes(value)
        return json.loads(encoded.decode("ascii"), object_pairs_hook=_strict_object)
    except (
        ObjectDeltaPayloadError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        raise ObjectDeltaPayloadError(f"{label} is not canonical JSON") from exc


def _require_timestamp(value: object) -> int | float:
    if type(value) not in {int, float}:
        raise ObjectDeltaPayloadError("object delta item timestamp is invalid")
    if not math.isfinite(value) or value <= 0:
        raise ObjectDeltaPayloadError("object delta item timestamp is invalid")
    return value


def _require_record_id(table: str, value: object, data: dict[str, Any]) -> int:
    if table == "trading_settings":
        if type(value) is not int or value != 0:
            raise ObjectDeltaPayloadError("trading_settings record identity is invalid")
        key = data.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ObjectDeltaPayloadError("trading_settings key identity is invalid")
        return value
    record_id = _require_positive_int(value, label="object delta item record identity")
    data_id = data.get("id")
    if data_id is not None and (type(data_id) is not int or data_id != record_id):
        raise ObjectDeltaPayloadError("object delta item record identity does not match data")
    return record_id


def _validate_table(table: object, *, source_server: str) -> str:
    if not isinstance(table, str) or table not in OBJECT_DELTA_SYNC_TABLES:
        raise ObjectDeltaPayloadError("object delta item table is not enabled for sync")
    if table in IRAN_AUTHORITATIVE_SYNC_TABLES and source_server != "iran":
        raise ObjectDeltaPayloadError("object delta item source lacks table authority")
    return table


def _require_exact_protocol_int(value: object, *, label: str, expected: int) -> int:
    if type(value) is not int or value != expected:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _require_registry_fingerprint(value: object, *, label: str) -> str:
    if not isinstance(value, str) or REGISTRY_FINGERPRINT_RE.fullmatch(value) is None:
        raise ObjectDeltaPayloadError(f"{label} is invalid")
    return value


def _validate_sync_protocol(
    value: object,
    *,
    source_server: str,
    expected_registry_fingerprint: str | None,
) -> dict[str, Any]:
    protocol = _require_mapping(
        value,
        label="object delta item sync_protocol",
        fields=SYNC_PROTOCOL_FIELDS,
    )
    producer = _require_mapping(
        protocol["producer"],
        label="object delta item sync_protocol producer",
        fields=SYNC_PROTOCOL_PRODUCER_FIELDS,
    )
    if producer["server_mode"] != source_server:
        raise ObjectDeltaPayloadError(
            "object delta item protocol source does not match sync metadata"
        )
    _require_exact_protocol_int(
        protocol["protocol_version"],
        label="object delta item protocol version",
        expected=SYNC_PROTOCOL_VERSION,
    )
    _require_exact_protocol_int(
        protocol["min_consumer_protocol_version"],
        label="object delta item minimum consumer protocol version",
        expected=SYNC_PROTOCOL_MIN_SUPPORTED_VERSION,
    )
    _require_exact_protocol_int(
        protocol["payload_schema_version"],
        label="object delta item payload schema version",
        expected=SYNC_PAYLOAD_SCHEMA_VERSION,
    )
    _require_exact_protocol_int(
        protocol["min_consumer_payload_schema_version"],
        label="object delta item minimum consumer payload schema version",
        expected=SYNC_PAYLOAD_SCHEMA_MIN_SUPPORTED_VERSION,
    )
    _require_exact_protocol_int(
        protocol["registry_version"],
        label="object delta item registry version",
        expected=SYNC_REGISTRY_VERSION,
    )
    _require_exact_protocol_int(
        protocol["min_consumer_registry_version"],
        label="object delta item minimum consumer registry version",
        expected=SYNC_REGISTRY_MIN_SUPPORTED_VERSION,
    )
    fingerprint = _require_registry_fingerprint(
        protocol["registry_fingerprint"], label="object delta item registry fingerprint"
    )
    if expected_registry_fingerprint is not None and fingerprint != expected_registry_fingerprint:
        raise ObjectDeltaPayloadError(
            "object delta item registry fingerprint does not match the expected release"
        )
    return _canonical_json_value(protocol, label="object delta item sync_protocol")


def _validate_sync_metadata(
    value: object,
    *,
    table: str,
    operation: str,
    record_id: int,
    data: dict[str, Any],
    change_log_id: int,
    source_server: str,
) -> dict[str, Any]:
    sync_meta = _require_mapping(
        value,
        label="object delta item sync_meta",
        fields=SYNC_META_FIELDS,
    )
    expected = build_sync_metadata(
        table,
        record_id,
        operation,
        data,
        change_log_id=change_log_id,
        source_server=source_server,
    )
    if sync_meta != expected:
        raise ObjectDeltaPayloadError(
            "object delta item sync metadata does not bind the source evidence"
        )
    return _canonical_json_value(sync_meta, label="object delta item sync_meta")


def _validate_public_identity(
    value: object,
    *,
    table: str,
    record_id: int,
    data: dict[str, Any],
    item_has_public_identity: bool,
) -> dict[str, Any] | None:
    expected = build_sync_public_identity(table, record_id, data)
    if expected is None:
        if item_has_public_identity:
            raise ObjectDeltaPayloadError("object delta item has an unexpected public identity")
        return None
    if value != expected:
        raise ObjectDeltaPayloadError("object delta item public identity does not match its data")
    return _canonical_json_value(expected, label="object delta item public_identity")


def _normalize_item(
    value: object,
    *,
    expected_source_server: str | None,
    expected_registry_fingerprint: str | None,
) -> NormalizedObjectDeltaItem:
    item = _require_item_mapping(value)
    logical_sequence = _require_positive_int(
        item["logical_sequence"], label="object delta logical sequence"
    )
    if item["type"] != "db_change":
        raise ObjectDeltaPayloadError("object delta item type must be db_change")
    operation = item["operation"]
    if not isinstance(operation, str) or operation not in SAFE_OPERATIONS:
        raise ObjectDeltaPayloadError("object delta item operation is invalid")
    change_log_id = _require_positive_int(
        item["change_log_id"], label="object delta ChangeLog evidence"
    )
    if not isinstance(item["data"], dict):
        raise ObjectDeltaPayloadError("object delta item data is invalid")
    data = _canonical_json_value(item["data"], label="object delta item data")
    if not isinstance(data, dict) or not data:
        raise ObjectDeltaPayloadError("object delta item data is invalid")
    source_server_value = _require_mapping(
        item["sync_meta"], label="object delta item sync_meta", fields=SYNC_META_FIELDS
    )["source_server"]
    source_server = _require_source_server(
        source_server_value, label="object delta item source server"
    )
    if expected_source_server is not None and source_server != expected_source_server:
        raise ObjectDeltaPayloadError("object delta item source does not match the expected source")
    table = _validate_table(item["table"], source_server=source_server)
    record_id = _require_record_id(table, item["id"], data)
    if table == "trading_settings" and operation == "DELETE":
        raise ObjectDeltaPayloadError(
            "trading_settings delete is not a supported object delta operation"
        )
    if sanitize_sync_payload(table, data) != data:
        raise ObjectDeltaPayloadError("object delta item data violates the sync field policy")
    protocol = _validate_sync_protocol(
        item["sync_protocol"],
        source_server=source_server,
        expected_registry_fingerprint=expected_registry_fingerprint,
    )
    sync_meta = _validate_sync_metadata(
        item["sync_meta"],
        table=table,
        operation=operation,
        record_id=record_id,
        data=data,
        change_log_id=change_log_id,
        source_server=source_server,
    )
    public_identity = _validate_public_identity(
        item.get("public_identity"),
        table=table,
        record_id=record_id,
        data=data,
        item_has_public_identity="public_identity" in item,
    )
    normalized = {
        "logical_sequence": logical_sequence,
        "type": "db_change",
        "operation": operation,
        "table": table,
        "id": record_id,
        "data": data,
        "hash": _require_sha256(item["hash"], label="object delta item ChangeLog hash"),
        "timestamp": _require_timestamp(item["timestamp"]),
        "change_log_id": change_log_id,
        "sync_protocol": protocol,
        "sync_meta": sync_meta,
    }
    if public_identity is not None:
        normalized["public_identity"] = public_identity
    return NormalizedObjectDeltaItem(
        logical_sequence=logical_sequence,
        change_log_id=change_log_id,
        item=normalized,
    )


def _validate_expected_sequence_ids(value: Sequence[int] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not value or len(value) > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaPayloadError("expected logical stream sequence IDs are invalid")
    normalized: list[int] = []
    for expected in value:
        normalized.append(
            _require_positive_int(expected, label="expected logical stream sequence ID")
        )
    if any(current != previous + 1 for previous, current in zip(normalized, normalized[1:])):
        raise ObjectDeltaPayloadError("expected logical stream sequence IDs are not contiguous")
    return tuple(normalized)


def normalize_object_delta_payload(
    value: object,
    *,
    expected_stream_generation_id: str | None = None,
    expected_stream_sequence_ids: Sequence[int] | None = None,
    expected_source_server: str | None = None,
    expected_registry_fingerprint: str | None = None,
) -> NormalizedObjectDeltaPayload:
    """Validate and normalize a canonical Object-delta payload without I/O.

    ``expected_stream_sequence_ids`` should come from the validated outer batch
    manifest.  Passing it binds each inner item to that exact logical stream;
    this function does not create or persist the stream cursor itself.
    """

    payload = _require_mapping(value, label="object delta payload", fields=PAYLOAD_FIELDS)
    if payload["schema"] != OBJECT_DELTA_PAYLOAD_SCHEMA:
        raise ObjectDeltaPayloadError("object delta payload schema is invalid")
    stream_generation_id = _require_stream_generation_id(
        payload["stream_generation_id"], label="object delta payload stream generation_id"
    )
    if expected_stream_generation_id is not None and _require_stream_generation_id(
        expected_stream_generation_id, label="expected object delta stream generation_id"
    ) != stream_generation_id:
        raise ObjectDeltaPayloadError(
            "object delta payload generation does not match the expected generation"
        )
    if expected_source_server is not None:
        expected_source_server = _require_source_server(
            expected_source_server, label="expected object delta source server"
        )
    if expected_registry_fingerprint is not None:
        expected_registry_fingerprint = _require_registry_fingerprint(
            expected_registry_fingerprint, label="expected object delta registry fingerprint"
        )
    raw_items = payload["items"]
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaPayloadError("object delta payload items are invalid")
    normalized_items: list[NormalizedObjectDeltaItem] = []
    previous_sequence = 0
    seen_change_log_ids: set[int] = set()
    payload_source_server = expected_source_server
    payload_registry_fingerprint = expected_registry_fingerprint
    for raw_item in raw_items:
        item = _normalize_item(
            raw_item,
            expected_source_server=payload_source_server,
            expected_registry_fingerprint=payload_registry_fingerprint,
        )
        if item.logical_sequence != previous_sequence + 1 and previous_sequence:
            raise ObjectDeltaPayloadError(
                "object delta logical sequence items are not contiguous and ordered"
            )
        if item.logical_sequence <= previous_sequence:
            raise ObjectDeltaPayloadError(
                "object delta logical sequence items are not strictly ordered"
            )
        if item.change_log_id in seen_change_log_ids:
            raise ObjectDeltaPayloadError("object delta ChangeLog evidence is not unique")
        previous_sequence = item.logical_sequence
        seen_change_log_ids.add(item.change_log_id)
        if payload_source_server is None:
            payload_source_server = item.item["sync_meta"]["source_server"]
        if payload_registry_fingerprint is None:
            payload_registry_fingerprint = item.item["sync_protocol"]["registry_fingerprint"]
        normalized_items.append(item)
    expected_sequences = _validate_expected_sequence_ids(expected_stream_sequence_ids)
    actual_sequences = tuple(item.logical_sequence for item in normalized_items)
    if expected_sequences is not None and actual_sequences != expected_sequences:
        raise ObjectDeltaPayloadError(
            "object delta logical sequence does not match the batch manifest"
        )
    return NormalizedObjectDeltaPayload(
        stream_generation_id=stream_generation_id,
        items=tuple(normalized_items),
    )


def parse_object_delta_payload(raw: bytes, **expected: object) -> NormalizedObjectDeltaPayload:
    """Parse one canonical payload only when all outer-batch bindings are known."""

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_DELTA_PAYLOAD_BYTES:
        raise ObjectDeltaPayloadError("object delta payload input has an unsafe size")
    required_bindings = (
        "expected_stream_generation_id",
        "expected_stream_sequence_ids",
        "expected_source_server",
        "expected_registry_fingerprint",
    )
    if any(expected.get(binding) is None for binding in required_bindings):
        raise ObjectDeltaPayloadError("object delta payload requires all outer-batch bindings")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ObjectDeltaPayloadError("object delta payload JSON is invalid") from exc
    if raw != canonical_json_bytes(value) + b"\n":
        raise ObjectDeltaPayloadError("object delta payload JSON is not canonical")
    return normalize_object_delta_payload(value, **expected)


def build_object_delta_payload(
    *,
    stream_generation_id: str,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a validated pure payload manifest from in-memory db-change items."""

    if isinstance(items, (str, bytes)):
        raise ObjectDeltaPayloadError("object delta payload items are invalid")
    value: dict[str, Any] = {
        "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
        "stream_generation_id": stream_generation_id,
        "items": [dict(item) for item in items],
    }
    normalize_object_delta_payload(value)
    return value
