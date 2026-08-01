"""Fail-closed exact-version pull adapter for encrypted physical recovery objects.

This module is deliberately a *receiver-side transport adapter*, not an
Object-Storage deployment helper.  It has no environment-variable fallback,
does not create an SDK client, does not load credentials, and never lists a
bucket or follows an URL.  A future root-owned receiver bootstrap must inject
both a validated :class:`RootOwnedArvanExactVersionPullConfig` and its scoped
S3-compatible client factory explicitly.

The reader accepts only an already pinned ``Key + VersionId`` expectation.
Before any bytes are accepted it requires the canonical Arvan HTTPS origin,
the matching region, an exact private-bucket name, a safe immutable key and
version ID, exact response identity/metadata, no visible provider-side
encryption or redirect field, and a bounded streamed hash/length match.  Its
return type is the exact ``PhysicalWalExactVersionReadback`` type consumed by
``physical_wal_receiver_staging``.  Blob receiver code can use the same
four-field receipt when it gains its own receiver staging boundary.

This boundary intentionally does **not** implement age decryption,
PostgreSQL recovery/replay, credential installation, bucket preflight,
Witness/term authority, or runtime orchestration.  A successful exact GET is
therefore never a replay, promotion, or remote-durable acknowledgement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
import re
import stat
from types import MappingProxyType
from typing import Any, Protocol

from core.append_only_sync_delta_batch import OBJECT_KEY_RE, SHA256_RE, VERSION_ID_RE
from core.physical_wal_receiver_staging import PhysicalWalExactVersionReadback


__all__ = (
    "ARVAN_EXACT_VERSION_PULL_DEFAULT_ENABLED",
    "ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES",
    "ARVAN_EXACT_VERSION_PULL_SCHEMA",
    "ArvanExactVersionPullClient",
    "ArvanExactVersionPullClientFactory",
    "ArvanExactVersionPullError",
    "ArvanExactVersionPullExpectation",
    "ArvanExactVersionPullReader",
    "RootOwnedArvanExactVersionPullConfig",
    "validate_arvan_exact_version_pull_config",
)


ARVAN_EXACT_VERSION_PULL_SCHEMA = "gold-trade-physical-arvan-exact-version-pull-v1"
ARVAN_EXACT_VERSION_PULL_DEFAULT_ENABLED = False
# This ceiling permits the currently known encrypted image and physical-
# recovery artifacts while ensuring a malicious response cannot turn one GET
# into an unbounded local write.  A deployment cannot widen it by config.
ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES = 100 * 1024 * 1024 * 1024

_READ_CHUNK_BYTES = 256 * 1024
_MAX_EXPECTED_METADATA_FIELDS = 32
_MAX_METADATA_VALUE_BYTES = 1024
_MAX_RESPONSE_SCAN_DEPTH = 32
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_ENDPOINT_RE = re.compile(
    r"^https://s3\.([a-z0-9][a-z0-9-]{0,62})\.arvanstorage\.ir/?$",
    re.ASCII,
)
# Physical Blob v2 keys intentionally contain the witnessed-term component:
# ``term-<20-digit-epoch>-<64-hex-lease>-<64-hex-proof>``.  That component is
# 155 characters, so the safe component grammar must not accidentally reject
# a valid physical-recovery key merely because it is longer than an ordinary
# campaign identifier.
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,255}$", re.ASCII)
_METADATA_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)
_MUTABLE_ALIASES = frozenset({"alias", "current", "head", "latest", "pointer"})
_VERSION_ALIASES = _MUTABLE_ALIASES | frozenset({"null", "undefined"})
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+|access[_ -]?key|authorization|credential|password|"
    r"private[_ -]?key|secret|token)"
)
_URL_VALUE_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)")


class ArvanExactVersionPullError(ValueError):
    """An Arvan exact-version pull request is unsafe or unverifiable.

    Error codes deliberately contain no endpoint, object key, response body,
    SDK exception text, credential, or token material.
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanExactVersionPullConfig:
    """Non-secret policy a root-owned bootstrap injects explicitly.

    This value intentionally contains no access key, secret, session token,
    presigned URL, proxy URL, or client object.  The deployment/bootstrap that
    constructs it must be root-owned; this pure adapter neither reads files
    nor falls back to environment variables.
    """

    schema: str = ARVAN_EXACT_VERSION_PULL_SCHEMA
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    maximum_ciphertext_bytes: int = ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES
    enabled: bool = ARVAN_EXACT_VERSION_PULL_DEFAULT_ENABLED
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class ArvanExactVersionPullExpectation:
    """One signed receiver plan's immutable encrypted-object expectation."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]


class ArvanExactVersionPullClient(Protocol):
    """Minimal injected S3-compatible read client; listing is absent by design."""

    def get_object(self, *, Bucket: str, Key: str, VersionId: str) -> Mapping[str, Any]: ...


class ArvanExactVersionPullClientFactory(Protocol):
    """Create a scoped S3 client only for the already validated origin/region."""

    def __call__(self, *, endpoint: str, region: str) -> ArvanExactVersionPullClient: ...


@dataclass(frozen=True)
class _ConfigFacts:
    endpoint: str
    region: str
    bucket: str
    maximum_ciphertext_bytes: int


@dataclass(frozen=True)
class _ExpectationFacts:
    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int
    metadata: Mapping[str, str]


def _fail(code: str) -> None:
    raise ArvanExactVersionPullError(code)


def _safe_text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _safe_object_key(value: object, *, code: str) -> str:
    key = _safe_text(value, pattern=OBJECT_KEY_RE, code=code)
    parts = key.split("/")
    if (
        not parts
        or any(
            part in {"", ".", ".."}
            or _COMPONENT_RE.fullmatch(part) is None
            or part.lower() in _MUTABLE_ALIASES
            for part in parts
        )
    ):
        _fail(code)
    return key


def _safe_version_id(value: object, *, code: str) -> str:
    version_id = _safe_text(value, pattern=VERSION_ID_RE, code=code)
    if version_id.lower() in _VERSION_ALIASES:
        _fail(code)
    return version_id


def _is_sensitive_or_url(value: str) -> bool:
    return _SENSITIVE_VALUE_RE.search(value) is not None or _URL_VALUE_RE.search(value) is not None


def _unsafe_transport_key(value: str) -> bool:
    normalized = value.replace("-", "").replace("_", "").lower()
    return (
        normalized.startswith(
            (
                "serversideencryption",
                "sse",
                "kms",
                "bucketkey",
                "xamzserversideencryption",
                "xamzsse",
                "xamzkms",
                "xamzbucketkey",
            )
        )
        or "redirect" in normalized
        or normalized in {"location", "contentlocation", "contentencoding", "transferencoding"}
        or _is_sensitive_or_url(value)
    )


def _safe_metadata(value: object, *, digest: str, size: int, code: str) -> Mapping[str, str]:
    # Requiring a literal dict eliminates surprising ``Mapping`` implementations
    # and provides an unambiguous exact comparison with the S3 response.
    if type(value) is not dict or not value or len(value) > _MAX_EXPECTED_METADATA_FIELDS:
        _fail(code)
    result: dict[str, str] = {}
    for raw_key, raw_item in value.items():
        if type(raw_key) is not str or type(raw_item) is not str:
            _fail(code)
        if (
            _METADATA_KEY_RE.fullmatch(raw_key) is None
            or not raw_item
            or len(raw_item.encode("utf-8")) > _MAX_METADATA_VALUE_BYTES
            or _unsafe_transport_key(raw_key)
            or _is_sensitive_or_url(raw_item)
        ):
            _fail(code)
        result[raw_key] = raw_item
    if (
        result.get("encryption") != "age-v1"
        or result.get("ciphertext-sha256") != digest
        or result.get("ciphertext-bytes") != str(size)
    ):
        _fail(code)
    return MappingProxyType(result)


def _expectation_facts(value: object, *, maximum_bytes: int, code: str) -> _ExpectationFacts:
    if type(value) is not ArvanExactVersionPullExpectation:
        _fail(code)
    object_key = _safe_object_key(value.object_key, code=code)
    version_id = _safe_version_id(value.version_id, code=code)
    digest = _safe_text(value.ciphertext_sha256, pattern=SHA256_RE, code=code)
    size = _positive_int(value.ciphertext_bytes, maximum=maximum_bytes, code=code)
    metadata = _safe_metadata(value.metadata, digest=digest, size=size, code=code)
    return _ExpectationFacts(object_key, version_id, digest, size, metadata)


def validate_arvan_exact_version_pull_config(
    config: RootOwnedArvanExactVersionPullConfig,
) -> RootOwnedArvanExactVersionPullConfig:
    """Validate one explicit non-secret Arvan pull policy without I/O.

    The endpoint is the sole authority for the region: arbitrary hosts,
    ports, paths, queries, fragments, credentials, and region drift are all
    rejected before a client factory can be called.
    """

    if type(config) is not RootOwnedArvanExactVersionPullConfig:
        _fail("ARVAN_PULL_CONFIG_TYPE_INVALID")
    if config.schema != ARVAN_EXACT_VERSION_PULL_SCHEMA:
        _fail("ARVAN_PULL_CONFIG_SCHEMA_INVALID")
    if type(config.endpoint) is not str:
        _fail("ARVAN_PULL_ENDPOINT_INVALID")
    match = _ENDPOINT_RE.fullmatch(config.endpoint)
    if match is None:
        _fail("ARVAN_PULL_ENDPOINT_INVALID")
    endpoint_region = match.group(1)
    if type(config.region) is not str or config.region != endpoint_region:
        _fail("ARVAN_PULL_REGION_INVALID")
    bucket = _safe_text(config.bucket, pattern=_BUCKET_RE, code="ARVAN_PULL_BUCKET_INVALID")
    if _is_sensitive_or_url(bucket):
        _fail("ARVAN_PULL_BUCKET_INVALID")
    maximum_bytes = _positive_int(
        config.maximum_ciphertext_bytes,
        maximum=ARVAN_EXACT_VERSION_PULL_MAX_CIPHERTEXT_BYTES,
        code="ARVAN_PULL_MAXIMUM_BYTES_INVALID",
    )
    if type(config.enabled) is not bool:
        _fail("ARVAN_PULL_ENABLED_INVALID")
    if config.direct_site_control != "forbidden" or config.destination_object_ingest != "pull-only":
        _fail("ARVAN_PULL_DIRECTION_POLICY_INVALID")
    return RootOwnedArvanExactVersionPullConfig(
        schema=ARVAN_EXACT_VERSION_PULL_SCHEMA,
        endpoint=f"https://s3.{endpoint_region}.arvanstorage.ir",
        region=endpoint_region,
        bucket=bucket,
        maximum_ciphertext_bytes=maximum_bytes,
        enabled=config.enabled,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )


def _response_contains_unsafe_transport_field(
    value: object,
    *,
    seen: set[int] | None = None,
    depth: int = 0,
) -> bool:
    """Reject redirects, provider encryption, secret-looking fields and cycles."""

    if depth > _MAX_RESPONSE_SCAN_DEPTH:
        return True
    if type(value) is dict:
        identities = seen if seen is not None else set()
        identity = id(value)
        if identity in identities:
            return True
        identities.add(identity)
        try:
            for raw_key, item in value.items():
                if type(raw_key) is not str:
                    return True
                normalized = raw_key.replace("-", "").replace("_", "").lower()
                if _unsafe_transport_key(raw_key):
                    return True
                if normalized == "httpstatuscode" and (type(item) is not int or item != 200):
                    return True
                if normalized in {"contentencoding", "transferencoding"} and item not in {None, "", "identity"}:
                    return True
                if _response_contains_unsafe_transport_field(item, seen=identities, depth=depth + 1):
                    return True
        except Exception:
            return True
        finally:
            identities.discard(identity)
        return False
    if type(value) in (list, tuple):
        return any(
            _response_contains_unsafe_transport_field(item, seen=seen, depth=depth + 1)
            for item in value
        )
    return False


def _response_metadata(value: object, *, expected: Mapping[str, str]) -> None:
    if type(value) is not dict or len(value) != len(expected):
        _fail("ARVAN_GET_METADATA_MISMATCH")
    actual: dict[str, str] = {}
    for raw_key, raw_item in value.items():
        if type(raw_key) is not str or type(raw_item) is not str:
            _fail("ARVAN_GET_METADATA_MISMATCH")
        if (
            _METADATA_KEY_RE.fullmatch(raw_key) is None
            or not raw_item
            or len(raw_item.encode("utf-8")) > _MAX_METADATA_VALUE_BYTES
            or _unsafe_transport_key(raw_key)
            or _is_sensitive_or_url(raw_item)
        ):
            _fail("ARVAN_GET_METADATA_MISMATCH")
        actual[raw_key] = raw_item
    if actual != dict(expected):
        _fail("ARVAN_GET_METADATA_MISMATCH")


def _require_safe_destination_fd(value: object) -> int:
    if type(value) is not int or value < 0:
        _fail("ARVAN_PULL_DESTINATION_FD_INVALID")
    try:
        metadata = os.fstat(value)
    except OSError:
        raise ArvanExactVersionPullError("ARVAN_PULL_DESTINATION_FD_INVALID") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _fail("ARVAN_PULL_DESTINATION_FD_INVALID")
    return value


def _write_all(destination_fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(destination_fd, payload[offset:])
        except OSError:
            raise ArvanExactVersionPullError("ARVAN_PULL_DESTINATION_WRITE_FAILED") from None
        if type(written) is not int or written <= 0:
            _fail("ARVAN_PULL_DESTINATION_WRITE_FAILED")
        offset += written


class ArvanExactVersionPullReader:
    """A fixed exact-version reader compatible with physical WAL staging.

    ``expectations`` must come from a receiver-verified signed manifest or
    blob mapping.  It is intentionally fixed at construction: callers cannot
    use this object to select ``latest``, list a bucket, or substitute a
    different key/version after validation.
    """

    def __init__(
        self,
        *,
        config: RootOwnedArvanExactVersionPullConfig,
        client_factory: ArvanExactVersionPullClientFactory | Callable[..., object],
        expectations: Sequence[ArvanExactVersionPullExpectation],
    ) -> None:
        validated = validate_arvan_exact_version_pull_config(config)
        if not callable(client_factory):
            _fail("ARVAN_CLIENT_FACTORY_REQUIRED")
        if isinstance(expectations, (str, bytes)) or not isinstance(expectations, Sequence):
            _fail("ARVAN_PULL_EXPECTATIONS_INVALID")
        facts: dict[tuple[str, str], _ExpectationFacts] = {}
        for value in expectations:
            fact = _expectation_facts(
                value,
                maximum_bytes=validated.maximum_ciphertext_bytes,
                code="ARVAN_PULL_EXPECTATION_INVALID",
            )
            key = (fact.object_key, fact.version_id)
            if key in facts:
                _fail("ARVAN_PULL_EXPECTATION_DUPLICATE")
            facts[key] = fact
        if not facts:
            _fail("ARVAN_PULL_EXPECTATIONS_EMPTY")
        self._config = _ConfigFacts(
            endpoint=validated.endpoint,
            region=validated.region,
            bucket=validated.bucket,
            maximum_ciphertext_bytes=validated.maximum_ciphertext_bytes,
        )
        self._enabled = validated.enabled
        self._client_factory = client_factory
        self._expectations = MappingProxyType(facts)

    def read_exact_to_fd(
        self,
        *,
        object_key: str,
        version_id: str,
        destination_fd: int,
    ) -> PhysicalWalExactVersionReadback:
        """GET exactly one pinned version, verify it, and stream it to ``FD``.

        The method deliberately takes no URL, no credentials, no metadata
        override, and no mutable selector.  On failure it may have written a
        partial local file; the receiver must discard that candidate and never
        consume it as a receipt.
        """

        if self._enabled is not True:
            _fail("ARVAN_PULL_DISABLED")
        key = _safe_object_key(object_key, code="ARVAN_PULL_OBJECT_SELECTOR_INVALID")
        version = _safe_version_id(version_id, code="ARVAN_PULL_OBJECT_SELECTOR_INVALID")
        destination = _require_safe_destination_fd(destination_fd)
        expected = self._expectations.get((key, version))
        if expected is None:
            _fail("ARVAN_PULL_OBJECT_NOT_PINNED")
        try:
            client = self._client_factory(endpoint=self._config.endpoint, region=self._config.region)
        except Exception:
            raise ArvanExactVersionPullError("ARVAN_CLIENT_FACTORY_FAILED") from None
        get_object = getattr(client, "get_object", None)
        if not callable(get_object):
            _fail("ARVAN_GET_UNAVAILABLE")
        try:
            response = get_object(
                Bucket=self._config.bucket,
                Key=expected.object_key,
                VersionId=expected.version_id,
            )
        except Exception:
            raise ArvanExactVersionPullError("ARVAN_GET_FAILED") from None
        if type(response) is not dict or _response_contains_unsafe_transport_field(response):
            _fail("ARVAN_GET_RESPONSE_UNSAFE")
        if (
            type(response.get("VersionId")) is not str
            or response.get("VersionId") != expected.version_id
            or type(response.get("ContentLength")) is not int
            or response.get("ContentLength") != expected.ciphertext_bytes
        ):
            _fail("ARVAN_GET_IDENTITY_MISMATCH")
        if "Key" in response and (type(response["Key"]) is not str or response["Key"] != expected.object_key):
            _fail("ARVAN_GET_IDENTITY_MISMATCH")
        _response_metadata(response.get("Metadata"), expected=expected.metadata)
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            _fail("ARVAN_GET_BODY_INVALID")
        close = getattr(body, "close", None)
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                try:
                    chunk = body.read(_READ_CHUNK_BYTES)
                except Exception:
                    raise ArvanExactVersionPullError("ARVAN_GET_BODY_FAILED") from None
                if type(chunk) is not bytes:
                    _fail("ARVAN_GET_BODY_INVALID")
                if not chunk:
                    break
                if total + len(chunk) > expected.ciphertext_bytes:
                    _fail("ARVAN_GET_SIZE_MISMATCH")
                _write_all(destination, chunk)
                digest.update(chunk)
                total += len(chunk)
        except BaseException:
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            raise
        if callable(close):
            try:
                close()
            except Exception:
                raise ArvanExactVersionPullError("ARVAN_GET_BODY_CLOSE_FAILED") from None
        if total != expected.ciphertext_bytes:
            _fail("ARVAN_GET_SIZE_MISMATCH")
        if digest.hexdigest() != expected.ciphertext_sha256:
            _fail("ARVAN_GET_HASH_MISMATCH")
        return PhysicalWalExactVersionReadback(
            object_key=expected.object_key,
            version_id=expected.version_id,
            ciphertext_sha256=expected.ciphertext_sha256,
            ciphertext_bytes=expected.ciphertext_bytes,
        )
