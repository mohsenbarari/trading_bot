"""Pure contract for a future append-only Object-Storage sync delta batch.

This module only builds and validates bounded canonical metadata plus an
in-memory payload digest.  It does not open files, contact Object Storage,
load credentials, publish or download objects, apply ChangeLog entries, or
make an import decision.  In particular, a valid batch does *not* claim full
data coverage; a future importer must independently define its table and
ChangeLog semantics before it can use this foundation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any


DELTA_BATCH_SCHEMA = "gold-trade-object-storage-append-only-sync-delta-batch-v2"
IMMUTABLE_RECEIPT_SCHEMA = "gold-trade-object-storage-append-only-sync-delta-receipt-v1"
DELTA_BATCH_STATUS = "prepared"
IMMUTABLE_RECEIPT_STATUS = "read_back_verified"
DELTA_OBJECT_KIND = "sync_delta_batch"
IMPORT_MODE_VALIDATE_ONLY = "validate_only"
GENESIS_PRIOR_CHAIN_SHA256 = "0" * 64

MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_DELTA_PAYLOAD_BYTES = 100 * 1024 * 1024 * 1024
MAX_STREAM_SEQUENCE_IDS = 100_000
MAX_OBJECT_KEY_BYTES = 1024
MAX_VERSION_ID_BYTES = 1024
MAX_LEASE_ID_BYTES = 128
MAX_STREAM_GENERATION_ID_BYTES = 128

WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
STREAM_GENERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
VERSION_ID_RE = re.compile(rf"^[A-Za-z0-9._~+/=-]{{1,{MAX_VERSION_ID_BYTES}}}$")

DELTA_BATCH_FIELDS = frozenset(
    {
        "schema",
        "status",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "writer_term",
        "stream",
        "payload",
        "prior_chain_sha256",
        "import_intent",
        "immutable_receipt",
        "batch_sha256",
    }
)


class AppendOnlySyncDeltaBatchError(ValueError):
    """Raised when append-only delta metadata is unsafe or unbound."""


@dataclass(frozen=True)
class WriterTermBinding:
    """The exact Writer Witness term that produced one future delta batch."""

    epoch: int
    lease_id: str


@dataclass(frozen=True)
class LogicalStreamBinding:
    """One immutable logical stream scoped by the batch's source and target."""

    generation_id: str
    first_sequence: int
    last_sequence: int
    sequence_ids: tuple[int, ...]


@dataclass(frozen=True)
class ImmutableObjectReceipt:
    """Version-bound, non-secret metadata expected after immutable read-back."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class AppendOnlySyncDeltaBatch:
    """Validated metadata only; this object neither imports nor transports data."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    writer_term: WriterTermBinding
    stream: LogicalStreamBinding
    payload_sha256: str
    payload_bytes: int
    prior_chain_sha256: str
    immutable_receipt: ImmutableObjectReceipt
    batch_sha256: str


def canonical_json_bytes(value: object) -> bytes:
    """Return the canonical ASCII JSON representation used by batch hashes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise AppendOnlySyncDeltaBatchError("delta batch cannot be canonically encoded") from exc


def sha256_bytes(payload: bytes) -> str:
    """Return a SHA-256 digest of caller-provided in-memory bytes only."""

    if not isinstance(payload, bytes):
        raise AppendOnlySyncDeltaBatchError("delta payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AppendOnlySyncDeltaBatchError("delta batch contains duplicate JSON fields")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise AppendOnlySyncDeltaBatchError(f"delta batch JSON constant is forbidden: {value}")


def _require_mapping(value: object, *, label: str, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AppendOnlySyncDeltaBatchError(f"{label} fields are invalid")
    return dict(value)


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _require_site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _require_campaign_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or CAMPAIGN_ID_RE.fullmatch(value) is None:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _require_release_sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or RELEASE_SHA_RE.fullmatch(value) is None:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _require_lease_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or LEASE_ID_RE.fullmatch(value) is None:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid") from exc
    if len(encoded) > MAX_LEASE_ID_BYTES:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _validate_writer_term(value: object) -> WriterTermBinding:
    term = _require_mapping(value, label="writer_term", fields=frozenset({"epoch", "lease_id"}))
    return WriterTermBinding(
        epoch=_require_positive_int(term["epoch"], label="writer term epoch"),
        lease_id=_require_lease_id(term["lease_id"], label="writer term lease_id"),
    )


def _require_stream_generation_id(value: object, *, label: str) -> str:
    if not isinstance(value, str) or STREAM_GENERATION_ID_RE.fullmatch(value) is None:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid") from exc
    if len(encoded) > MAX_STREAM_GENERATION_ID_BYTES:
        raise AppendOnlySyncDeltaBatchError(f"{label} is invalid")
    return value


def _validate_stream(value: object) -> LogicalStreamBinding:
    """Validate a contiguous *logical* stream, never a ChangeLog ID range.

    The surrounding source, destination, campaign, and prior-chain binding
    scope this generation.  Raw ChangeLog IDs remain inside the separately
    authenticated payload because PostgreSQL sequence IDs are not a reliable
    append-only stream cursor.
    """

    stream = _require_mapping(
        value,
        label="logical stream",
        fields=frozenset({"generation_id", "first_sequence", "last_sequence", "sequence_ids"}),
    )
    generation_id = _require_stream_generation_id(
        stream["generation_id"], label="logical stream generation_id"
    )
    first_sequence = _require_positive_int(
        stream["first_sequence"], label="logical stream first sequence"
    )
    last_sequence = _require_positive_int(
        stream["last_sequence"], label="logical stream last sequence"
    )
    raw_ids = stream["sequence_ids"]
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_STREAM_SEQUENCE_IDS:
        raise AppendOnlySyncDeltaBatchError("logical stream sequence IDs are invalid")
    if first_sequence > last_sequence or last_sequence - first_sequence + 1 != len(raw_ids):
        raise AppendOnlySyncDeltaBatchError("logical stream range is not contiguous")
    sequence_ids: list[int] = []
    for offset, item in enumerate(raw_ids):
        sequence_id = _require_positive_int(item, label="logical stream sequence ID")
        if sequence_id != first_sequence + offset:
            raise AppendOnlySyncDeltaBatchError("logical stream sequence IDs are not contiguous and ordered")
        sequence_ids.append(sequence_id)
    if sequence_ids[-1] != last_sequence:
        raise AppendOnlySyncDeltaBatchError("logical stream range endpoint is invalid")
    return LogicalStreamBinding(
        generation_id=generation_id,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        sequence_ids=tuple(sequence_ids),
    )


def _validate_payload(value: object) -> tuple[str, int]:
    payload = _require_mapping(value, label="payload", fields=frozenset({"sha256", "bytes"}))
    return (
        _require_sha256(payload["sha256"], label="payload sha256"),
        _require_positive_int(payload["bytes"], label="payload bytes", maximum=MAX_DELTA_PAYLOAD_BYTES),
    )


def _validate_import_intent(value: object) -> None:
    intent = _require_mapping(
        value,
        label="import_intent",
        fields=frozenset({"mode", "side_effects_disabled"}),
    )
    if intent["mode"] != IMPORT_MODE_VALIDATE_ONLY or intent["side_effects_disabled"] is not True:
        raise AppendOnlySyncDeltaBatchError("delta batch import intent must disable side effects")


def _validate_object_key(value: object) -> str:
    if not isinstance(value, str) or OBJECT_KEY_RE.fullmatch(value) is None or ".." in value.split("/"):
        raise AppendOnlySyncDeltaBatchError("immutable receipt object_key is invalid")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise AppendOnlySyncDeltaBatchError("immutable receipt object_key is invalid") from exc
    if len(encoded) > MAX_OBJECT_KEY_BYTES:
        raise AppendOnlySyncDeltaBatchError("immutable receipt object_key is invalid")
    return value


def _validate_version_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or value.lower() == "null"
        or VERSION_ID_RE.fullmatch(value) is None
    ):
        raise AppendOnlySyncDeltaBatchError("immutable receipt version_id is invalid")
    return value


def _validate_immutable_receipt(value: object) -> ImmutableObjectReceipt:
    receipt = _require_mapping(
        value,
        label="immutable_receipt",
        fields=frozenset(
            {
                "schema",
                "status",
                "object_kind",
                "object_key",
                "version_id",
                "ciphertext_sha256",
                "ciphertext_bytes",
            }
        ),
    )
    if (
        receipt["schema"] != IMMUTABLE_RECEIPT_SCHEMA
        or receipt["status"] != IMMUTABLE_RECEIPT_STATUS
        or receipt["object_kind"] != DELTA_OBJECT_KIND
    ):
        raise AppendOnlySyncDeltaBatchError("immutable receipt schema or status is invalid")
    return ImmutableObjectReceipt(
        object_key=_validate_object_key(receipt["object_key"]),
        version_id=_validate_version_id(receipt["version_id"]),
        ciphertext_sha256=_require_sha256(receipt["ciphertext_sha256"], label="immutable receipt ciphertext_sha256"),
        ciphertext_bytes=_require_positive_int(
            receipt["ciphertext_bytes"],
            label="immutable receipt ciphertext_bytes",
            maximum=MAX_DELTA_PAYLOAD_BYTES + 1024 * 1024,
        ),
    )


def _validate_expected_term(
    *,
    expected_writer_epoch: int | None,
    expected_writer_lease_id: str | None,
    actual: WriterTermBinding,
) -> None:
    if (expected_writer_epoch is None) != (expected_writer_lease_id is None):
        raise AppendOnlySyncDeltaBatchError("expected writer term must include both epoch and lease_id")
    if expected_writer_epoch is None:
        return
    expected = WriterTermBinding(
        epoch=_require_positive_int(expected_writer_epoch, label="expected writer term epoch"),
        lease_id=_require_lease_id(expected_writer_lease_id, label="expected writer term lease_id"),
    )
    if actual != expected:
        raise AppendOnlySyncDeltaBatchError("delta batch writer term does not match the expected term")


def validate_delta_batch(
    value: object,
    *,
    expected_source_site: str | None = None,
    expected_destination_site: str | None = None,
    expected_campaign_id: str | None = None,
    expected_release_sha: str | None = None,
    expected_writer_epoch: int | None = None,
    expected_writer_lease_id: str | None = None,
    expected_prior_chain_sha256: str | None = None,
    expected_stream_generation_id: str | None = None,
    expected_first_stream_sequence: int | None = None,
) -> AppendOnlySyncDeltaBatch:
    """Validate one self-hashed delta-batch manifest without any I/O.

    Optional expectations let a future importer bind the batch to its known
    campaign, release, active Writer Witness term, predecessor digest, logical
    stream generation, and next logical sequence before considering a
    read-only payload inspection.  This function intentionally does not apply
    the payload or certify source coverage.
    """

    batch = _require_mapping(value, label="delta batch", fields=DELTA_BATCH_FIELDS)
    if batch["schema"] != DELTA_BATCH_SCHEMA or batch["status"] != DELTA_BATCH_STATUS:
        raise AppendOnlySyncDeltaBatchError("delta batch schema or status is invalid")
    source_site = _require_site(batch["source_site"], label="source_site")
    destination_site = _require_site(batch["destination_site"], label="destination_site")
    if source_site == destination_site:
        raise AppendOnlySyncDeltaBatchError("delta batch source and destination must differ")
    campaign_id = _require_campaign_id(batch["campaign_id"], label="campaign_id")
    release_sha = _require_release_sha(batch["release_sha"], label="release_sha")
    writer_term = _validate_writer_term(batch["writer_term"])
    stream = _validate_stream(batch["stream"])
    payload_sha256, payload_bytes = _validate_payload(batch["payload"])
    prior_chain_sha256 = _require_sha256(batch["prior_chain_sha256"], label="prior_chain_sha256")
    _validate_import_intent(batch["import_intent"])
    immutable_receipt = _validate_immutable_receipt(batch["immutable_receipt"])
    batch_sha256 = _require_sha256(batch["batch_sha256"], label="batch_sha256")
    unsigned = {key: item for key, item in batch.items() if key != "batch_sha256"}
    if sha256_bytes(canonical_json_bytes(unsigned)) != batch_sha256:
        raise AppendOnlySyncDeltaBatchError("delta batch hash is invalid")
    if prior_chain_sha256 == batch_sha256:
        raise AppendOnlySyncDeltaBatchError("delta batch prior chain digest cannot self-reference")

    if expected_source_site is not None and _require_site(
        expected_source_site, label="expected_source_site"
    ) != source_site:
        raise AppendOnlySyncDeltaBatchError("delta batch source does not match the expected source")
    if expected_destination_site is not None and _require_site(
        expected_destination_site, label="expected_destination_site"
    ) != destination_site:
        raise AppendOnlySyncDeltaBatchError("delta batch destination does not match the expected destination")
    if expected_campaign_id is not None and _require_campaign_id(
        expected_campaign_id, label="expected_campaign_id"
    ) != campaign_id:
        raise AppendOnlySyncDeltaBatchError("delta batch campaign does not match the expected campaign")
    if expected_release_sha is not None and _require_release_sha(
        expected_release_sha, label="expected_release_sha"
    ) != release_sha:
        raise AppendOnlySyncDeltaBatchError("delta batch release does not match the expected release")
    _validate_expected_term(
        expected_writer_epoch=expected_writer_epoch,
        expected_writer_lease_id=expected_writer_lease_id,
        actual=writer_term,
    )
    if (
        expected_prior_chain_sha256 is not None or expected_first_stream_sequence is not None
    ) and expected_stream_generation_id is None:
        raise AppendOnlySyncDeltaBatchError(
            "logical stream generation is required when validating a chain continuation"
        )
    if expected_prior_chain_sha256 is not None and _require_sha256(
        expected_prior_chain_sha256, label="expected_prior_chain_sha256"
    ) != prior_chain_sha256:
        raise AppendOnlySyncDeltaBatchError("delta batch prior chain does not match the expected predecessor")
    if expected_stream_generation_id is not None and _require_stream_generation_id(
        expected_stream_generation_id, label="expected logical stream generation_id"
    ) != stream.generation_id:
        raise AppendOnlySyncDeltaBatchError(
            "delta batch logical stream generation does not match the expected generation"
        )
    if expected_first_stream_sequence is not None and _require_positive_int(
        expected_first_stream_sequence, label="expected logical stream first sequence"
    ) != stream.first_sequence:
        raise AppendOnlySyncDeltaBatchError(
            "delta batch logical stream first sequence does not match the expected next sequence"
        )
    return AppendOnlySyncDeltaBatch(
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_term=writer_term,
        stream=stream,
        payload_sha256=payload_sha256,
        payload_bytes=payload_bytes,
        prior_chain_sha256=prior_chain_sha256,
        immutable_receipt=immutable_receipt,
        batch_sha256=batch_sha256,
    )


def parse_delta_batch(raw: bytes, **expected: object) -> AppendOnlySyncDeltaBatch:
    """Parse exactly one canonical newline-terminated delta-batch manifest."""

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BATCH_BYTES:
        raise AppendOnlySyncDeltaBatchError("delta batch input has an unsafe size")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise AppendOnlySyncDeltaBatchError("delta batch JSON is invalid") from exc
    if raw != canonical_json_bytes(value) + b"\n":
        raise AppendOnlySyncDeltaBatchError("delta batch JSON is not canonical")
    return validate_delta_batch(value, **expected)


def verify_delta_payload(batch: AppendOnlySyncDeltaBatch, payload: bytes) -> None:
    """Check only the supplied bytes against a validated batch descriptor.

    This function does not parse, import, persist, or otherwise act on the
    payload.  It exists so a future caller can fail closed before any separate
    read-only inspection or explicitly authorized import implementation.
    """

    if not isinstance(batch, AppendOnlySyncDeltaBatch):
        raise AppendOnlySyncDeltaBatchError("delta batch descriptor is invalid")
    if not isinstance(payload, bytes):
        raise AppendOnlySyncDeltaBatchError("delta payload must be bytes")
    if len(payload) != batch.payload_bytes or sha256_bytes(payload) != batch.payload_sha256:
        raise AppendOnlySyncDeltaBatchError("delta payload does not match its batch descriptor")


def build_delta_batch(
    *,
    source_site: str,
    destination_site: str,
    campaign_id: str,
    release_sha: str,
    writer_epoch: int,
    writer_lease_id: str,
    stream_generation_id: str,
    stream_sequence_ids: Sequence[int],
    payload: bytes,
    prior_chain_sha256: str,
    immutable_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Build self-hashed metadata from caller-provided in-memory payload bytes.

    The caller still owns all Object Storage work and must obtain the immutable
    receipt elsewhere.  This helper performs no filesystem, network, provider,
    or import operation.
    """

    sequence_ids = list(stream_sequence_ids)
    if not sequence_ids:
        raise AppendOnlySyncDeltaBatchError("logical stream sequence IDs are invalid")
    if not isinstance(payload, bytes):
        raise AppendOnlySyncDeltaBatchError("delta payload must be bytes")
    unsigned: dict[str, Any] = {
        "schema": DELTA_BATCH_SCHEMA,
        "status": DELTA_BATCH_STATUS,
        "source_site": source_site,
        "destination_site": destination_site,
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "writer_term": {"epoch": writer_epoch, "lease_id": writer_lease_id},
        "stream": {
            "generation_id": stream_generation_id,
            "first_sequence": sequence_ids[0],
            "last_sequence": sequence_ids[-1],
            "sequence_ids": sequence_ids,
        },
        "payload": {"sha256": sha256_bytes(payload), "bytes": len(payload)},
        "prior_chain_sha256": prior_chain_sha256,
        "import_intent": {"mode": IMPORT_MODE_VALIDATE_ONLY, "side_effects_disabled": True},
        "immutable_receipt": dict(immutable_receipt),
    }
    value = {**unsigned, "batch_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
    validate_delta_batch(value)
    return value
