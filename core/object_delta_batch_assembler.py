"""Pure assembly of one homogeneous Object-delta payload from durable outbox rows.

This module deliberately stops before encryption, Object Storage, database
queries, source-ledger writes, or receiver import.  A future publisher loads a
locked contiguous outbox prefix, projects each row into
``SourceOutboxDeltaItem``, and calls :func:`assemble_object_delta_payload`.

One immutable batch has exactly one Writer Witness term.  A lease renewal is
therefore a batch boundary, not a field that can be silently mixed into a
payload.  The source ledger and eventual publisher can then bind the resulting
payload hash to one encrypted Object receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.append_only_sync_delta_batch import (
    LEASE_ID_RE,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    SHA256_RE,
    WriterTermBinding,
    canonical_json_bytes,
    sha256_bytes,
)
from core.append_only_sync_delta_payload import (
    OBJECT_DELTA_PAYLOAD_SCHEMA,
    ObjectDeltaPayloadError,
    REGISTRY_FINGERPRINT_RE,
    normalize_object_delta_payload,
    parse_object_delta_payload,
)
from core.object_delta_outbox_allocator import SOURCE_SERVER_BY_SITE
from core.object_delta_source_batch_ledger import SourceStreamIdentity


class ObjectDeltaBatchAssemblyError(ValueError):
    """Raised when a durable outbox prefix cannot safely become one batch."""


_PREPARED_OBJECT_DELTA_PAYLOAD_CAPABILITY = object()


@dataclass(frozen=True)
class SourceOutboxDeltaItem:
    """The immutable columns needed from one durable source outbox row."""

    logical_sequence: int
    change_log_id: int
    writer_epoch: int
    writer_lease_id: str
    canonical_sync_item: Mapping[str, Any]


@dataclass(frozen=True)
class PreparedObjectDeltaPayload:
    """Canonical plaintext ready for a later authenticated encrypt-and-upload step."""

    stream: SourceStreamIdentity
    writer_term: WriterTermBinding
    first_sequence: int
    last_sequence: int
    sequence_ids: tuple[int, ...]
    payload: bytes
    payload_sha256: str
    _capability: object | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    @property
    def payload_bytes(self) -> int:
        return len(self.payload)


def _require_prepared_object_delta_payload_provenance(
    prepared: object,
    *,
    expected_registry_fingerprint: str,
) -> PreparedObjectDeltaPayload:
    """Require assembler-minted canonical plaintext provenance for a strict gate.

    Existing low-level helpers intentionally continue to accept a plain
    ``PreparedObjectDeltaPayload`` after their own validation.  This private
    helper is narrower: a source pre-upload authorization boundary needs to
    reject a manually constructed, replaced, or subclassed dataclass before
    it can derive a deterministic Object key or reservation identity.

    The marker is process-local capability evidence, not durable authority.
    A future coordinator must still lock and validate the source cutover,
    outbox prefix, ledger frontier, and live Writer Witness term.
    """

    if type(prepared) is not PreparedObjectDeltaPayload:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload provenance is invalid")
    if prepared._capability is not _PREPARED_OBJECT_DELTA_PAYLOAD_CAPABILITY:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload has no assembler provenance")
    fingerprint = _require_registry_fingerprint(expected_registry_fingerprint)
    try:
        stream = SourceStreamIdentity(
            source_site=prepared.stream.source_site,
            destination_site=prepared.stream.destination_site,
            campaign_id=prepared.stream.campaign_id,
            release_sha=prepared.stream.release_sha,
            stream_generation_id=prepared.stream.stream_generation_id,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload stream is invalid") from exc
    if prepared.stream != stream:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload stream is not normalized")
    if type(prepared.writer_term) is not WriterTermBinding:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload Writer Witness term is invalid")
    if (
        type(prepared.writer_term.epoch) is not int
        or prepared.writer_term.epoch < 1
        or not isinstance(prepared.writer_term.lease_id, str)
        or LEASE_ID_RE.fullmatch(prepared.writer_term.lease_id) is None
    ):
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload Writer Witness term is invalid")
    if (
        type(prepared.first_sequence) is not int
        or type(prepared.last_sequence) is not int
        or prepared.first_sequence < 1
        or prepared.last_sequence < prepared.first_sequence
        or type(prepared.sequence_ids) is not tuple
        or not prepared.sequence_ids
        or len(prepared.sequence_ids) > MAX_STREAM_SEQUENCE_IDS
        or prepared.sequence_ids
        != tuple(range(prepared.first_sequence, prepared.last_sequence + 1))
        or any(type(sequence) is not int for sequence in prepared.sequence_ids)
    ):
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload sequence is invalid")
    if (
        not isinstance(prepared.payload, bytes)
        or not prepared.payload
        or len(prepared.payload) > MAX_DELTA_PAYLOAD_BYTES
        or not isinstance(prepared.payload_sha256, str)
        or SHA256_RE.fullmatch(prepared.payload_sha256) is None
        or sha256_bytes(prepared.payload) != prepared.payload_sha256
    ):
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload bytes are invalid")
    try:
        normalized = parse_object_delta_payload(
            prepared.payload + b"\n",
            expected_stream_generation_id=stream.stream_generation_id,
            expected_stream_sequence_ids=prepared.sequence_ids,
            expected_source_server=SOURCE_SERVER_BY_SITE[stream.source_site],
            expected_registry_fingerprint=fingerprint,
        )
    except (KeyError, ObjectDeltaPayloadError) as exc:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload is not canonical") from exc
    if tuple(item.logical_sequence for item in normalized.items) != prepared.sequence_ids:
        raise ObjectDeltaBatchAssemblyError("object-delta prepared payload sequence is invalid")
    return prepared


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaBatchAssemblyError(f"object-delta {label} is invalid")
    return value


def _require_registry_fingerprint(value: object) -> str:
    if not isinstance(value, str) or REGISTRY_FINGERPRINT_RE.fullmatch(value) is None:
        raise ObjectDeltaBatchAssemblyError("object-delta expected registry fingerprint is invalid")
    return value


def _validated_item(value: SourceOutboxDeltaItem) -> SourceOutboxDeltaItem:
    if not isinstance(value, SourceOutboxDeltaItem):
        raise ObjectDeltaBatchAssemblyError("object-delta source outbox item is invalid")
    sequence = _require_positive_int(
        value.logical_sequence,
        label="source outbox logical sequence",
    )
    change_log_id = _require_positive_int(
        value.change_log_id,
        label="source ChangeLog id",
    )
    writer_term = WriterTermBinding(
        epoch=_require_positive_int(value.writer_epoch, label="source writer epoch"),
        lease_id=value.writer_lease_id,
    )
    if not isinstance(value.canonical_sync_item, Mapping):
        raise ObjectDeltaBatchAssemblyError("object-delta source sync item is invalid")
    item = dict(value.canonical_sync_item)
    if "logical_sequence" in item:
        raise ObjectDeltaBatchAssemblyError("object-delta source sync item already has a logical sequence")
    return SourceOutboxDeltaItem(
        logical_sequence=sequence,
        change_log_id=change_log_id,
        writer_epoch=writer_term.epoch,
        writer_lease_id=writer_term.lease_id,
        canonical_sync_item=item,
    )


def assemble_object_delta_payload(
    *,
    stream: SourceStreamIdentity,
    outbox_items: Sequence[SourceOutboxDeltaItem],
    expected_registry_fingerprint: str,
    maximum_payload_bytes: int = MAX_DELTA_PAYLOAD_BYTES,
) -> PreparedObjectDeltaPayload:
    """Return one canonical payload from a contiguous, same-term outbox prefix.

    The caller must supply rows after its source stream lock and in ascending
    logical sequence.  No sorting or best-effort filtering happens here: a
    gap, duplicate, reordered row, mixed Writer term, or oversize payload
    fails before plaintext can reach an encryption command.
    """

    if not isinstance(stream, SourceStreamIdentity):
        raise ObjectDeltaBatchAssemblyError("object-delta source stream is invalid")
    fingerprint = _require_registry_fingerprint(expected_registry_fingerprint)
    limit = _require_positive_int(
        maximum_payload_bytes,
        label="maximum payload bytes",
        maximum=MAX_DELTA_PAYLOAD_BYTES,
    )
    if not isinstance(outbox_items, Sequence) or isinstance(outbox_items, (str, bytes)):
        raise ObjectDeltaBatchAssemblyError("object-delta source outbox batch is invalid")
    if not outbox_items or len(outbox_items) > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaBatchAssemblyError("object-delta source outbox batch size is invalid")

    normalized_items = tuple(_validated_item(item) for item in outbox_items)
    first = normalized_items[0]
    writer_term = WriterTermBinding(epoch=first.writer_epoch, lease_id=first.writer_lease_id)
    expected_sequence = first.logical_sequence
    payload_items: list[dict[str, Any]] = []
    seen_change_log_ids: set[int] = set()
    for item in normalized_items:
        if item.logical_sequence != expected_sequence:
            raise ObjectDeltaBatchAssemblyError("object-delta source outbox sequence is not contiguous")
        expected_sequence += 1
        if (item.writer_epoch, item.writer_lease_id) != (writer_term.epoch, writer_term.lease_id):
            raise ObjectDeltaBatchAssemblyError("object-delta source outbox batch mixes Writer Witness terms")
        if item.change_log_id in seen_change_log_ids:
            raise ObjectDeltaBatchAssemblyError("object-delta source outbox batch repeats ChangeLog evidence")
        seen_change_log_ids.add(item.change_log_id)
        payload_items.append({"logical_sequence": item.logical_sequence, **item.canonical_sync_item})

    payload_value = {
        "schema": OBJECT_DELTA_PAYLOAD_SCHEMA,
        "stream_generation_id": stream.stream_generation_id,
        "items": payload_items,
    }
    try:
        payload = canonical_json_bytes(payload_value)
        normalize_object_delta_payload(
            payload_value,
            expected_stream_generation_id=stream.stream_generation_id,
            expected_stream_sequence_ids=tuple(item.logical_sequence for item in normalized_items),
            expected_source_server=SOURCE_SERVER_BY_SITE[stream.source_site],
            expected_registry_fingerprint=fingerprint,
        )
    except ObjectDeltaPayloadError as exc:
        raise ObjectDeltaBatchAssemblyError("object-delta source payload is invalid") from exc
    if len(payload) > limit:
        raise ObjectDeltaBatchAssemblyError("object-delta source payload exceeds its configured bound")

    prepared = PreparedObjectDeltaPayload(
        stream=stream,
        writer_term=writer_term,
        first_sequence=normalized_items[0].logical_sequence,
        last_sequence=normalized_items[-1].logical_sequence,
        sequence_ids=tuple(item.logical_sequence for item in normalized_items),
        payload=payload,
        payload_sha256=sha256_bytes(payload),
    )
    object.__setattr__(prepared, "_capability", _PREPARED_OBJECT_DELTA_PAYLOAD_CAPABILITY)
    return prepared
