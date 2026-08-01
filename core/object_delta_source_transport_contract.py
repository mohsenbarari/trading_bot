"""Pure source-side Object Storage contract for one Object-delta ciphertext.

This module defines the data that a future controller-presigned source
publisher may exchange with a WebApp source.  It intentionally has no S3
client, network, filesystem, subprocess, age, URL, or credential capability.
Provider credentials remain controller-only through
``ObjectDeltaTransportPolicy``; no credential field exists in any type here.

The contract is deliberately separate from the legacy WebApp-FI source
transport.  An Object-delta key is always derived by the existing
``derive_object_delta_object_key`` binding, and its metadata has a different
typed shape.  A future runtime must use the pure values below only after it
has independently checked the private/versioned bucket and performed the
actual conditional PUT or exact-version GET.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from core.append_only_sync_delta_batch import (
    DELTA_OBJECT_KIND,
    IMMUTABLE_RECEIPT_SCHEMA,
    IMMUTABLE_RECEIPT_STATUS,
    MAX_DELTA_PAYLOAD_BYTES,
    MAX_STREAM_SEQUENCE_IDS,
    OBJECT_KEY_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
    sha256_bytes,
)
from core.object_delta_source_batch_ledger import (
    ObjectDeltaSourceLedgerError,
    SourceStreamIdentity,
)
from core.object_delta_transport_binding import (
    OBJECT_DELTA_ENCRYPTION,
    OBJECT_DELTA_TRANSPORT_SCHEMA,
    ObjectDeltaTransportBindingError,
    ObjectDeltaTransportPolicy,
    derive_object_delta_object_key,
    destination_age_recipient,
    validate_object_delta_transport_policy,
)


OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_SCHEMA = "gold-trade-object-delta-source-transport-receipt-v1"
OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_STATUS = IMMUTABLE_RECEIPT_STATUS
MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES = 1024 * 1024
MAX_OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_BYTES = 32 * 1024

_REQUEST_FIELDS = frozenset(
    {
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "first_sequence",
        "last_sequence",
        "payload_sha256",
    }
)
_PAYLOAD_FIELDS = frozenset({"sha256", "bytes"})
_OBJECT_FIELDS = frozenset(
    {"object_key", "version_id", "ciphertext_sha256", "ciphertext_bytes"}
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bucket",
        "request",
        "destination_age_recipient",
        "payload",
        "transport",
        "object",
        "receipt_sha256",
    }
)


class ObjectDeltaSourceTransportContractError(ValueError):
    """A future Object-delta source transport value is unsafe or unbound."""


@dataclass(frozen=True)
class ObjectDeltaSourceTransportPolicy:
    """Public Object-delta policy for a controller-operated source transfer.

    ``transport_policy`` carries the fixed bucket, prefix, and recipient pins
    and itself requires ``credential_holder == 'controller'``.  This wrapper
    intentionally has no endpoint, credential path, credential value, or URL.
    Those belong to a later controller-only adapter.
    """

    transport_policy: ObjectDeltaTransportPolicy
    maximum_plaintext_bytes: int = MAX_DELTA_PAYLOAD_BYTES


@dataclass(frozen=True)
class ObjectDeltaSourceTransportRequest:
    """The immutable logical delta range that determines one Object key."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    first_sequence: int
    last_sequence: int
    payload_sha256: str


@dataclass(frozen=True)
class ObjectDeltaSourceTransportExpectation:
    """Hashes and sizes fixed before a controller creates a PUT capability."""

    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class ObjectDeltaSourceTransportRoute:
    """A request resolved to its sole key and destination age recipient."""

    request: ObjectDeltaSourceTransportRequest
    object_key: str
    destination_age_recipient: str


@dataclass(frozen=True)
class ObjectDeltaImmutableObjectDescriptor:
    """The exact immutable object identity returned after a successful PUT."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class ObjectDeltaExactReadback:
    """A future adapter's normalized exact-VersionId GET observation.

    ``provider_side_encryption`` must be ``None`` only after the adapter has
    inspected every provider SSE response field.  The pure contract cannot
    perform that inspection itself.
    """

    object_key: str
    version_id: str
    metadata: Mapping[str, str]
    ciphertext_sha256: str
    ciphertext_bytes: int
    # The adapter must make the no-SSE observation explicit.  A default would
    # let a caller accidentally turn an uninspected response into a no-SSE
    # claim in an immutable receipt.
    provider_side_encryption: str | None


@dataclass(frozen=True)
class ObjectDeltaSourceTransportAttempt:
    """The durable, URL-free source candidate required before adoption.

    A future source sender must persist an equivalent root-only attempt record
    before a direct PUT.  This type deliberately contains no object version,
    URL, credential, or mutable runtime state.
    """

    request: ObjectDeltaSourceTransportRequest
    expectation: ObjectDeltaSourceTransportExpectation


@dataclass(frozen=True)
class ObjectDeltaExactObjectVersionHistory:
    """A complete exact-key version listing normalized by a future adapter."""

    object_key: str
    version_ids: Sequence[str]
    delete_marker_version_ids: Sequence[str]
    latest_version_id: str | None
    listing_complete: bool


def _require_exact_mapping(value: object, *, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ObjectDeltaSourceTransportContractError(f"{label} fields are invalid")
    return dict(value)


def _require_text(value: object, *, label: str, pattern) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaSourceTransportContractError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaSourceTransportContractError(f"{label} is invalid")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ObjectDeltaSourceTransportContractError(
                "Object-delta source transport receipt has duplicate fields"
            )
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ObjectDeltaSourceTransportContractError(
        f"Object-delta source transport receipt JSON constant is forbidden: {value}"
    )


def _reject_persisted_url(payload: bytes) -> None:
    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt persists a forbidden URL"
        )


def _validate_policy(
    policy: ObjectDeltaSourceTransportPolicy,
) -> ObjectDeltaSourceTransportPolicy:
    if not isinstance(policy, ObjectDeltaSourceTransportPolicy):
        raise ObjectDeltaSourceTransportContractError("Object-delta source transport policy is invalid")
    try:
        transport_policy = validate_object_delta_transport_policy(policy.transport_policy)
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport policy is invalid"
        ) from exc
    maximum = _require_positive_int(
        policy.maximum_plaintext_bytes,
        label="Object-delta maximum plaintext bytes",
        maximum=MAX_DELTA_PAYLOAD_BYTES,
    )
    return ObjectDeltaSourceTransportPolicy(
        transport_policy=transport_policy,
        maximum_plaintext_bytes=maximum,
    )


def validate_object_delta_source_transport_policy(
    policy: ObjectDeltaSourceTransportPolicy,
) -> ObjectDeltaSourceTransportPolicy:
    """Validate public pins while preserving controller-only credentials.

    This function never loads credentials.  The nested binding policy rejects
    any non-controller credential holder before a future adapter can create a
    presigned request.
    """

    return _validate_policy(policy)


def _normalize_request(
    request: ObjectDeltaSourceTransportRequest,
) -> ObjectDeltaSourceTransportRequest:
    if not isinstance(request, ObjectDeltaSourceTransportRequest):
        raise ObjectDeltaSourceTransportContractError("Object-delta source transport request is invalid")
    try:
        stream = SourceStreamIdentity(
            source_site=request.source_site,
            destination_site=request.destination_site,
            campaign_id=request.campaign_id,
            release_sha=request.release_sha,
            stream_generation_id=request.stream_generation_id,
        )
    except (AttributeError, TypeError, ObjectDeltaSourceLedgerError) as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport request is invalid"
        ) from exc
    first = _require_positive_int(
        request.first_sequence,
        label="Object-delta source first sequence",
        maximum=None,
    )
    last = _require_positive_int(
        request.last_sequence,
        label="Object-delta source last sequence",
        maximum=None,
    )
    if last < first or last - first + 1 > MAX_STREAM_SEQUENCE_IDS:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport sequence range is invalid"
        )
    return ObjectDeltaSourceTransportRequest(
        source_site=stream.source_site,
        destination_site=stream.destination_site,
        campaign_id=stream.campaign_id,
        release_sha=stream.release_sha,
        stream_generation_id=stream.stream_generation_id,
        first_sequence=first,
        last_sequence=last,
        payload_sha256=_require_text(
            request.payload_sha256,
            label="Object-delta source payload SHA-256",
            pattern=SHA256_RE,
        ),
    )


def _route_for(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
) -> ObjectDeltaSourceTransportRoute:
    normalized_policy = _validate_policy(policy)
    normalized_request = _normalize_request(request)
    try:
        object_key = derive_object_delta_object_key(
            normalized_policy.transport_policy,
            source_site=normalized_request.source_site,
            destination_site=normalized_request.destination_site,
            campaign_id=normalized_request.campaign_id,
            release_sha=normalized_request.release_sha,
            stream_generation_id=normalized_request.stream_generation_id,
            first_sequence=normalized_request.first_sequence,
            last_sequence=normalized_request.last_sequence,
            payload_sha256=normalized_request.payload_sha256,
        )
        recipient = destination_age_recipient(
            normalized_policy.transport_policy,
            destination_site=normalized_request.destination_site,
        )
    except ObjectDeltaTransportBindingError as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport route is invalid"
        ) from exc
    return ObjectDeltaSourceTransportRoute(
        request=normalized_request,
        object_key=object_key,
        destination_age_recipient=recipient,
    )


def resolve_object_delta_source_transport_route(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
) -> ObjectDeltaSourceTransportRoute:
    """Resolve the sole deterministic key without creating any capability."""

    return _route_for(policy, request)


def _normalize_expectation(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> ObjectDeltaSourceTransportExpectation:
    normalized_policy = _validate_policy(policy)
    normalized_request = _normalize_request(request)
    if not isinstance(expectation, ObjectDeltaSourceTransportExpectation):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport expectation is invalid"
        )
    plaintext_sha256 = _require_text(
        expectation.plaintext_sha256,
        label="Object-delta source plaintext SHA-256",
        pattern=SHA256_RE,
    )
    if plaintext_sha256 != normalized_request.payload_sha256:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source plaintext hash does not match the deterministic key request"
        )
    return ObjectDeltaSourceTransportExpectation(
        plaintext_sha256=plaintext_sha256,
        plaintext_bytes=_require_positive_int(
            expectation.plaintext_bytes,
            label="Object-delta source plaintext bytes",
            maximum=normalized_policy.maximum_plaintext_bytes,
        ),
        ciphertext_sha256=_require_text(
            expectation.ciphertext_sha256,
            label="Object-delta source ciphertext SHA-256",
            pattern=SHA256_RE,
        ),
        ciphertext_bytes=_require_positive_int(
            expectation.ciphertext_bytes,
            label="Object-delta source ciphertext bytes",
            maximum=(
                normalized_policy.maximum_plaintext_bytes
                + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES
            ),
        ),
    )


def validate_object_delta_source_transport_expectation(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> ObjectDeltaSourceTransportExpectation:
    """Validate a source's URL-free ciphertext expectation before presigning."""

    return _normalize_expectation(policy, request, expectation)


def _metadata_for(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> dict[str, str]:
    route = _route_for(policy, request)
    expected = _normalize_expectation(policy, route.request, expectation)
    return {
        "transport-schema": OBJECT_DELTA_TRANSPORT_SCHEMA,
        "encryption": OBJECT_DELTA_ENCRYPTION,
        "ciphertext-sha256": expected.ciphertext_sha256,
        "source-site": route.request.source_site,
        "destination-site": route.request.destination_site,
        "stream-generation-id": route.request.stream_generation_id,
    }


def required_object_delta_source_upload_metadata(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> dict[str, str]:
    """Return the exact no-SSE metadata for one conditional source PUT."""

    return _metadata_for(policy, request, expectation)


def required_object_delta_source_upload_headers(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> dict[str, str]:
    """Return the exact headers a later presigned PUT must sign and send."""

    metadata = _metadata_for(policy, request, expectation)
    return {
        "content-type": "application/octet-stream",
        "if-none-match": "*",
        **{"x-amz-meta-" + name: value for name, value in metadata.items()},
    }


def strict_object_delta_source_transport_guarantees() -> dict[str, object]:
    """The immutable transport claims that receipts must contain exactly."""

    return {
        "encryption": OBJECT_DELTA_ENCRYPTION,
        "create_only": True,
        "private_bucket": True,
        "versioned_bucket": True,
        "provider_side_sse": False,
        "read_back_same_version_id": True,
        "controller_credentials_only": True,
    }


def _descriptor_from_mapping(value: object) -> ObjectDeltaImmutableObjectDescriptor:
    raw = _require_exact_mapping(value, fields=_OBJECT_FIELDS, label="Object-delta immutable object")
    key = _require_text(raw["object_key"], label="Object-delta immutable object key", pattern=OBJECT_KEY_RE)
    if ".." in key.split("/"):
        raise ObjectDeltaSourceTransportContractError("Object-delta immutable object key is invalid")
    version_id = _require_text(
        raw["version_id"],
        label="Object-delta immutable object VersionId",
        pattern=VERSION_ID_RE,
    )
    if version_id.lower() == "null":
        raise ObjectDeltaSourceTransportContractError("Object-delta immutable object VersionId is invalid")
    return ObjectDeltaImmutableObjectDescriptor(
        object_key=key,
        version_id=version_id,
        ciphertext_sha256=_require_text(
            raw["ciphertext_sha256"],
            label="Object-delta immutable object ciphertext SHA-256",
            pattern=SHA256_RE,
        ),
        ciphertext_bytes=_require_positive_int(
            raw["ciphertext_bytes"],
            label="Object-delta immutable object ciphertext bytes",
            maximum=(MAX_DELTA_PAYLOAD_BYTES + MAX_OBJECT_DELTA_CIPHERTEXT_OVERHEAD_BYTES),
        ),
    )


def _normalize_descriptor(
    descriptor: ObjectDeltaImmutableObjectDescriptor,
) -> ObjectDeltaImmutableObjectDescriptor:
    if not isinstance(descriptor, ObjectDeltaImmutableObjectDescriptor):
        raise ObjectDeltaSourceTransportContractError("Object-delta immutable object descriptor is invalid")
    return _descriptor_from_mapping(
        {
            "object_key": descriptor.object_key,
            "version_id": descriptor.version_id,
            "ciphertext_sha256": descriptor.ciphertext_sha256,
            "ciphertext_bytes": descriptor.ciphertext_bytes,
        }
    )


def _normalize_metadata(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact read-back metadata is invalid")
    normalized: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not isinstance(item, str):
            raise ObjectDeltaSourceTransportContractError(
                "Object-delta exact read-back metadata is invalid"
            )
        normalized[name] = item
    return normalized


def _normalize_readback(value: ObjectDeltaExactReadback) -> ObjectDeltaExactReadback:
    if not isinstance(value, ObjectDeltaExactReadback):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact read-back is invalid")
    descriptor = _descriptor_from_mapping(
        {
            "object_key": value.object_key,
            "version_id": value.version_id,
            "ciphertext_sha256": value.ciphertext_sha256,
            "ciphertext_bytes": value.ciphertext_bytes,
        }
    )
    return ObjectDeltaExactReadback(
        object_key=descriptor.object_key,
        version_id=descriptor.version_id,
        metadata=_normalize_metadata(value.metadata),
        ciphertext_sha256=descriptor.ciphertext_sha256,
        ciphertext_bytes=descriptor.ciphertext_bytes,
        provider_side_encryption=value.provider_side_encryption,
    )


def validate_object_delta_exact_same_version_descriptor(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
    descriptor: ObjectDeltaImmutableObjectDescriptor,
    readback: ObjectDeltaExactReadback,
) -> ObjectDeltaImmutableObjectDescriptor:
    """Validate that one descriptor and one GET observation name the same version.

    The caller supplies a normalized GET observation after it has requested an
    exact ``VersionId``.  This function rejects a changed VersionId, changed
    metadata, any provider SSE indication, or a changed ciphertext binding.
    """

    route = _route_for(policy, request)
    expected = _normalize_expectation(policy, route.request, expectation)
    normalized_descriptor = _normalize_descriptor(descriptor)
    normalized_readback = _normalize_readback(readback)
    if normalized_descriptor.object_key != route.object_key:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta immutable object key does not match the deterministic route"
        )
    if (
        normalized_descriptor.ciphertext_sha256 != expected.ciphertext_sha256
        or normalized_descriptor.ciphertext_bytes != expected.ciphertext_bytes
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta immutable object does not match the ciphertext expectation"
        )
    if normalized_readback.object_key != route.object_key:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta read-back key does not match the deterministic route"
        )
    if normalized_readback.version_id != normalized_descriptor.version_id:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta read-back does not match the exact immutable VersionId"
        )
    if normalized_readback.provider_side_encryption is not None:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta read-back enables forbidden provider-side encryption"
        )
    if normalized_readback.metadata != _metadata_for(policy, route.request, expected):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta read-back metadata does not match the typed transport"
        )
    if (
        normalized_readback.ciphertext_sha256 != expected.ciphertext_sha256
        or normalized_readback.ciphertext_bytes != expected.ciphertext_bytes
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta read-back ciphertext does not match the expectation"
        )
    return normalized_descriptor


def _request_to_value(request: ObjectDeltaSourceTransportRequest) -> dict[str, object]:
    return {
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "stream_generation_id": request.stream_generation_id,
        "first_sequence": request.first_sequence,
        "last_sequence": request.last_sequence,
        "payload_sha256": request.payload_sha256,
    }


def _request_from_mapping(value: object) -> ObjectDeltaSourceTransportRequest:
    raw = _require_exact_mapping(value, fields=_REQUEST_FIELDS, label="Object-delta source transport request")
    return _normalize_request(
        ObjectDeltaSourceTransportRequest(
            source_site=raw["source_site"],
            destination_site=raw["destination_site"],
            campaign_id=raw["campaign_id"],
            release_sha=raw["release_sha"],
            stream_generation_id=raw["stream_generation_id"],
            first_sequence=raw["first_sequence"],
            last_sequence=raw["last_sequence"],
            payload_sha256=raw["payload_sha256"],
        )
    )


def _payload_from_mapping(value: object, *, maximum_bytes: int) -> tuple[str, int]:
    raw = _require_exact_mapping(value, fields=_PAYLOAD_FIELDS, label="Object-delta source payload")
    return (
        _require_text(raw["sha256"], label="Object-delta source payload SHA-256", pattern=SHA256_RE),
        _require_positive_int(
            raw["bytes"],
            label="Object-delta source payload bytes",
            maximum=maximum_bytes,
        ),
    )


def _receipt_unsigned(
    *,
    policy: ObjectDeltaSourceTransportPolicy,
    route: ObjectDeltaSourceTransportRoute,
    expectation: ObjectDeltaSourceTransportExpectation,
    descriptor: ObjectDeltaImmutableObjectDescriptor,
) -> dict[str, object]:
    return {
        "schema": OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_SCHEMA,
        "status": OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_STATUS,
        "bucket": policy.transport_policy.bucket,
        "request": _request_to_value(route.request),
        "destination_age_recipient": route.destination_age_recipient,
        "payload": {
            "sha256": expectation.plaintext_sha256,
            "bytes": expectation.plaintext_bytes,
        },
        "transport": strict_object_delta_source_transport_guarantees(),
        "object": {
            "object_key": descriptor.object_key,
            "version_id": descriptor.version_id,
            "ciphertext_sha256": descriptor.ciphertext_sha256,
            "ciphertext_bytes": descriptor.ciphertext_bytes,
        },
    }


def build_verified_object_delta_source_transport_receipt(
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
    descriptor: ObjectDeltaImmutableObjectDescriptor,
    readback: ObjectDeltaExactReadback,
) -> dict[str, object]:
    """Build one URL-free receipt only after exact-version read-back validation."""

    normalized_policy = _validate_policy(policy)
    route = _route_for(normalized_policy, request)
    normalized_expectation = _normalize_expectation(
        normalized_policy,
        route.request,
        expectation,
    )
    normalized_descriptor = validate_object_delta_exact_same_version_descriptor(
        normalized_policy,
        route.request,
        normalized_expectation,
        descriptor,
        readback,
    )
    unsigned = _receipt_unsigned(
        policy=normalized_policy,
        route=route,
        expectation=normalized_expectation,
        descriptor=normalized_descriptor,
    )
    return {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def _parse_canonical_receipt(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_BYTES:
        raise ObjectDeltaSourceTransportContractError("Object-delta source transport receipt is invalid")
    _reject_persisted_url(payload)
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ObjectDeltaSourceTransportContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt is invalid"
        ) from exc
    try:
        canonical = canonical_json_bytes(value)
    except (ValueError, RecursionError) as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt is invalid"
        ) from exc
    if not isinstance(value, dict) or payload != canonical + b"\n":
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt is not canonical"
        )
    return value


def verify_object_delta_source_transport_receipt(
    payload: bytes,
    *,
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> ObjectDeltaImmutableObjectDescriptor:
    """Verify a URL-free receipt against the exact pre-PUT source candidate.

    Receipt verification proves only the persisted claim.  A later controller
    adapter must still perform a fresh exact-VersionId GET before delivery or
    before adopting an unrecorded object.
    """

    normalized_policy = _validate_policy(policy)
    route = _route_for(normalized_policy, request)
    normalized_expectation = _normalize_expectation(
        normalized_policy,
        route.request,
        expectation,
    )
    value = _parse_canonical_receipt(payload)
    raw = _require_exact_mapping(value, fields=_RECEIPT_FIELDS, label="Object-delta source transport receipt")
    if (
        raw["schema"] != OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_SCHEMA
        or raw["status"] != OBJECT_DELTA_SOURCE_TRANSPORT_RECEIPT_STATUS
        or raw["bucket"] != normalized_policy.transport_policy.bucket
        or raw["destination_age_recipient"] != route.destination_age_recipient
        or raw["transport"] != strict_object_delta_source_transport_guarantees()
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt protocol is invalid"
        )
    receipt_request = _request_from_mapping(raw["request"])
    if receipt_request != route.request:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt request does not match the candidate"
        )
    payload_sha256, payload_bytes = _payload_from_mapping(
        raw["payload"],
        maximum_bytes=normalized_policy.maximum_plaintext_bytes,
    )
    if (payload_sha256, payload_bytes) != (
        normalized_expectation.plaintext_sha256,
        normalized_expectation.plaintext_bytes,
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt payload does not match the candidate"
        )
    descriptor = _descriptor_from_mapping(raw["object"])
    if (
        descriptor.object_key != route.object_key
        or descriptor.ciphertext_sha256 != normalized_expectation.ciphertext_sha256
        or descriptor.ciphertext_bytes != normalized_expectation.ciphertext_bytes
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt object does not match the candidate"
        )
    unsigned = _receipt_unsigned(
        policy=normalized_policy,
        route=route,
        expectation=normalized_expectation,
        descriptor=descriptor,
    )
    if raw["receipt_sha256"] != sha256_bytes(canonical_json_bytes(unsigned)):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt checksum is invalid"
        )
    return descriptor


def canonical_object_delta_source_transport_receipt_bytes(
    receipt: Mapping[str, object],
    *,
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> bytes:
    """Return canonical receipt bytes after validating the complete binding."""

    if not isinstance(receipt, Mapping):
        raise ObjectDeltaSourceTransportContractError("Object-delta source transport receipt is invalid")
    try:
        payload = canonical_json_bytes(dict(receipt)) + b"\n"
    except (ValueError, RecursionError) as exc:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta source transport receipt is invalid"
        ) from exc
    verify_object_delta_source_transport_receipt(
        payload,
        policy=policy,
        request=request,
        expectation=expectation,
    )
    return payload


def append_only_immutable_receipt_from_verified_source_transport_receipt(
    payload: bytes,
    *,
    policy: ObjectDeltaSourceTransportPolicy,
    request: ObjectDeltaSourceTransportRequest,
    expectation: ObjectDeltaSourceTransportExpectation,
) -> dict[str, object]:
    """Project a verified transport receipt into the existing batch receipt shape."""

    descriptor = verify_object_delta_source_transport_receipt(
        payload,
        policy=policy,
        request=request,
        expectation=expectation,
    )
    return {
        "schema": IMMUTABLE_RECEIPT_SCHEMA,
        "status": IMMUTABLE_RECEIPT_STATUS,
        "object_kind": DELTA_OBJECT_KIND,
        "object_key": descriptor.object_key,
        "version_id": descriptor.version_id,
        "ciphertext_sha256": descriptor.ciphertext_sha256,
        "ciphertext_bytes": descriptor.ciphertext_bytes,
    }


def _normalize_attempt(
    policy: ObjectDeltaSourceTransportPolicy,
    attempt: ObjectDeltaSourceTransportAttempt,
) -> tuple[ObjectDeltaSourceTransportRoute, ObjectDeltaSourceTransportExpectation]:
    if not isinstance(attempt, ObjectDeltaSourceTransportAttempt):
        raise ObjectDeltaSourceTransportContractError("Object-delta source transport attempt is invalid")
    route = _route_for(policy, attempt.request)
    expectation = _normalize_expectation(policy, route.request, attempt.expectation)
    return route, expectation


def _normalize_history(value: ObjectDeltaExactObjectVersionHistory) -> ObjectDeltaExactObjectVersionHistory:
    if not isinstance(value, ObjectDeltaExactObjectVersionHistory):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact object history is invalid")
    key = _require_text(value.object_key, label="Object-delta exact object history key", pattern=OBJECT_KEY_RE)
    if ".." in key.split("/"):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact object history key is invalid")

    def normalize_ids(candidate: object, *, label: str) -> tuple[str, ...]:
        if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
            raise ObjectDeltaSourceTransportContractError(f"{label} is invalid")
        return tuple(
            _require_text(item, label=label, pattern=VERSION_ID_RE)
            for item in candidate
        )

    version_ids = normalize_ids(value.version_ids, label="Object-delta exact object history versions")
    delete_marker_ids = normalize_ids(
        value.delete_marker_version_ids,
        label="Object-delta exact object history delete markers",
    )
    if any(item.lower() == "null" for item in (*version_ids, *delete_marker_ids)):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact object history version is invalid")
    if len(set(version_ids)) != len(version_ids) or len(set(delete_marker_ids)) != len(delete_marker_ids):
        raise ObjectDeltaSourceTransportContractError("Object-delta exact object history is ambiguous")
    latest = value.latest_version_id
    if latest is not None:
        latest = _require_text(latest, label="Object-delta exact object history latest version", pattern=VERSION_ID_RE)
        if latest.lower() == "null":
            raise ObjectDeltaSourceTransportContractError(
                "Object-delta exact object history latest version is invalid"
            )
    if value.listing_complete is not True:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta exact object history listing is incomplete"
        )
    return ObjectDeltaExactObjectVersionHistory(
        object_key=key,
        version_ids=version_ids,
        delete_marker_version_ids=delete_marker_ids,
        latest_version_id=latest,
        listing_complete=True,
    )


def assess_object_delta_singleton_adopt_eligibility(
    policy: ObjectDeltaSourceTransportPolicy,
    attempt: ObjectDeltaSourceTransportAttempt,
    history: ObjectDeltaExactObjectVersionHistory,
    readback: ObjectDeltaExactReadback,
) -> dict[str, object]:
    """Return a receipt only when an interrupted PUT is safe to adopt.

    The future caller must supply a durable URL-free source attempt record, a
    complete exact-key listing, and an exact-version GET observation.  A key
    with a delete marker, more than one version, an incomplete listing, or a
    changed object is never adopted and must remain blocked for reconciliation.
    This function never probes, uploads, retries, or deletes an Object.
    """

    normalized_policy = _validate_policy(policy)
    route, expectation = _normalize_attempt(normalized_policy, attempt)
    normalized_history = _normalize_history(history)
    normalized_readback = _normalize_readback(readback)
    if normalized_history.object_key != route.object_key:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta exact object history key does not match the attempt"
        )
    if (
        len(normalized_history.version_ids) != 1
        or normalized_history.delete_marker_version_ids
        or normalized_history.latest_version_id != normalized_history.version_ids[0]
    ):
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta existing object is not one safe immutable singleton"
        )
    descriptor = ObjectDeltaImmutableObjectDescriptor(
        object_key=normalized_readback.object_key,
        version_id=normalized_readback.version_id,
        ciphertext_sha256=normalized_readback.ciphertext_sha256,
        ciphertext_bytes=normalized_readback.ciphertext_bytes,
    )
    if descriptor.version_id != normalized_history.version_ids[0]:
        raise ObjectDeltaSourceTransportContractError(
            "Object-delta exact read-back does not match the singleton version"
        )
    return build_verified_object_delta_source_transport_receipt(
        normalized_policy,
        route.request,
        expectation,
        descriptor,
        normalized_readback,
    )
