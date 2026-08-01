#!/usr/bin/env python3
"""Pure validation contract for WebApp-FI source-phase transport.

This module is deliberately stdlib-only and side-effect free.  It contains
the typed-object, recipient-pinning, receipt, descriptor, and transient URL
rules shared by a future controller publisher and WebApp-FI sender.  It does
not load configuration files or credentials, invoke a process, create an S3
client, or perform filesystem or network I/O.

The contract has exactly five permitted source-phase directions.  In
particular, the static archive is the sole dual-recipient object and must be
encrypted once for the controller followed by WebApp-IR, in that order.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlsplit


TRANSPORT_SCHEMA = "gold-trade-webapp-fi-source-transport-v1"
# ``CONFIG_SCHEMA`` is retained for the URL-free FI exchange policy projection.
# The controller's on-disk config has a distinct, stricter v2 shape; sharing a
# schema label would let a v1 exchange policy masquerade as a controller config.
CONFIG_SCHEMA = "gold-trade-webapp-fi-source-transport-config-v1"
CONTROLLER_CONFIG_SCHEMA = "gold-trade-webapp-fi-source-transport-config-v2"
OBJECT_ENCRYPTION = "age-v1"
OBJECT_LAYOUT_VERSION = "v1"
STATIC_MODE = "static"
SINGLE_MODE = "single"
STATIC_DESTINATION_SITE = "controller_webapp_ir"
BOOTSTRAP_OBJECT_KIND = "bootstrap_package"
STATIC_OBJECT_KIND = "static"
STATIC_PROVENANCE_OBJECT_KIND = "static-provenance"
RAW_APP_IMAGE_OBJECT_KIND = "raw-app-image"
SOURCE_EVIDENCE_OBJECT_KIND = "source-evidence"
# The source image bundle can legitimately contain the four production images
# and must therefore be admitted up to the raw-image transport limit.  The
# controller config cannot lower or raise this value; it is a code pin.
MAXIMUM_PLAINTEXT_BYTES = 100 * 1024 * 1024 * 1024
MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES = 1024 * 1024
MINIMUM_PRESIGNED_URL_SECONDS = 1
MAXIMUM_PRESIGNED_URL_SECONDS = 900
FIXED_AGE_BINARY = "/usr/bin/age"
SOURCE_TRANSPORT_WORKSPACE_ROOT = Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source")

# This is the complete controller-only configuration projection.  Runtime
# policy fields that can be derived safely are deliberately not accepted from
# disk, so a stale configuration cannot redirect a workspace, executable,
# region, or size limit.
CONTROLLER_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "endpoint",
        "bucket",
        "prefix",
        "credentials_file",
        "controller_age_recipient",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "presign_expires_seconds",
    }
)

CAMPAIGN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
SITE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
OBJECT_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
PREFIX_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,127}$")
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")
AMZ_DATE_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
SIGV4_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
ARVAN_S3_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir/?$"
)

# These are intentionally exact rather than minimum sets.  A source sender
# cannot add a provider-side encryption header, omit the create-only
# condition, or introduce an unrelated signed request header.
_SIGV4_REQUIRED_QUERY = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
    }
)
_SIGV4_OPTIONAL_QUERY = frozenset({"X-Amz-Security-Token"})
_PUT_SIGNED_HEADERS = (
    "content-type",
    "host",
    "if-none-match",
    "x-amz-meta-ciphertext-sha256",
    "x-amz-meta-encryption",
    "x-amz-meta-recipient-mode",
    "x-amz-meta-transport-schema",
)
_GET_SIGNED_HEADERS = ("host",)


class SourceTransportError(RuntimeError):
    """The pure source-phase immutable transport contract was violated."""


@dataclasses.dataclass(frozen=True)
class SourceTransportPolicy:
    """Non-secret, public-recipient policy shared with an FI sender."""

    endpoint: str
    region: str
    bucket: str
    prefix: str
    age_binary: str
    workspace: Path
    controller_age_recipient: str
    webapp_fi_age_recipient: str
    webapp_ir_age_recipient: str
    maximum_plaintext_bytes: int = MAXIMUM_PLAINTEXT_BYTES


# Preserve the compact historical name used by source transport callers.
SourceTransportConfig = SourceTransportPolicy


@dataclasses.dataclass(frozen=True)
class SourceObjectRequest:
    """The complete typed binding for one source-phase object."""

    campaign_id: str
    release_sha: str
    control_commit: str
    control_tree: str
    source_site: str
    destination_site: str
    object_kind: str
    object_id: str
    mode: str
    recipients: Sequence[str]


@dataclasses.dataclass(frozen=True)
class SourceObjectExpectation:
    """Non-secret hashes and sizes used to bind one encrypted object."""

    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int


# A typed object cannot be routed to a new role merely by changing an argument.
_ALLOWED_DIRECTIONS: dict[tuple[str, str, str], str] = {
    ("bot_fi", "webapp_fi", BOOTSTRAP_OBJECT_KIND): SINGLE_MODE,
    ("webapp_fi", STATIC_DESTINATION_SITE, STATIC_OBJECT_KIND): STATIC_MODE,
    ("controller", "webapp_fi", STATIC_PROVENANCE_OBJECT_KIND): SINGLE_MODE,
    ("webapp_fi", "controller", RAW_APP_IMAGE_OBJECT_KIND): SINGLE_MODE,
    ("webapp_fi", "controller", SOURCE_EVIDENCE_OBJECT_KIND): SINGLE_MODE,
}
ALLOWED_DIRECTIONS: Mapping[tuple[str, str, str], str] = _ALLOWED_DIRECTIONS


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Encode canonical ASCII JSON without touching persistent state."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    """Return a SHA-256 digest of caller-supplied bytes."""

    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceTransportError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceTransportError(f"JSON input contains unsupported constant: {value}")


def _reject_persisted_url(payload: bytes, *, field: str) -> None:
    """Receipts and descriptors must never retain transient control URLs."""

    lowered = payload.lower()
    if b"://" in lowered or b"presigned" in lowered or b'"url"' in lowered:
        raise SourceTransportError(f"{field} persists a forbidden URL")


def _parse_canonical_json(payload: bytes, *, field: str) -> dict[str, Any]:
    if not isinstance(payload, bytes):
        raise SourceTransportError(f"{field} is not strict UTF-8 JSON")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceTransportError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceTransportError(f"{field} is not canonical JSON")
    _reject_persisted_url(payload, field=field)
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SourceTransportError(f"{field} must be a non-empty string")
    return value


def _require_id(value: object, *, field: str, pattern: re.Pattern[str]) -> str:
    text = _require_string(value, field=field)
    if not pattern.fullmatch(text):
        raise SourceTransportError(f"{field} has an unsafe format")
    return text


def _require_positive_int(value: object, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceTransportError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise SourceTransportError(f"{field} exceeds its configured maximum")
    return value


def _require_absolute_path(value: object, *, field: str) -> Path:
    path = value if isinstance(value, Path) else Path(_require_string(value, field=field))
    if not path.is_absolute():
        raise SourceTransportError(f"{field} must be an absolute path")
    return path


def _validate_prefix(value: object) -> str:
    prefix = _require_string(value, field="prefix").strip("/")
    if not prefix or any(not PREFIX_COMPONENT_RE.fullmatch(component) for component in prefix.split("/")):
        raise SourceTransportError("prefix must consist of safe non-empty object-key components")
    return prefix


def derive_region_from_endpoint(endpoint: object) -> tuple[str, str]:
    """Return the canonical HTTPS origin and its only permitted Arvan region.

    The endpoint is an operator-provided controller config value.  Treating a
    separate region value as authoritative would let those two values drift,
    which in turn could produce a signed request for a different S3 target.
    A single exact endpoint grammar keeps both derived values deterministic.
    """

    endpoint_text = _require_string(endpoint, field="endpoint")
    match = ARVAN_S3_ENDPOINT_RE.fullmatch(endpoint_text)
    if match is None:
        raise SourceTransportError("endpoint must be the canonical HTTPS Arvan S3 endpoint")
    region = match.group(1)
    return f"https://s3.{region}.arvanstorage.ir", region


def _validate_endpoint(endpoint: object, region: object) -> tuple[str, str]:
    endpoint_text, derived_region = derive_region_from_endpoint(endpoint)
    region_text = _require_string(region, field="region")
    if region_text != derived_region:
        raise SourceTransportError("region must be derived exactly from the Arvan S3 endpoint")
    return endpoint_text.rstrip("/"), region_text


def _require_age_recipient(value: object, *, field: str) -> str:
    return _require_id(value, field=field, pattern=AGE_RECIPIENT_RE)


def source_transport_workspace_for_campaign(campaign_id: object) -> Path:
    """Return the fixed, host-local source workspace for one valid campaign.

    This is intentionally a pure path derivation.  A caller creates the
    directory only when it performs an operation that needs a workspace.
    """

    campaign = _require_id(campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    return SOURCE_TRANSPORT_WORKSPACE_ROOT / campaign


def validate_policy(config: SourceTransportPolicy) -> SourceTransportPolicy:
    """Validate a non-secret policy without opening or creating a path."""

    if not isinstance(config, SourceTransportPolicy):
        raise SourceTransportError("source transport policy has an unsupported type")
    endpoint, region = _validate_endpoint(config.endpoint, config.region)
    bucket = _require_id(config.bucket, field="bucket", pattern=BUCKET_RE)
    prefix = _validate_prefix(config.prefix)
    age_binary = _require_string(config.age_binary, field="age_binary")
    if not age_binary.startswith("/"):
        raise SourceTransportError("age_binary must be an absolute path")
    workspace = _require_absolute_path(config.workspace, field="workspace")
    maximum_plaintext_bytes = _require_positive_int(
        config.maximum_plaintext_bytes,
        field="maximum_plaintext_bytes",
        maximum=100 * 1024 * 1024 * 1024,
    )
    controller = _require_age_recipient(config.controller_age_recipient, field="controller_age_recipient")
    webapp_fi = _require_age_recipient(config.webapp_fi_age_recipient, field="webapp_fi_age_recipient")
    webapp_ir = _require_age_recipient(config.webapp_ir_age_recipient, field="webapp_ir_age_recipient")
    if len({controller, webapp_fi, webapp_ir}) != 3:
        raise SourceTransportError("configured age recipients must be distinct public keys")
    return SourceTransportPolicy(
        endpoint=endpoint,
        region=region,
        bucket=bucket,
        prefix=prefix,
        age_binary=age_binary,
        workspace=workspace,
        controller_age_recipient=controller,
        webapp_fi_age_recipient=webapp_fi,
        webapp_ir_age_recipient=webapp_ir,
        maximum_plaintext_bytes=maximum_plaintext_bytes,
    )


# Keep the internal spelling available for an incremental refactor of the
# existing publisher without duplicating validation rules.
_validate_policy = validate_policy


def _recipient_for_single_destination(config: SourceTransportPolicy, destination_site: str) -> str:
    recipients = {
        "controller": config.controller_age_recipient,
        "webapp_fi": config.webapp_fi_age_recipient,
        "webapp_ir": config.webapp_ir_age_recipient,
    }
    try:
        return recipients[destination_site]
    except KeyError as exc:
        raise SourceTransportError("single-recipient destination_site is unsupported") from exc


def resolve_recipients(config: SourceTransportPolicy, request: SourceObjectRequest) -> tuple[str, ...]:
    """Return only the canonical public-recipient tuple for a typed object."""

    config = validate_policy(config)
    if not isinstance(request, SourceObjectRequest):
        raise SourceTransportError("source object request has an unsupported type")
    if isinstance(request.recipients, (str, bytes)) or not isinstance(request.recipients, Sequence):
        raise SourceTransportError("recipients must be an ordered sequence of public age recipients")
    supplied = tuple(_require_age_recipient(item, field="recipient") for item in request.recipients)
    if request.mode == STATIC_MODE:
        expected = (config.controller_age_recipient, config.webapp_ir_age_recipient)
        if request.destination_site != STATIC_DESTINATION_SITE or supplied != expected:
            raise SourceTransportError(
                "static transport requires exactly the pinned controller and WebApp-IR recipients in canonical order"
            )
        return expected
    if request.mode == SINGLE_MODE:
        expected = (_recipient_for_single_destination(config, request.destination_site),)
        if supplied != expected:
            raise SourceTransportError("single transport requires exactly the pinned destination recipient")
        return expected
    raise SourceTransportError("source transport mode is unsupported")


def validate_request(config: SourceTransportPolicy, request: SourceObjectRequest) -> tuple[str, ...]:
    """Validate one of the five permitted directions and return its pins."""

    if not isinstance(request, SourceObjectRequest):
        raise SourceTransportError("source object request has an unsupported type")
    _require_id(request.campaign_id, field="campaign_id", pattern=CAMPAIGN_RE)
    _require_id(request.release_sha, field="release_sha", pattern=GIT_SHA_RE)
    _require_id(request.control_commit, field="control_commit", pattern=GIT_SHA_RE)
    _require_id(request.control_tree, field="control_tree", pattern=GIT_SHA_RE)
    source_site = _require_id(request.source_site, field="source_site", pattern=SITE_RE)
    destination_site = _require_id(request.destination_site, field="destination_site", pattern=SITE_RE)
    if source_site == destination_site:
        raise SourceTransportError("source_site and destination_site must differ")
    object_kind = _require_id(request.object_kind, field="object_kind", pattern=OBJECT_KIND_RE)
    _require_id(request.object_id, field="object_id", pattern=OBJECT_ID_RE)
    expected_mode = _ALLOWED_DIRECTIONS.get((source_site, destination_site, object_kind))
    if expected_mode is None or request.mode != expected_mode:
        raise SourceTransportError("source transport direction, object kind, or recipient mode is unsupported")
    return resolve_recipients(config, request)


def source_object_key(config: SourceTransportPolicy, request: SourceObjectRequest) -> str:
    """Construct a deterministic key bound to release and control revisions."""

    config = validate_policy(config)
    validate_request(config, request)
    return "/".join(
        (
            config.prefix,
            "webapp-fi-source-transport",
            OBJECT_LAYOUT_VERSION,
            request.campaign_id,
            request.release_sha,
            request.control_commit,
            request.control_tree,
            request.source_site,
            request.destination_site,
            request.object_kind,
            request.object_id + ".age",
        )
    )


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceTransportError(f"{field} is invalid")
    return value


def _require_version_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or value.lower() == "null" or not VERSION_ID_RE.fullmatch(value):
        raise SourceTransportError(f"{field} is invalid")
    return value


def _require_size(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceTransportError(f"{field} is invalid")
    return value


def validate_expectation(
    expectation: SourceObjectExpectation,
    *,
    maximum_plaintext_bytes: int,
) -> SourceObjectExpectation:
    """Validate a caller-reported plaintext/ciphertext hash and size pair."""

    if not isinstance(expectation, SourceObjectExpectation):
        raise SourceTransportError("source object expectation has an unsupported type")
    maximum = _require_positive_int(
        maximum_plaintext_bytes,
        field="maximum_plaintext_bytes",
        maximum=100 * 1024 * 1024 * 1024,
    )
    return SourceObjectExpectation(
        plaintext_sha256=_require_sha256(expectation.plaintext_sha256, field="plaintext_sha256"),
        plaintext_bytes=_require_size(
            expectation.plaintext_bytes,
            field="plaintext_bytes",
            maximum=maximum,
        ),
        ciphertext_sha256=_require_sha256(expectation.ciphertext_sha256, field="ciphertext_sha256"),
        ciphertext_bytes=_require_size(
            expectation.ciphertext_bytes,
            field="ciphertext_bytes",
            maximum=maximum + MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        ),
    )


# Historical private spelling retained for the publisher's future import.
_validate_expectation = validate_expectation


def validate_object_descriptor(value: object, *, maximum_plaintext_bytes: int) -> dict[str, Any]:
    """Normalize the URL-free exact-VersionId descriptor used in receipts."""

    maximum = _require_positive_int(
        maximum_plaintext_bytes,
        field="maximum_plaintext_bytes",
        maximum=100 * 1024 * 1024 * 1024,
    )
    expected = {
        "object_key",
        "version_id",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_sha256",
        "plaintext_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise SourceTransportError("source transport object descriptor is invalid")
    key = value.get("object_key")
    if not isinstance(key, str) or not OBJECT_KEY_RE.fullmatch(key):
        raise SourceTransportError("source transport object descriptor is invalid")
    return {
        "object_key": key,
        "version_id": _require_version_id(value.get("version_id"), field="source transport object version ID"),
        "ciphertext_sha256": _require_sha256(
            value.get("ciphertext_sha256"), field="source transport object ciphertext SHA-256"
        ),
        "ciphertext_bytes": _require_size(
            value.get("ciphertext_bytes"),
            field="source transport object ciphertext bytes",
            maximum=maximum + MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        ),
        "plaintext_sha256": _require_sha256(
            value.get("plaintext_sha256"), field="source transport object plaintext SHA-256"
        ),
        "plaintext_bytes": _require_size(
            value.get("plaintext_bytes"),
            field="source transport object plaintext bytes",
            maximum=maximum,
        ),
    }


_object_descriptor = validate_object_descriptor


def required_upload_headers(*, expectation: SourceObjectExpectation, mode: str) -> dict[str, str]:
    """Return the exact no-SSE header set for a create-only direct PUT."""

    expected = validate_expectation(expectation, maximum_plaintext_bytes=MAXIMUM_PLAINTEXT_BYTES)
    if mode not in {STATIC_MODE, SINGLE_MODE}:
        raise SourceTransportError("source transport mode is unsupported")
    return {
        "content-type": "application/octet-stream",
        "if-none-match": "*",
        "x-amz-meta-transport-schema": TRANSPORT_SCHEMA,
        "x-amz-meta-encryption": OBJECT_ENCRYPTION,
        "x-amz-meta-ciphertext-sha256": expected.ciphertext_sha256,
        "x-amz-meta-recipient-mode": mode,
    }


_required_upload_headers = required_upload_headers


def _receipt_without_hash(
    *,
    request: SourceObjectRequest,
    recipients: Sequence[str],
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": TRANSPORT_SCHEMA,
        "status": "published",
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "control_commit": request.control_commit,
        "control_tree": request.control_tree,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "object_kind": request.object_kind,
        "object_id": request.object_id,
        "recipient_mode": request.mode,
        "recipients": list(recipients),
        "transport": {
            "encryption": OBJECT_ENCRYPTION,
            "create_only": True,
            "private_bucket": True,
            "provider_side_sse": False,
            "read_back_same_version_id": True,
        },
        "object": dict(descriptor),
    }


def build_publish_receipt(
    *,
    config: SourceTransportPolicy,
    request: SourceObjectRequest,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a canonical URL-free receipt for an already verified object."""

    config = validate_policy(config)
    recipients = validate_request(config, request)
    normalized = validate_object_descriptor(
        descriptor,
        maximum_plaintext_bytes=config.maximum_plaintext_bytes,
    )
    if normalized["object_key"] != source_object_key(config, request):
        raise SourceTransportError("source transport object descriptor is not bound to its typed request")
    unsigned = _receipt_without_hash(request=request, recipients=recipients, descriptor=normalized)
    return {**unsigned, "receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def verify_publish_receipt(*, config: SourceTransportPolicy, payload: bytes) -> dict[str, Any]:
    """Verify strict, canonical, URL-free receipt content without I/O."""

    config = validate_policy(config)
    value = _parse_canonical_json(payload, field="source transport publish receipt")
    expected = {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "control_commit",
        "control_tree",
        "source_site",
        "destination_site",
        "object_kind",
        "object_id",
        "recipient_mode",
        "recipients",
        "transport",
        "object",
        "receipt_sha256",
    }
    if set(value) != expected or value.get("schema") != TRANSPORT_SCHEMA or value.get("status") != "published":
        raise SourceTransportError("source transport publish receipt is unsupported")
    recipients_value = value.get("recipients")
    if not isinstance(recipients_value, list):
        raise SourceTransportError("source transport publish receipt recipients are invalid")
    request = SourceObjectRequest(
        campaign_id=value.get("campaign_id"),
        release_sha=value.get("release_sha"),
        control_commit=value.get("control_commit"),
        control_tree=value.get("control_tree"),
        source_site=value.get("source_site"),
        destination_site=value.get("destination_site"),
        object_kind=value.get("object_kind"),
        object_id=value.get("object_id"),
        mode=value.get("recipient_mode"),
        recipients=tuple(recipients_value),
    )
    recipients = validate_request(config, request)
    transport = value.get("transport")
    if transport != {
        "encryption": OBJECT_ENCRYPTION,
        "create_only": True,
        "private_bucket": True,
        "provider_side_sse": False,
        "read_back_same_version_id": True,
    }:
        raise SourceTransportError("source transport publish receipt transport policy is unsupported")
    descriptor = validate_object_descriptor(
        value.get("object"),
        maximum_plaintext_bytes=config.maximum_plaintext_bytes,
    )
    if descriptor["object_key"] != source_object_key(config, request):
        raise SourceTransportError("source transport publish receipt object key is not bound to its typed request")
    unsigned = _receipt_without_hash(request=request, recipients=recipients, descriptor=descriptor)
    if value.get("receipt_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise SourceTransportError("source transport publish receipt checksum is invalid")
    return {**unsigned, "receipt_sha256": value["receipt_sha256"]}


def _require_presigned_url(value: object, *, field: str) -> tuple[str, Any]:
    url = _require_string(value, field=field)
    if (
        len(url) > 8192
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in url)
    ):
        raise SourceTransportError(f"{field} has an unsafe format")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as exc:
        raise SourceTransportError(f"{field} has an unsafe format") from exc
    return url, parsed


def _require_exact_presigned_envelope(
    query: Mapping[str, list[str]],
    *,
    policy: SourceTransportPolicy,
    allowed_query: frozenset[str],
    expected_signed_headers: tuple[str, ...],
    field: str,
) -> None:
    """Require a short-lived, typed SigV4 query envelope.

    This checks the parts of the signed request that can be inspected without
    possessing the signing key.  It intentionally rejects a permissive
    signed-header superset: adding a signed header can change the request
    semantics across S3-compatible providers and hides an unexpected
    provider-side encryption instruction.
    """

    if set(query) - allowed_query or not _SIGV4_REQUIRED_QUERY.issubset(query):
        raise SourceTransportError(f"{field} does not contain the exact supported SigV4 envelope")
    if any(len(query[name]) != 1 or not query[name][0] for name in _SIGV4_REQUIRED_QUERY):
        raise SourceTransportError(f"{field} does not contain the exact supported SigV4 envelope")
    if "X-Amz-Security-Token" in query and (
        len(query["X-Amz-Security-Token"]) != 1 or not query["X-Amz-Security-Token"][0]
    ):
        raise SourceTransportError(f"{field} has an invalid SigV4 security token")
    if query["X-Amz-Algorithm"] != ["AWS4-HMAC-SHA256"]:
        raise SourceTransportError(f"{field} does not contain the exact supported SigV4 envelope")

    signing_time = query["X-Amz-Date"][0]
    if not AMZ_DATE_RE.fullmatch(signing_time):
        raise SourceTransportError(f"{field} has an invalid signing time")
    try:
        dt.datetime.strptime(signing_time, "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise SourceTransportError(f"{field} has an invalid signing time") from exc

    credential = query["X-Amz-Credential"][0]
    scope = credential.split("/")
    if (
        len(scope) != 5
        or not scope[0]
        or any(not component or any(ord(character) < 0x21 or ord(character) == 0x7F for character in component)
               for component in scope)
        or scope[1] != signing_time[:8]
        or scope[2] != policy.region
        or scope[3] != "s3"
        or scope[4] != "aws4_request"
    ):
        raise SourceTransportError(f"{field} has an invalid SigV4 credential scope")

    expires = query["X-Amz-Expires"][0]
    if not expires.isascii() or not expires.isdecimal():
        raise SourceTransportError(f"{field} has an invalid expiry")
    seconds = int(expires)
    if not MINIMUM_PRESIGNED_URL_SECONDS <= seconds <= MAXIMUM_PRESIGNED_URL_SECONDS:
        raise SourceTransportError(f"{field} expiry is not short-lived")
    if not SIGV4_SIGNATURE_RE.fullmatch(query["X-Amz-Signature"][0]):
        raise SourceTransportError(f"{field} has an invalid SigV4 signature")

    signed_headers = tuple(query["X-Amz-SignedHeaders"][0].split(";"))
    if signed_headers != expected_signed_headers:
        raise SourceTransportError(f"{field} signed headers are not the exact required no-SSE set")


def _validate_presigned_url_base(
    value: object,
    *,
    policy: SourceTransportPolicy,
    object_key: str,
    allowed_query: frozenset[str],
    expected_signed_headers: tuple[str, ...],
    field: str,
) -> tuple[str, Mapping[str, list[str]]]:
    config = validate_policy(policy)
    expected_key = _require_string(object_key, field="object_key")
    if not OBJECT_KEY_RE.fullmatch(expected_key):
        raise SourceTransportError("object_key has an unsafe format")
    url, parsed = _require_presigned_url(value, field=field)
    try:
        endpoint = urlsplit(config.endpoint)
        parsed_port = parsed.port
    except ValueError as exc:
        raise SourceTransportError(f"{field} has an unsafe format") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != endpoint.hostname
        or parsed_port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SourceTransportError(f"{field} is not bound to the configured private Object Storage endpoint")
    expected_path = "/" + quote(config.bucket, safe="") + "/" + quote(expected_key, safe="/")
    if parsed.path != expected_path:
        raise SourceTransportError(f"{field} path is not bound to the exact immutable object key")
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise SourceTransportError(f"{field} has an unsafe format") from exc
    _require_exact_presigned_envelope(
        query,
        policy=config,
        allowed_query=allowed_query,
        expected_signed_headers=expected_signed_headers,
        field=field,
    )
    return url, query


def require_create_only_presigned_put_url(
    value: object,
    *,
    policy: SourceTransportPolicy,
    object_key: str,
) -> str:
    """Validate one short-lived create-only PUT URL for a new object key."""

    url, query = _validate_presigned_url_base(
        value,
        policy=policy,
        object_key=object_key,
        allowed_query=_SIGV4_REQUIRED_QUERY | _SIGV4_OPTIONAL_QUERY | frozenset({"versionId"}),
        expected_signed_headers=_PUT_SIGNED_HEADERS,
        field="presigned upload URL",
    )
    if "versionId" in query or "VersionId" in query:
        raise SourceTransportError("presigned upload URL must not target a pre-existing object version")
    return url


def require_version_bound_presigned_get_url(
    value: object,
    *,
    policy: SourceTransportPolicy,
    object_key: str,
    version_id: str,
) -> str:
    """Validate one short-lived GET URL bound to exactly one VersionId."""

    exact_version = _require_version_id(version_id, field="VersionId")
    url, query = _validate_presigned_url_base(
        value,
        policy=policy,
        object_key=object_key,
        allowed_query=_SIGV4_REQUIRED_QUERY | _SIGV4_OPTIONAL_QUERY | frozenset({"versionId"}),
        expected_signed_headers=_GET_SIGNED_HEADERS,
        field="presigned download URL",
    )
    if "VersionId" in query or query.get("versionId") != [exact_version]:
        raise SourceTransportError("presigned download URL must bind exactly one matching VersionId")
    return url
