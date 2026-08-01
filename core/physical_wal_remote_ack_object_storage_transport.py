"""Fail-closed encrypted Object-Storage transport for physical-WAL remote ack.

This module carries the *already signed* physical-WAL remote-ack request and
durable receiver-ledger receipt through private versioned Object Storage.  It
does not poll Witness, discover an object, list a bucket, use a presigned URL,
contact a peer directly, perform PostgreSQL recovery, or mint a receipt.  A
future runtime must deliver fresh Witness-signed locators and invoke the
receiver recovery/ledger boundary separately.

Every Object is age-v1 encrypted and published conditionally.  Receivers use
only the exact ``Key + VersionId`` embedded in a verified Witness locator.
The locator itself is canonical Ed25519-signed evidence; it binds the remote
ack route/lineage/term/frontier, request digest/Object version, and (for the
return path) receipt digest/Object version.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

import core.physical_wal_remote_ack as _ack
from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_remote_ack import (
    MAX_PHYSICAL_WAL_REMOTE_ACK_AGE_SECONDS,
    MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
    MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS,
    PhysicalWalRemoteAckBinding,
    PhysicalWalRemoteAckError,
    VerifiedPhysicalWalRemoteAckEvidence,
    VerifiedPhysicalWalRemoteAckRequest,
    require_verified_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
    verify_physical_wal_remote_ack_request,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PhysicalWalRemoteAckReceiverLedgerResult,
)


__all__ = (
    "PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_DEFAULT_ENABLED",
    "PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_LOCATOR_SCHEMA",
    "PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_SCHEMA",
    "PhysicalWalRemoteAckAgeDecryptor",
    "PhysicalWalRemoteAckAgeEncryptor",
    "PhysicalWalRemoteAckObjectStorageClient",
    "PhysicalWalRemoteAckObjectStorageTransport",
    "PhysicalWalRemoteAckObjectStorageTransportConfig",
    "PhysicalWalRemoteAckObjectStorageTransportError",
    "PhysicalWalRemoteAckReceiptPublication",
    "PhysicalWalRemoteAckRequestPublication",
    "PhysicalWalRemoteAckTransportLocator",
    "VerifiedPhysicalWalRemoteAckReceiptLocator",
    "VerifiedPhysicalWalRemoteAckRequestLocator",
    "build_physical_wal_remote_ack_receipt_locator",
    "build_physical_wal_remote_ack_request_locator",
    "require_verified_physical_wal_remote_ack_receipt_locator",
    "require_verified_physical_wal_remote_ack_request_locator",
    "verify_physical_wal_remote_ack_receipt_locator",
    "verify_physical_wal_remote_ack_request_locator",
)


PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_SCHEMA = (
    "gold-trade-physical-wal-remote-ack-object-storage-transport-v1"
)
PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_LOCATOR_SCHEMA = (
    "gold-trade-physical-wal-remote-ack-object-storage-locator-v1"
)
PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_DEFAULT_ENABLED = False
PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_VERSION = 1
MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES = (
    MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES + 4 * 1024 * 1024
)
MAX_PHYSICAL_WAL_REMOTE_ACK_LOCATOR_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_REMOTE_ACK_LOCATOR_LIFETIME_SECONDS = 90

_AGE_HEADER = b"age-encryption.org/v1\n"
_READ_CHUNK_BYTES = 256 * 1024
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_B64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$", re.ASCII)
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,255}$", re.ASCII)
_MUTABLE_COMPONENTS = frozenset({"alias", "current", "head", "latest", "pointer"})
_MUTABLE_VERSIONS = _MUTABLE_COMPONENTS | frozenset({"null", "undefined"})
_LOCATOR_CAPABILITY = object()
_PUBLICATION_CAPABILITY = object()


class PhysicalWalRemoteAckObjectStorageTransportError(ValueError):
    """Remote-ack Object-Storage transport input or evidence is unsafe."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PhysicalWalRemoteAckAgeEncryptor(Protocol):
    def encrypt(
        self,
        *,
        recipient: str,
        plaintext_path: Path,
        ciphertext_path: Path,
    ) -> None: ...


class PhysicalWalRemoteAckAgeDecryptor(Protocol):
    def decrypt(
        self,
        *,
        expected_recipient: str,
        ciphertext_path: Path,
        plaintext_path: Path,
    ) -> None: ...


class PhysicalWalRemoteAckObjectStorageClient(Protocol):
    def put_object(self, **request: Any) -> Mapping[str, Any]: ...

    def get_object(self, **request: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PhysicalWalRemoteAckObjectStorageTransportConfig:
    """One root-owned endpoint policy; credentials and SDK clients are injected."""

    workspace: Path | None = None
    bucket: str = ""
    local_site: str = ""
    peer_site: str = ""
    local_age_recipient: str = ""
    peer_age_recipient: str = ""
    enabled: bool = PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_DEFAULT_ENABLED
    maximum_ciphertext_bytes: int = MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


@dataclass(frozen=True)
class _ObjectPin:
    role: str
    object_key: str
    version_id: str
    plaintext_sha256: str
    plaintext_bytes: int
    ciphertext_sha256: str
    ciphertext_bytes: int
    age_recipient: str


@dataclass(frozen=True)
class PhysicalWalRemoteAckRequestPublication:
    """Opaque local proof of one FI request create-only Object publication."""

    source_request: bytes
    verified_request: VerifiedPhysicalWalRemoteAckRequest
    source_age_recipient: str
    object_pin: _ObjectPin
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalRemoteAckReceiptPublication:
    """Opaque local proof of one IR durable-ledger receipt publication."""

    request_publication: PhysicalWalRemoteAckRequestPublication
    destination_receipt: bytes
    verified_evidence: VerifiedPhysicalWalRemoteAckEvidence
    source_age_recipient: str
    object_pin: _ObjectPin
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalRemoteAckTransportLocator:
    """Canonical Witness-signed exact Object locator, never a polling command."""

    signed_locator: bytes
    locator_sha256: str
    kind: str
    locator_id: str
    locator_nonce: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalRemoteAckRequestLocator:
    signed_locator: bytes
    locator_sha256: str
    binding: PhysicalWalRemoteAckBinding
    source_age_recipient: str
    request_object: _ObjectPin
    locator_id: str
    locator_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalWalRemoteAckReceiptLocator:
    signed_locator: bytes
    locator_sha256: str
    binding: PhysicalWalRemoteAckBinding
    source_age_recipient: str
    request_object: _ObjectPin
    receipt_object: _ObjectPin
    locator_id: str
    locator_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ConfigFacts:
    workspace: Path
    bucket: str
    local_site: str
    peer_site: str
    local_age_recipient: str
    peer_age_recipient: str
    maximum_ciphertext_bytes: int


@dataclass(frozen=True)
class _LocatorFacts:
    raw: bytes
    kind: str
    binding: PhysicalWalRemoteAckBinding
    source_age_recipient: str
    request_object: _ObjectPin
    receipt_object: _ObjectPin | None
    locator_id: str
    locator_nonce: str
    issued_at: datetime
    expires_at: datetime
    witness_public_key: bytes


def _fail(code: str) -> None:
    raise PhysicalWalRemoteAckObjectStorageTransportError(code)


def _canonical(value: Mapping[str, Any], *, code: str) -> bytes:
    try:
        return canonical_json_bytes(dict(value))
    except (TypeError, ValueError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("REMOTE_ACK_TRANSPORT_LOCATOR_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    _fail("REMOTE_ACK_TRANSPORT_LOCATOR_JSON_INVALID")


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive_int(value: object, *, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(code)
    return value


def _id(value: object, *, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _recipient(value: object, *, code: str) -> str:
    if type(value) is not str or AGE_RECIPIENT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _safe_object_key(value: object, *, code: str) -> str:
    if type(value) is not str or OBJECT_KEY_RE.fullmatch(value) is None:
        _fail(code)
    parts = value.split("/")
    if (
        not parts
        or any(
            part in {"", ".", ".."}
            or _COMPONENT_RE.fullmatch(part) is None
            or part.lower() in _MUTABLE_COMPONENTS
            for part in parts
        )
    ):
        _fail(code)
    return value


def _version_id(value: object, *, code: str) -> str:
    if type(value) is not str or VERSION_ID_RE.fullmatch(value) is None:
        _fail(code)
    if value.lower() in _MUTABLE_VERSIONS:
        _fail(code)
    return value


def _private_workspace(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute() or ".." in value.parts:
        _fail(code)
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail(code)
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _config_facts(value: object, *, require_enabled: bool) -> _ConfigFacts:
    if type(value) is not PhysicalWalRemoteAckObjectStorageTransportConfig:
        _fail("REMOTE_ACK_TRANSPORT_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("REMOTE_ACK_TRANSPORT_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("REMOTE_ACK_TRANSPORT_DISABLED")
    if (
        type(value.direct_site_control) is not str
        or value.direct_site_control != "forbidden"
        or type(value.destination_object_ingest) is not str
        or value.destination_object_ingest != "pull-only"
    ):
        _fail("REMOTE_ACK_TRANSPORT_DIRECTION_POLICY_INVALID")
    if type(value.bucket) is not str or _BUCKET_RE.fullmatch(value.bucket) is None:
        _fail("REMOTE_ACK_TRANSPORT_BUCKET_INVALID")
    if type(value.local_site) is not str or value.local_site not in WEBAPP_SITES:
        _fail("REMOTE_ACK_TRANSPORT_LOCAL_SITE_INVALID")
    if type(value.peer_site) is not str or value.peer_site not in WEBAPP_SITES or value.peer_site == value.local_site:
        _fail("REMOTE_ACK_TRANSPORT_PEER_SITE_INVALID")
    return _ConfigFacts(
        workspace=_private_workspace(value.workspace, code="REMOTE_ACK_TRANSPORT_WORKSPACE_UNSAFE"),
        bucket=value.bucket,
        local_site=value.local_site,
        peer_site=value.peer_site,
        local_age_recipient=_recipient(
            value.local_age_recipient, code="REMOTE_ACK_TRANSPORT_LOCAL_RECIPIENT_INVALID"
        ),
        peer_age_recipient=_recipient(
            value.peer_age_recipient, code="REMOTE_ACK_TRANSPORT_PEER_RECIPIENT_INVALID"
        ),
        maximum_ciphertext_bytes=_positive_int(
            value.maximum_ciphertext_bytes,
            maximum=MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES,
            code="REMOTE_ACK_TRANSPORT_MAXIMUM_BYTES_INVALID",
        ),
    )


def _binding(value: object) -> PhysicalWalRemoteAckBinding:
    try:
        return _ack._normalise_binding(value, label="remote acknowledgement transport binding")
    except PhysicalWalRemoteAckError:
        _fail("REMOTE_ACK_TRANSPORT_BINDING_INVALID")


def _binding_mapping(value: PhysicalWalRemoteAckBinding) -> dict[str, Any]:
    try:
        return _ack._binding_mapping(value)
    except PhysicalWalRemoteAckError:
        _fail("REMOTE_ACK_TRANSPORT_BINDING_INVALID")


def _route_for_source(config: _ConfigFacts, binding: PhysicalWalRemoteAckBinding) -> None:
    if (
        config.local_site != binding.source_site
        or config.peer_site != binding.destination_site
        or config.peer_age_recipient != binding.destination_age_recipient
    ):
        _fail("REMOTE_ACK_TRANSPORT_SOURCE_ROUTE_MISMATCH")


def _route_for_destination(config: _ConfigFacts, binding: PhysicalWalRemoteAckBinding) -> None:
    if (
        config.local_site != binding.destination_site
        or config.peer_site != binding.source_site
        or config.local_age_recipient != binding.destination_age_recipient
    ):
        _fail("REMOTE_ACK_TRANSPORT_DESTINATION_ROUTE_MISMATCH")


def _term_component(binding: PhysicalWalRemoteAckBinding) -> str:
    term = binding.writer_term
    if (
        type(term.writer_epoch) is not int
        or term.writer_epoch < 1
        or type(term.writer_lease_id) is not str
        or LEASE_ID_RE.fullmatch(term.writer_lease_id) is None
        or type(term.witnessed_term_proof_sha256) is not str
        or SHA256_RE.fullmatch(term.witnessed_term_proof_sha256) is None
    ):
        _fail("REMOTE_ACK_TRANSPORT_BINDING_INVALID")
    return (
        f"term-{term.writer_epoch:020d}-{term.writer_lease_id}-"
        f"{term.witnessed_term_proof_sha256}"
    )


def _object_key(
    *,
    binding: PhysicalWalRemoteAckBinding,
    role: str,
    request_sha256: str,
    receipt_sha256: str | None,
) -> str:
    if role not in {"request", "receipt"}:
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_ROLE_INVALID")
    request_hash = _sha256(request_sha256, code="REMOTE_ACK_TRANSPORT_REQUEST_HASH_INVALID")
    if role == "request":
        if receipt_sha256 is not None:
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_ROLE_INVALID")
        suffix = ("requests", request_hash + ".age")
    else:
        receipt_hash = _sha256(receipt_sha256, code="REMOTE_ACK_TRANSPORT_RECEIPT_HASH_INVALID")
        suffix = ("receipts", request_hash, receipt_hash + ".age")
    key = "/".join(
        (
            "physical-wal-remote-ack-transport-v1",
            binding.source_site,
            binding.destination_site,
            binding.campaign_id,
            binding.release_sha,
            binding.baseline_generation_id,
            _term_component(binding),
            *suffix,
        )
    )
    return _safe_object_key(key, code="REMOTE_ACK_TRANSPORT_OBJECT_KEY_INVALID")


def _metadata(
    *,
    binding: PhysicalWalRemoteAckBinding,
    pin: _ObjectPin,
    request_sha256: str,
    receipt_sha256: str | None,
) -> dict[str, str]:
    result = {
        "transport-schema": PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_SCHEMA,
        "message-role": pin.role,
        "source-site": binding.source_site,
        "destination-site": binding.destination_site,
        "campaign-id": binding.campaign_id,
        "release-sha": binding.release_sha,
        "baseline-generation-id": binding.baseline_generation_id,
        "baseline-manifest-sha256": binding.baseline_manifest_sha256,
        "writer-epoch": str(binding.writer_term.writer_epoch),
        "writer-lease-id": binding.writer_term.writer_lease_id,
        "witnessed-term-proof-sha256": binding.writer_term.witnessed_term_proof_sha256,
        "request-sha256": request_sha256,
        "plaintext-sha256": pin.plaintext_sha256,
        "plaintext-bytes": str(pin.plaintext_bytes),
        "encryption": "age-v1",
        "age-recipient": pin.age_recipient,
        "ciphertext-sha256": pin.ciphertext_sha256,
        "ciphertext-bytes": str(pin.ciphertext_bytes),
    }
    if receipt_sha256 is not None:
        result["receipt-sha256"] = receipt_sha256
    return result


def _safe_response(value: object, *, expected_metadata: Mapping[str, str]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_RESPONSE_INVALID")
    for raw_key, item in value.items():
        if type(raw_key) is not str:
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_RESPONSE_INVALID")
        normalized = raw_key.replace("-", "").replace("_", "").lower()
        if normalized.startswith(("serversideencryption", "sse", "kms", "bucketkey")) or "redirect" in normalized:
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_RESPONSE_INVALID")
        if normalized == "httpstatuscode" and (type(item) is not int or item != 200):
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_RESPONSE_INVALID")
    metadata = value.get("Metadata")
    if type(metadata) is not dict or metadata != dict(expected_metadata):
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_METADATA_MISMATCH")
    return value


def _write_new_private(path: Path, data: bytes, *, maximum_bytes: int, code: str) -> None:
    if not data or len(data) > maximum_bytes or not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError:
        _fail(code)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if type(written) is not int or written <= 0:
                _fail(code)
            view = view[written:]
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(data)
        ):
            _fail(code)
    except OSError:
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _file_hash(path: Path, *, maximum_bytes: int, require_age: bool, code: str) -> tuple[str, int]:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail(code)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
        path_metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or path_metadata.st_dev != metadata.st_dev
            or path_metadata.st_ino != metadata.st_ino
            or stat.S_ISLNK(path_metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > maximum_bytes
        ):
            _fail(code)
        header = os.read(descriptor, len(_AGE_HEADER))
        if require_age and header != _AGE_HEADER:
            _fail("REMOTE_ACK_TRANSPORT_ENCRYPTION_AMBIGUOUS")
        digest = hashlib.sha256()
        total = 0
        if header:
            digest.update(header)
            total += len(header)
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if type(chunk) is not bytes:
                _fail(code)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            digest.update(chunk)
        if total != metadata.st_size:
            _fail(code)
        return digest.hexdigest(), total
    except OSError:
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_private(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    digest, size = _file_hash(path, maximum_bytes=maximum_bytes, require_age=False, code=code)
    del digest
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _fail(code)
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
            chunks.append(chunk)
        raw = b"".join(chunks)
        if len(raw) != size:
            _fail(code)
        return raw
    except OSError:
        _fail(code)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _new_temp_workspace(root: Path) -> tempfile.TemporaryDirectory[str]:
    try:
        temporary = tempfile.TemporaryDirectory(prefix="physical-wal-remote-ack-", dir=str(root))
        path = Path(temporary.name)
        os.chmod(path, 0o700)
        metadata = os.lstat(path)
    except OSError:
        _fail("REMOTE_ACK_TRANSPORT_WORKSPACE_UNSAFE")
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        temporary.cleanup()
        _fail("REMOTE_ACK_TRANSPORT_WORKSPACE_UNSAFE")
    return temporary


def _body_to_new_file(
    *,
    response: Mapping[str, Any],
    pin: _ObjectPin,
    expected_metadata: Mapping[str, str],
    destination: Path,
) -> None:
    value = _safe_response(response, expected_metadata=expected_metadata)
    if (
        type(value.get("VersionId")) is not str
        or value["VersionId"] != pin.version_id
        or type(value.get("ContentLength")) is not int
        or value["ContentLength"] != pin.ciphertext_bytes
        or ("Key" in value and (type(value["Key"]) is not str or value["Key"] != pin.object_key))
    ):
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_IDENTITY_MISMATCH")
    body = value.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_BODY_INVALID")
    close = getattr(body, "close", None)
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            _fail("REMOTE_ACK_TRANSPORT_STAGING_UNSAFE")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                try:
                    chunk = body.read(_READ_CHUNK_BYTES)
                except Exception:
                    _fail("REMOTE_ACK_TRANSPORT_OBJECT_BODY_READ_FAILED")
                if type(chunk) is not bytes:
                    _fail("REMOTE_ACK_TRANSPORT_OBJECT_BODY_INVALID")
                if not chunk:
                    break
                total += len(chunk)
                if total > pin.ciphertext_bytes:
                    _fail("REMOTE_ACK_TRANSPORT_OBJECT_SIZE_MISMATCH")
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if type(written) is not int or written <= 0:
                        _fail("REMOTE_ACK_TRANSPORT_STAGING_UNSAFE")
                    view = view[written:]
                digest.update(chunk)
            os.fsync(descriptor)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if total != pin.ciphertext_bytes or digest.hexdigest() != pin.ciphertext_sha256:
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_READBACK_MISMATCH")
    except OSError:
        _fail("REMOTE_ACK_TRANSPORT_STAGING_UNSAFE")
    finally:
        if callable(close):
            try:
                close()
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_OBJECT_BODY_CLOSE_FAILED")
    _file_hash(
        destination,
        maximum_bytes=pin.ciphertext_bytes,
        require_age=True,
        code="REMOTE_ACK_TRANSPORT_STAGING_UNSAFE",
    )


def _object_pin(
    *,
    role: str,
    object_key: object,
    version_id: object,
    plaintext_sha256: object,
    plaintext_bytes: object,
    ciphertext_sha256: object,
    ciphertext_bytes: object,
    age_recipient: object,
) -> _ObjectPin:
    if role not in {"request", "receipt"}:
        _fail("REMOTE_ACK_TRANSPORT_OBJECT_ROLE_INVALID")
    return _ObjectPin(
        role=role,
        object_key=_safe_object_key(object_key, code="REMOTE_ACK_TRANSPORT_OBJECT_KEY_INVALID"),
        version_id=_version_id(version_id, code="REMOTE_ACK_TRANSPORT_VERSION_INVALID"),
        plaintext_sha256=_sha256(plaintext_sha256, code="REMOTE_ACK_TRANSPORT_PLAINTEXT_HASH_INVALID"),
        plaintext_bytes=_positive_int(
            plaintext_bytes,
            maximum=MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
            code="REMOTE_ACK_TRANSPORT_PLAINTEXT_BYTES_INVALID",
        ),
        ciphertext_sha256=_sha256(ciphertext_sha256, code="REMOTE_ACK_TRANSPORT_CIPHERTEXT_HASH_INVALID"),
        ciphertext_bytes=_positive_int(
            ciphertext_bytes,
            maximum=MAX_PHYSICAL_WAL_REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES,
            code="REMOTE_ACK_TRANSPORT_CIPHERTEXT_BYTES_INVALID",
        ),
        age_recipient=_recipient(age_recipient, code="REMOTE_ACK_TRANSPORT_RECIPIENT_INVALID"),
    )


def _object_mapping(pin: _ObjectPin) -> dict[str, Any]:
    return {
        "role": pin.role,
        "object_key": pin.object_key,
        "version_id": pin.version_id,
        "plaintext_sha256": pin.plaintext_sha256,
        "plaintext_bytes": pin.plaintext_bytes,
        "ciphertext_sha256": pin.ciphertext_sha256,
        "ciphertext_bytes": pin.ciphertext_bytes,
        "encryption": "age-v1",
        "age_recipient": pin.age_recipient,
    }


_OBJECT_FIELDS = frozenset(
    {
        "role",
        "object_key",
        "version_id",
        "plaintext_sha256",
        "plaintext_bytes",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "encryption",
        "age_recipient",
    }
)
_SIGNER_FIELDS = frozenset({"algorithm", "public_key_base64", "key_id"})
_SIGNATURE_FIELDS = frozenset({"algorithm", "signature_base64"})
_REQUEST_LOCATOR_FIELDS = frozenset(
    {
        "schema",
        "version",
        "kind",
        "binding",
        "source_age_recipient",
        "request",
        "locator_id",
        "locator_nonce",
        "issued_at",
        "expires_at",
        "witness_signer",
        "witness_signature",
    }
)
_RECEIPT_LOCATOR_FIELDS = _REQUEST_LOCATOR_FIELDS | frozenset({"receipt"})
_WITNESS_LOCATOR_DOMAIN = b"gold-trade-physical-wal-remote-ack-object-storage-locator-v1\x00"


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _b64(value: object, *, expected_bytes: int, code: str) -> bytes:
    if type(value) is not str or _B64_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception:
        _fail(code)
    if len(result) != expected_bytes:
        _fail(code)
    return result


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(code)
    return value


def _signer_mapping(private: Ed25519PrivateKey) -> tuple[bytes, dict[str, str]]:
    try:
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID")
    public = _public_key(public, code="REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID")
    return public, {
        "algorithm": "ed25519",
        "public_key_base64": base64.b64encode(public).decode("ascii"),
        "key_id": "ed25519-sha256:" + hashlib.sha256(public).hexdigest(),
    }


def _parse_signer(value: object, *, expected_public_key: bytes | None, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNER_FIELDS, code=code)
    if item["algorithm"] != "ed25519":
        _fail(code)
    public = _b64(item["public_key_base64"], expected_bytes=32, code=code)
    public = _public_key(public, code=code)
    if item["key_id"] != "ed25519-sha256:" + hashlib.sha256(public).hexdigest():
        _fail(code)
    if expected_public_key is not None and public != expected_public_key:
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_MISMATCH")
    return public


def _parse_signature(value: object, *, code: str) -> bytes:
    item = _exact_mapping(value, fields=_SIGNATURE_FIELDS, code=code)
    if item["algorithm"] != "ed25519":
        _fail(code)
    return _b64(item["signature_base64"], expected_bytes=64, code=code)


def _parse_object(value: object, *, expected_role: str, expected_recipient: str, code: str) -> _ObjectPin:
    item = _exact_mapping(value, fields=_OBJECT_FIELDS, code=code)
    if item["role"] != expected_role or item["encryption"] != "age-v1" or item["age_recipient"] != expected_recipient:
        _fail(code)
    try:
        return _object_pin(
            role=expected_role,
            object_key=item["object_key"],
            version_id=item["version_id"],
            plaintext_sha256=item["plaintext_sha256"],
            plaintext_bytes=item["plaintext_bytes"],
            ciphertext_sha256=item["ciphertext_sha256"],
            ciphertext_bytes=item["ciphertext_bytes"],
            age_recipient=item["age_recipient"],
        )
    except PhysicalWalRemoteAckObjectStorageTransportError:
        # A locator is independently auditable evidence.  Do not let a
        # malformed nested pin escape as a generic local publication error.
        _fail(code)


def _parse_locator(
    value: object,
    *,
    expected_witness_public_key: bytes | None,
    expected_kind: str | None,
) -> _LocatorFacts:
    if type(value) is PhysicalWalRemoteAckTransportLocator:
        raw = value.signed_locator
    elif type(value) is bytes:
        raw = value
    else:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
    if not raw or len(raw) > MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
    if type(parsed) is not dict or _canonical(parsed, code="REMOTE_ACK_TRANSPORT_LOCATOR_INVALID") != raw:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
    kind = parsed.get("kind")
    if kind == "remote_ack_request_locator":
        item = _exact_mapping(parsed, fields=_REQUEST_LOCATOR_FIELDS, code="REMOTE_ACK_TRANSPORT_LOCATOR_FIELDS_INVALID")
        receipt_object = None
    elif kind == "remote_ack_receipt_locator":
        item = _exact_mapping(parsed, fields=_RECEIPT_LOCATOR_FIELDS, code="REMOTE_ACK_TRANSPORT_LOCATOR_FIELDS_INVALID")
        receipt_object = object()
    else:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_KIND_INVALID")
    if expected_kind is not None and kind != expected_kind:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_ROLE_INVALID")
    if item["schema"] != PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_LOCATOR_SCHEMA or type(item["version"]) is not int or item["version"] != PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_VERSION:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_SCHEMA_INVALID")
    binding = _binding_from_locator(item["binding"])
    source_age_recipient = _recipient(item["source_age_recipient"], code="REMOTE_ACK_TRANSPORT_LOCATOR_RECIPIENT_INVALID")
    request_object = _parse_object(
        item["request"],
        expected_role="request",
        expected_recipient=binding.destination_age_recipient,
        code="REMOTE_ACK_TRANSPORT_LOCATOR_REQUEST_OBJECT_INVALID",
    )
    if request_object.object_key != _object_key(
        binding=binding,
        role="request",
        request_sha256=request_object.plaintext_sha256,
        receipt_sha256=None,
    ):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_REQUEST_OBJECT_INVALID")
    if receipt_object is not None:
        receipt_object = _parse_object(
            item["receipt"],
            expected_role="receipt",
            expected_recipient=source_age_recipient,
            code="REMOTE_ACK_TRANSPORT_LOCATOR_RECEIPT_OBJECT_INVALID",
        )
        if receipt_object.object_key != _object_key(
            binding=binding,
            role="receipt",
            request_sha256=request_object.plaintext_sha256,
            receipt_sha256=receipt_object.plaintext_sha256,
        ):
            _fail("REMOTE_ACK_TRANSPORT_LOCATOR_RECEIPT_OBJECT_INVALID")
    locator_id = _id(item["locator_id"], code="REMOTE_ACK_TRANSPORT_LOCATOR_ID_INVALID")
    locator_nonce = _nonce(item["locator_nonce"], code="REMOTE_ACK_TRANSPORT_LOCATOR_NONCE_INVALID")
    if locator_id == locator_nonce:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_IDENTITY_INVALID")
    issued_at = _timestamp(item["issued_at"], code="REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    expires_at = _timestamp(item["expires_at"], code="REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    if expires_at <= issued_at or expires_at > issued_at + timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_LOCATOR_LIFETIME_SECONDS):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    witness_key = _parse_signer(
        item["witness_signer"],
        expected_public_key=expected_witness_public_key,
        code="REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID",
    )
    signature = _parse_signature(item["witness_signature"], code="REMOTE_ACK_TRANSPORT_WITNESS_SIGNATURE_INVALID")
    unsigned = dict(item)
    del unsigned["witness_signature"]
    try:
        Ed25519PublicKey.from_public_bytes(witness_key).verify(
            signature,
            _WITNESS_LOCATOR_DOMAIN + _canonical(unsigned, code="REMOTE_ACK_TRANSPORT_LOCATOR_INVALID"),
        )
    except (InvalidSignature, ValueError):
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNATURE_INVALID")
    return _LocatorFacts(
        raw=raw,
        kind=kind,
        binding=binding,
        source_age_recipient=source_age_recipient,
        request_object=request_object,
        receipt_object=receipt_object,
        locator_id=locator_id,
        locator_nonce=locator_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        witness_public_key=witness_key,
    )


def _binding_from_locator(value: object) -> PhysicalWalRemoteAckBinding:
    try:
        binding = _ack._binding_from_mapping(value, label="remote acknowledgement transport locator")
        if _ack._binding_mapping(binding) != value:
            raise PhysicalWalRemoteAckError("noncanonical")
        return binding
    except PhysicalWalRemoteAckError:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_BINDING_INVALID")


def _fresh_locator(
    facts: _LocatorFacts,
    *,
    expected_binding: PhysicalWalRemoteAckBinding,
    now: datetime,
    consumed_locator_ids: Collection[str],
    consumed_locator_nonces: Collection[str],
) -> None:
    binding = _binding(expected_binding)
    observed_now = _utc(now, code="REMOTE_ACK_TRANSPORT_LOCATOR_CLOCK_INVALID")
    if facts.binding != binding:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_BINDING_MISMATCH")
    if facts.issued_at > observed_now + timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_FUTURE")
    if facts.issued_at < observed_now - timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_LOCATOR_AGE_SECONDS):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_STALE")
    if facts.expires_at <= observed_now:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_STALE")
    if isinstance(consumed_locator_ids, (str, bytes)) or not isinstance(consumed_locator_ids, Collection):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_REPLAY_SET_INVALID")
    if isinstance(consumed_locator_nonces, (str, bytes)) or not isinstance(consumed_locator_nonces, Collection):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_REPLAY_SET_INVALID")
    ids = frozenset(_id(item, code="REMOTE_ACK_TRANSPORT_LOCATOR_REPLAY_SET_INVALID") for item in consumed_locator_ids)
    nonces = frozenset(_nonce(item, code="REMOTE_ACK_TRANSPORT_LOCATOR_REPLAY_SET_INVALID") for item in consumed_locator_nonces)
    if facts.locator_id in ids or facts.locator_id in nonces or facts.locator_nonce in ids or facts.locator_nonce in nonces:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_REPLAYED")


def _publication_request(value: object, *, now: datetime) -> PhysicalWalRemoteAckRequestPublication:
    if type(value) is not PhysicalWalRemoteAckRequestPublication or value._capability is not _PUBLICATION_CAPABILITY:
        _fail("REMOTE_ACK_TRANSPORT_REQUEST_PUBLICATION_REQUIRED")
    try:
        request = require_verified_physical_wal_remote_ack_request(value.verified_request, now=now)
    except PhysicalWalRemoteAckError:
        _fail("REMOTE_ACK_TRANSPORT_REQUEST_PUBLICATION_INVALID")
    pin = _object_pin(
        role="request",
        object_key=value.object_pin.object_key,
        version_id=value.object_pin.version_id,
        plaintext_sha256=value.object_pin.plaintext_sha256,
        plaintext_bytes=value.object_pin.plaintext_bytes,
        ciphertext_sha256=value.object_pin.ciphertext_sha256,
        ciphertext_bytes=value.object_pin.ciphertext_bytes,
        age_recipient=value.object_pin.age_recipient,
    )
    if (
        value.source_request != request.source_request
        or pin.plaintext_sha256 != hashlib.sha256(request.source_request).hexdigest()
        or pin.plaintext_bytes != len(request.source_request)
        or pin.age_recipient != request.binding.destination_age_recipient
        or pin.object_key
        != _object_key(
            binding=request.binding,
            role="request",
            request_sha256=pin.plaintext_sha256,
            receipt_sha256=None,
        )
        or _recipient(value.source_age_recipient, code="REMOTE_ACK_TRANSPORT_SOURCE_RECIPIENT_INVALID")
        == request.binding.destination_age_recipient
    ):
        _fail("REMOTE_ACK_TRANSPORT_REQUEST_PUBLICATION_INVALID")
    return value


def _publication_receipt(value: object, *, now: datetime) -> PhysicalWalRemoteAckReceiptPublication:
    if type(value) is not PhysicalWalRemoteAckReceiptPublication or value._capability is not _PUBLICATION_CAPABILITY:
        _fail("REMOTE_ACK_TRANSPORT_RECEIPT_PUBLICATION_REQUIRED")
    request_publication = _publication_request(value.request_publication, now=now)
    try:
        evidence = _ack.require_verified_physical_wal_remote_ack_evidence(value.verified_evidence, now=now)
    except PhysicalWalRemoteAckError:
        _fail("REMOTE_ACK_TRANSPORT_RECEIPT_PUBLICATION_INVALID")
    pin = _object_pin(
        role="receipt",
        object_key=value.object_pin.object_key,
        version_id=value.object_pin.version_id,
        plaintext_sha256=value.object_pin.plaintext_sha256,
        plaintext_bytes=value.object_pin.plaintext_bytes,
        ciphertext_sha256=value.object_pin.ciphertext_sha256,
        ciphertext_bytes=value.object_pin.ciphertext_bytes,
        age_recipient=value.object_pin.age_recipient,
    )
    if (
        value.destination_receipt != evidence.destination_receipt
        or evidence.source_request != request_publication.source_request
        or pin.plaintext_sha256 != hashlib.sha256(evidence.destination_receipt).hexdigest()
        or pin.plaintext_bytes != len(evidence.destination_receipt)
        or pin.age_recipient != value.source_age_recipient
        or pin.object_key
        != _object_key(
            binding=evidence.binding,
            role="receipt",
            request_sha256=hashlib.sha256(evidence.source_request).hexdigest(),
            receipt_sha256=pin.plaintext_sha256,
        )
        or value.source_age_recipient != request_publication.source_age_recipient
        or evidence.binding != request_publication.verified_request.binding
    ):
        _fail("REMOTE_ACK_TRANSPORT_RECEIPT_PUBLICATION_INVALID")
    return value


def _make_locator(
    *,
    kind: str,
    request_publication: PhysicalWalRemoteAckRequestPublication,
    receipt_publication: PhysicalWalRemoteAckReceiptPublication | None,
    locator_id: str,
    locator_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> PhysicalWalRemoteAckTransportLocator:
    now = _utc(issued_at, code="REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    request = _publication_request(request_publication, now=now)
    if kind == "remote_ack_request_locator":
        if receipt_publication is not None:
            _fail("REMOTE_ACK_TRANSPORT_LOCATOR_ROLE_INVALID")
    elif kind == "remote_ack_receipt_locator":
        receipt_publication = _publication_receipt(receipt_publication, now=now)
    else:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_ROLE_INVALID")
    identity = _id(locator_id, code="REMOTE_ACK_TRANSPORT_LOCATOR_ID_INVALID")
    nonce = _nonce(locator_nonce, code="REMOTE_ACK_TRANSPORT_LOCATOR_NONCE_INVALID")
    if identity == nonce:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_IDENTITY_INVALID")
    expires = _utc(expires_at, code="REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    if expires <= now or expires > now + timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_LOCATOR_LIFETIME_SECONDS):
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_TIME_INVALID")
    if not isinstance(witness_signer, Ed25519PrivateKey):
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID")
    witness_public_key, signer = _signer_mapping(witness_signer)
    del witness_public_key
    item: dict[str, Any] = {
        "schema": PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_LOCATOR_SCHEMA,
        "version": PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_VERSION,
        "kind": kind,
        "binding": _binding_mapping(request.verified_request.binding),
        "source_age_recipient": request.source_age_recipient,
        "request": _object_mapping(request.object_pin),
        "locator_id": identity,
        "locator_nonce": nonce,
        "issued_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "witness_signer": signer,
    }
    if receipt_publication is not None:
        if receipt_publication.verified_evidence.acknowledged_at > now:
            _fail("REMOTE_ACK_TRANSPORT_LOCATOR_ORDER_INVALID")
        item["receipt"] = _object_mapping(receipt_publication.object_pin)
    try:
        signature = witness_signer.sign(
            _WITNESS_LOCATOR_DOMAIN + _canonical(item, code="REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
        )
    except ValueError:
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID")
    if type(signature) is not bytes or len(signature) != 64:
        _fail("REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID")
    item["witness_signature"] = {
        "algorithm": "ed25519",
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }
    raw = _canonical(item, code="REMOTE_ACK_TRANSPORT_LOCATOR_INVALID")
    result = PhysicalWalRemoteAckTransportLocator(
        signed_locator=raw,
        locator_sha256=hashlib.sha256(raw).hexdigest(),
        kind=kind,
        locator_id=identity,
        locator_nonce=nonce,
    )
    object.__setattr__(result, "_capability", _LOCATOR_CAPABILITY)
    return result


def build_physical_wal_remote_ack_request_locator(
    *,
    request_publication: PhysicalWalRemoteAckRequestPublication,
    locator_id: str,
    locator_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> PhysicalWalRemoteAckTransportLocator:
    """Build a Witness locator for one already published request Object."""

    return _make_locator(
        kind="remote_ack_request_locator",
        request_publication=request_publication,
        receipt_publication=None,
        locator_id=locator_id,
        locator_nonce=locator_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        witness_signer=witness_signer,
    )


def build_physical_wal_remote_ack_receipt_locator(
    *,
    request_publication: PhysicalWalRemoteAckRequestPublication,
    receipt_publication: PhysicalWalRemoteAckReceiptPublication,
    locator_id: str,
    locator_nonce: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> PhysicalWalRemoteAckTransportLocator:
    """Build a separate Witness locator for one durable receipt Object."""

    return _make_locator(
        kind="remote_ack_receipt_locator",
        request_publication=request_publication,
        receipt_publication=receipt_publication,
        locator_id=locator_id,
        locator_nonce=locator_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
        witness_signer=witness_signer,
    )


def verify_physical_wal_remote_ack_request_locator(
    *,
    locator: object,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_locator_ids: Collection[str] = (),
    consumed_locator_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalRemoteAckRequestLocator:
    facts = _parse_locator(
        locator,
        expected_witness_public_key=_public_key(
            expected_witness_public_key, code="REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID"
        ),
        expected_kind="remote_ack_request_locator",
    )
    _fresh_locator(
        facts,
        expected_binding=expected_binding,
        now=now,
        consumed_locator_ids=consumed_locator_ids,
        consumed_locator_nonces=consumed_locator_nonces,
    )
    result = VerifiedPhysicalWalRemoteAckRequestLocator(
        signed_locator=facts.raw,
        locator_sha256=hashlib.sha256(facts.raw).hexdigest(),
        binding=facts.binding,
        source_age_recipient=facts.source_age_recipient,
        request_object=facts.request_object,
        locator_id=facts.locator_id,
        locator_nonce=facts.locator_nonce,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=facts.witness_public_key,
    )
    object.__setattr__(result, "_capability", _LOCATOR_CAPABILITY)
    return result


def verify_physical_wal_remote_ack_receipt_locator(
    *,
    locator: object,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_witness_public_key: bytes,
    now: datetime,
    consumed_locator_ids: Collection[str] = (),
    consumed_locator_nonces: Collection[str] = (),
) -> VerifiedPhysicalWalRemoteAckReceiptLocator:
    facts = _parse_locator(
        locator,
        expected_witness_public_key=_public_key(
            expected_witness_public_key, code="REMOTE_ACK_TRANSPORT_WITNESS_SIGNER_INVALID"
        ),
        expected_kind="remote_ack_receipt_locator",
    )
    _fresh_locator(
        facts,
        expected_binding=expected_binding,
        now=now,
        consumed_locator_ids=consumed_locator_ids,
        consumed_locator_nonces=consumed_locator_nonces,
    )
    if facts.receipt_object is None:
        _fail("REMOTE_ACK_TRANSPORT_LOCATOR_RECEIPT_OBJECT_INVALID")
    result = VerifiedPhysicalWalRemoteAckReceiptLocator(
        signed_locator=facts.raw,
        locator_sha256=hashlib.sha256(facts.raw).hexdigest(),
        binding=facts.binding,
        source_age_recipient=facts.source_age_recipient,
        request_object=facts.request_object,
        receipt_object=facts.receipt_object,
        locator_id=facts.locator_id,
        locator_nonce=facts.locator_nonce,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        witness_public_key=facts.witness_public_key,
    )
    object.__setattr__(result, "_capability", _LOCATOR_CAPABILITY)
    return result


def require_verified_physical_wal_remote_ack_request_locator(
    value: object,
    *,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_witness_public_key: bytes,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckRequestLocator:
    if type(value) is not VerifiedPhysicalWalRemoteAckRequestLocator or value._capability is not _LOCATOR_CAPABILITY:
        _fail("REMOTE_ACK_TRANSPORT_REQUEST_LOCATOR_REQUIRED")
    verified = verify_physical_wal_remote_ack_request_locator(
        locator=value.signed_locator,
        expected_binding=expected_binding,
        expected_witness_public_key=expected_witness_public_key,
        now=now,
    )
    if not _verified_request_locator_matches(value, verified):
        _fail("REMOTE_ACK_TRANSPORT_REQUEST_LOCATOR_TAMPERED")
    return value


def require_verified_physical_wal_remote_ack_receipt_locator(
    value: object,
    *,
    expected_binding: PhysicalWalRemoteAckBinding,
    expected_witness_public_key: bytes,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckReceiptLocator:
    if type(value) is not VerifiedPhysicalWalRemoteAckReceiptLocator or value._capability is not _LOCATOR_CAPABILITY:
        _fail("REMOTE_ACK_TRANSPORT_RECEIPT_LOCATOR_REQUIRED")
    verified = verify_physical_wal_remote_ack_receipt_locator(
        locator=value.signed_locator,
        expected_binding=expected_binding,
        expected_witness_public_key=expected_witness_public_key,
        now=now,
    )
    if not _verified_receipt_locator_matches(value, verified):
        _fail("REMOTE_ACK_TRANSPORT_RECEIPT_LOCATOR_TAMPERED")
    return value


def _strict_pin_matches(value: object, expected: _ObjectPin) -> bool:
    if type(value) is not _ObjectPin:
        return False
    if (
        type(value.role) is not str
        or type(value.object_key) is not str
        or type(value.version_id) is not str
        or type(value.plaintext_sha256) is not str
        or type(value.plaintext_bytes) is not int
        or type(value.ciphertext_sha256) is not str
        or type(value.ciphertext_bytes) is not int
        or type(value.age_recipient) is not str
    ):
        return False
    return value == expected


def _strict_binding_matches(
    value: object,
    expected: PhysicalWalRemoteAckBinding,
) -> bool:
    """Compare by canonical bytes so ``True`` never equals Writer epoch 1."""

    if type(value) is not PhysicalWalRemoteAckBinding:
        return False
    try:
        return _canonical(
            _binding_mapping(value), code="REMOTE_ACK_TRANSPORT_BINDING_INVALID"
        ) == _canonical(
            _binding_mapping(expected), code="REMOTE_ACK_TRANSPORT_BINDING_INVALID"
        )
    except Exception:
        return False


def _verified_request_locator_matches(
    value: VerifiedPhysicalWalRemoteAckRequestLocator,
    expected: VerifiedPhysicalWalRemoteAckRequestLocator,
) -> bool:
    return (
        type(value.signed_locator) is bytes
        and type(value.locator_sha256) is str
        and _strict_binding_matches(value.binding, expected.binding)
        and type(value.source_age_recipient) is str
        and type(value.locator_id) is str
        and type(value.locator_nonce) is str
        and type(value.issued_at) is datetime
        and type(value.expires_at) is datetime
        and type(value.witness_public_key) is bytes
        and _strict_pin_matches(value.request_object, expected.request_object)
        and value == expected
    )


def _verified_receipt_locator_matches(
    value: VerifiedPhysicalWalRemoteAckReceiptLocator,
    expected: VerifiedPhysicalWalRemoteAckReceiptLocator,
) -> bool:
    return (
        type(value.signed_locator) is bytes
        and type(value.locator_sha256) is str
        and _strict_binding_matches(value.binding, expected.binding)
        and type(value.source_age_recipient) is str
        and type(value.locator_id) is str
        and type(value.locator_nonce) is str
        and type(value.issued_at) is datetime
        and type(value.expires_at) is datetime
        and type(value.witness_public_key) is bytes
        and _strict_pin_matches(value.request_object, expected.request_object)
        and _strict_pin_matches(value.receipt_object, expected.receipt_object)
        and value == expected
    )


class PhysicalWalRemoteAckObjectStorageTransport:
    """One local endpoint's injected encrypted request/receipt transport adapter."""

    def __init__(
        self,
        *,
        config: PhysicalWalRemoteAckObjectStorageTransportConfig,
        client_factory: Callable[[], PhysicalWalRemoteAckObjectStorageClient] | None,
        age_encryptor_factory: Callable[[], PhysicalWalRemoteAckAgeEncryptor] | None,
        age_decryptor_factory: Callable[[], PhysicalWalRemoteAckAgeDecryptor] | None,
        expected_source_public_key: bytes,
        expected_destination_public_key: bytes,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._age_encryptor_factory = age_encryptor_factory
        self._age_decryptor_factory = age_decryptor_factory
        self._expected_source_public_key = expected_source_public_key
        self._expected_destination_public_key = expected_destination_public_key

    def _client(self) -> PhysicalWalRemoteAckObjectStorageClient:
        if self._client_factory is None or not callable(self._client_factory):
            _fail("REMOTE_ACK_TRANSPORT_CLIENT_FACTORY_REQUIRED")
        try:
            client = self._client_factory()
        except Exception:
            _fail("REMOTE_ACK_TRANSPORT_CLIENT_FACTORY_FAILED")
        if not callable(getattr(client, "put_object", None)) or not callable(getattr(client, "get_object", None)):
            _fail("REMOTE_ACK_TRANSPORT_CLIENT_INVALID")
        return client

    def _encryptor(self) -> PhysicalWalRemoteAckAgeEncryptor:
        if self._age_encryptor_factory is None or not callable(self._age_encryptor_factory):
            _fail("REMOTE_ACK_TRANSPORT_ENCRYPTOR_FACTORY_REQUIRED")
        try:
            encryptor = self._age_encryptor_factory()
        except Exception:
            _fail("REMOTE_ACK_TRANSPORT_ENCRYPTOR_FACTORY_FAILED")
        if not callable(getattr(encryptor, "encrypt", None)):
            _fail("REMOTE_ACK_TRANSPORT_ENCRYPTOR_INVALID")
        return encryptor

    def _decryptor(self) -> PhysicalWalRemoteAckAgeDecryptor:
        if self._age_decryptor_factory is None or not callable(self._age_decryptor_factory):
            _fail("REMOTE_ACK_TRANSPORT_DECRYPTOR_FACTORY_REQUIRED")
        try:
            decryptor = self._age_decryptor_factory()
        except Exception:
            _fail("REMOTE_ACK_TRANSPORT_DECRYPTOR_FACTORY_FAILED")
        if not callable(getattr(decryptor, "decrypt", None)):
            _fail("REMOTE_ACK_TRANSPORT_DECRYPTOR_INVALID")
        return decryptor

    def _publish(
        self,
        *,
        config: _ConfigFacts,
        binding: PhysicalWalRemoteAckBinding,
        role: str,
        plaintext: bytes,
        recipient: str,
        request_sha256: str,
        receipt_sha256: str | None,
    ) -> _ObjectPin:
        plaintext_hash = _sha256(
            hashlib.sha256(plaintext).hexdigest(), code="REMOTE_ACK_TRANSPORT_PLAINTEXT_HASH_INVALID"
        )
        if len(plaintext) > MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES or not plaintext:
            _fail("REMOTE_ACK_TRANSPORT_PLAINTEXT_INVALID")
        key = _object_key(
            binding=binding,
            role=role,
            request_sha256=request_sha256,
            receipt_sha256=receipt_sha256,
        )
        with _new_temp_workspace(config.workspace) as raw_directory:
            directory = Path(raw_directory)
            plaintext_path = directory / "payload.json"
            ciphertext_path = directory / "payload.age"
            readback_path = directory / "readback.age"
            _write_new_private(
                plaintext_path,
                plaintext,
                maximum_bytes=MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
                code="REMOTE_ACK_TRANSPORT_PLAINTEXT_INVALID",
            )
            encryptor = self._encryptor()
            try:
                encryptor.encrypt(
                    recipient=recipient,
                    plaintext_path=plaintext_path,
                    ciphertext_path=ciphertext_path,
                )
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_ENCRYPTION_FAILED")
            cipher_hash, cipher_bytes = _file_hash(
                ciphertext_path,
                maximum_bytes=config.maximum_ciphertext_bytes,
                require_age=True,
                code="REMOTE_ACK_TRANSPORT_CIPHERTEXT_INVALID",
            )
            pin_without_version = _ObjectPin(
                role=role,
                object_key=key,
                version_id="pending-version-id",
                plaintext_sha256=plaintext_hash,
                plaintext_bytes=len(plaintext),
                ciphertext_sha256=cipher_hash,
                ciphertext_bytes=cipher_bytes,
                age_recipient=recipient,
            )
            metadata = _metadata(
                binding=binding,
                pin=pin_without_version,
                request_sha256=request_sha256,
                receipt_sha256=receipt_sha256,
            )
            client = self._client()
            try:
                with os.fdopen(os.open(ciphertext_path, os.O_RDONLY | os.O_NOFOLLOW), "rb", closefd=True) as handle:
                    response = client.put_object(
                        Bucket=config.bucket,
                        Key=key,
                        Body=handle,
                        ContentLength=cipher_bytes,
                        Metadata=metadata,
                        ContentType="application/octet-stream",
                        IfNoneMatch="*",
                    )
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_CREATE_ONLY_PUT_FAILED")
            if type(response) is not dict:
                _fail("REMOTE_ACK_TRANSPORT_CREATE_ONLY_PUT_FAILED")
            version = _version_id(response.get("VersionId"), code="REMOTE_ACK_TRANSPORT_VERSION_INVALID")
            pin = _ObjectPin(
                role=role,
                object_key=key,
                version_id=version,
                plaintext_sha256=plaintext_hash,
                plaintext_bytes=len(plaintext),
                ciphertext_sha256=cipher_hash,
                ciphertext_bytes=cipher_bytes,
                age_recipient=recipient,
            )
            try:
                response = client.get_object(Bucket=config.bucket, Key=key, VersionId=version)
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_EXACT_READBACK_FAILED")
            _body_to_new_file(
                response=response,
                pin=pin,
                expected_metadata=metadata,
                destination=readback_path,
            )
            readback_hash, readback_bytes = _file_hash(
                readback_path,
                maximum_bytes=config.maximum_ciphertext_bytes,
                require_age=True,
                code="REMOTE_ACK_TRANSPORT_OBJECT_READBACK_MISMATCH",
            )
            if readback_hash != cipher_hash or readback_bytes != cipher_bytes:
                _fail("REMOTE_ACK_TRANSPORT_OBJECT_READBACK_MISMATCH")
            return pin

    def _pull_plaintext(
        self,
        *,
        config: _ConfigFacts,
        binding: PhysicalWalRemoteAckBinding,
        pin: _ObjectPin,
        request_sha256: str,
        receipt_sha256: str | None,
    ) -> bytes:
        if pin.ciphertext_bytes > config.maximum_ciphertext_bytes:
            _fail("REMOTE_ACK_TRANSPORT_OBJECT_TOO_LARGE")
        metadata = _metadata(
            binding=binding,
            pin=pin,
            request_sha256=request_sha256,
            receipt_sha256=receipt_sha256,
        )
        with _new_temp_workspace(config.workspace) as raw_directory:
            directory = Path(raw_directory)
            ciphertext_path = directory / "payload.age"
            plaintext_path = directory / "payload.json"
            client = self._client()
            try:
                response = client.get_object(
                    Bucket=config.bucket,
                    Key=pin.object_key,
                    VersionId=pin.version_id,
                )
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_EXACT_PULL_FAILED")
            _body_to_new_file(
                response=response,
                pin=pin,
                expected_metadata=metadata,
                destination=ciphertext_path,
            )
            decryptor = self._decryptor()
            try:
                decryptor.decrypt(
                    expected_recipient=pin.age_recipient,
                    ciphertext_path=ciphertext_path,
                    plaintext_path=plaintext_path,
                )
            except Exception:
                _fail("REMOTE_ACK_TRANSPORT_DECRYPTION_FAILED")
            plaintext_hash, plaintext_bytes = _file_hash(
                plaintext_path,
                maximum_bytes=MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
                require_age=False,
                code="REMOTE_ACK_TRANSPORT_PLAINTEXT_INVALID",
            )
            if plaintext_hash != pin.plaintext_sha256 or plaintext_bytes != pin.plaintext_bytes:
                _fail("REMOTE_ACK_TRANSPORT_PLAINTEXT_MISMATCH")
            return _read_private(
                plaintext_path,
                maximum_bytes=MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
                code="REMOTE_ACK_TRANSPORT_PLAINTEXT_INVALID",
            )

    def publish_request(
        self,
        *,
        source_request: Mapping[str, Any] | bytes,
        expected_binding: PhysicalWalRemoteAckBinding,
        now: datetime,
    ) -> PhysicalWalRemoteAckRequestPublication:
        config = _config_facts(self._config, require_enabled=True)
        binding = _binding(expected_binding)
        _route_for_source(config, binding)
        try:
            request = verify_physical_wal_remote_ack_request(
                source_request=source_request,
                expected_binding=binding,
                expected_source_public_key=self._expected_source_public_key,
                now=now,
            )
        except PhysicalWalRemoteAckError:
            _fail("REMOTE_ACK_TRANSPORT_SOURCE_REQUEST_INVALID")
        raw = request.source_request
        request_hash = hashlib.sha256(raw).hexdigest()
        pin = self._publish(
            config=config,
            binding=binding,
            role="request",
            plaintext=raw,
            recipient=binding.destination_age_recipient,
            request_sha256=request_hash,
            receipt_sha256=None,
        )
        result = PhysicalWalRemoteAckRequestPublication(
            source_request=raw,
            verified_request=request,
            source_age_recipient=config.local_age_recipient,
            object_pin=pin,
        )
        object.__setattr__(result, "_capability", _PUBLICATION_CAPABILITY)
        _publication_request(result, now=now)
        return result

    def receive_request(
        self,
        *,
        locator: VerifiedPhysicalWalRemoteAckRequestLocator,
        expected_binding: PhysicalWalRemoteAckBinding,
        expected_witness_public_key: bytes,
        now: datetime,
    ) -> VerifiedPhysicalWalRemoteAckRequest:
        binding = _binding(expected_binding)
        config = _config_facts(self._config, require_enabled=True)
        _route_for_destination(config, binding)
        verified_locator = require_verified_physical_wal_remote_ack_request_locator(
            locator,
            expected_binding=binding,
            expected_witness_public_key=expected_witness_public_key,
            now=now,
        )
        if (
            verified_locator.source_age_recipient != config.peer_age_recipient
            or verified_locator.request_object.age_recipient != config.local_age_recipient
        ):
            _fail("REMOTE_ACK_TRANSPORT_REQUEST_LOCATOR_ROUTE_MISMATCH")
        raw = self._pull_plaintext(
            config=config,
            binding=binding,
            pin=verified_locator.request_object,
            request_sha256=verified_locator.request_object.plaintext_sha256,
            receipt_sha256=None,
        )
        try:
            request = verify_physical_wal_remote_ack_request(
                source_request=raw,
                expected_binding=binding,
                expected_source_public_key=self._expected_source_public_key,
                now=now,
            )
        except PhysicalWalRemoteAckError:
            _fail("REMOTE_ACK_TRANSPORT_RECEIVED_REQUEST_INVALID")
        if (
            hashlib.sha256(raw).hexdigest() != verified_locator.request_object.plaintext_sha256
            or request.issued_at > verified_locator.issued_at
            or request.binding != binding
        ):
            _fail("REMOTE_ACK_TRANSPORT_REQUEST_LOCATOR_ORDER_INVALID")
        return request

    def publish_receipt(
        self,
        *,
        request_publication: PhysicalWalRemoteAckRequestPublication,
        durable_ledger_result: PhysicalWalRemoteAckReceiverLedgerResult,
        expected_binding: PhysicalWalRemoteAckBinding,
        now: datetime,
    ) -> PhysicalWalRemoteAckReceiptPublication:
        config = _config_facts(self._config, require_enabled=True)
        binding = _binding(expected_binding)
        _route_for_destination(config, binding)
        request = _publication_request(request_publication, now=now)
        # The only return recipient comes from the FI publication capability.
        # Validate it before creating an otherwise unreadable IR receipt.
        if config.peer_age_recipient != request.source_age_recipient:
            _fail("REMOTE_ACK_TRANSPORT_RECEIPT_ROUTE_MISMATCH")
        if type(durable_ledger_result) is not PhysicalWalRemoteAckReceiverLedgerResult:
            _fail("REMOTE_ACK_TRANSPORT_DURABLE_LEDGER_RESULT_REQUIRED")
        raw_receipt = durable_ledger_result.destination_receipt
        if (
            type(raw_receipt) is not bytes
            or type(durable_ledger_result.destination_receipt_sha256) is not str
            or hashlib.sha256(raw_receipt).hexdigest() != durable_ledger_result.destination_receipt_sha256
            or durable_ledger_result.source_request_sha256 != hashlib.sha256(request.source_request).hexdigest()
        ):
            _fail("REMOTE_ACK_TRANSPORT_DURABLE_LEDGER_RESULT_INVALID")
        try:
            evidence = verify_physical_wal_remote_ack_evidence(
                source_request=request.source_request,
                destination_receipt=raw_receipt,
                expected_binding=binding,
                expected_source_public_key=self._expected_source_public_key,
                expected_destination_public_key=self._expected_destination_public_key,
                now=now,
            )
        except PhysicalWalRemoteAckError:
            _fail("REMOTE_ACK_TRANSPORT_DURABLE_LEDGER_RECEIPT_INVALID")
        if (
            evidence.receipt_id != durable_ledger_result.receipt_id
            or evidence.receipt_nonce != durable_ledger_result.receipt_nonce
            or evidence.acknowledged_at != durable_ledger_result.acknowledged_at
        ):
            _fail("REMOTE_ACK_TRANSPORT_DURABLE_LEDGER_RESULT_INVALID")
        receipt_hash = hashlib.sha256(raw_receipt).hexdigest()
        pin = self._publish(
            config=config,
            binding=binding,
            role="receipt",
            plaintext=raw_receipt,
            recipient=config.peer_age_recipient,
            request_sha256=hashlib.sha256(request.source_request).hexdigest(),
            receipt_sha256=receipt_hash,
        )
        result = PhysicalWalRemoteAckReceiptPublication(
            request_publication=request,
            destination_receipt=raw_receipt,
            verified_evidence=evidence,
            source_age_recipient=request.source_age_recipient,
            object_pin=pin,
        )
        object.__setattr__(result, "_capability", _PUBLICATION_CAPABILITY)
        _publication_receipt(result, now=now)
        return result

    def receive_receipt(
        self,
        *,
        request_publication: PhysicalWalRemoteAckRequestPublication,
        locator: VerifiedPhysicalWalRemoteAckReceiptLocator,
        expected_binding: PhysicalWalRemoteAckBinding,
        expected_witness_public_key: bytes,
        now: datetime,
    ) -> VerifiedPhysicalWalRemoteAckEvidence:
        binding = _binding(expected_binding)
        config = _config_facts(self._config, require_enabled=True)
        _route_for_source(config, binding)
        request = _publication_request(request_publication, now=now)
        verified_locator = require_verified_physical_wal_remote_ack_receipt_locator(
            locator,
            expected_binding=binding,
            expected_witness_public_key=expected_witness_public_key,
            now=now,
        )
        if (
            verified_locator.source_age_recipient != config.local_age_recipient
            or verified_locator.request_object != request.object_pin
            or verified_locator.receipt_object.age_recipient != config.local_age_recipient
        ):
            _fail("REMOTE_ACK_TRANSPORT_RECEIPT_LOCATOR_ROUTE_MISMATCH")
        raw = self._pull_plaintext(
            config=config,
            binding=binding,
            pin=verified_locator.receipt_object,
            request_sha256=hashlib.sha256(request.source_request).hexdigest(),
            receipt_sha256=verified_locator.receipt_object.plaintext_sha256,
        )
        try:
            evidence = verify_physical_wal_remote_ack_evidence(
                source_request=request.source_request,
                destination_receipt=raw,
                expected_binding=binding,
                expected_source_public_key=self._expected_source_public_key,
                expected_destination_public_key=self._expected_destination_public_key,
                now=now,
            )
        except PhysicalWalRemoteAckError:
            _fail("REMOTE_ACK_TRANSPORT_RECEIVED_RECEIPT_INVALID")
        if (
            hashlib.sha256(raw).hexdigest() != verified_locator.receipt_object.plaintext_sha256
            or evidence.acknowledged_at > verified_locator.issued_at
        ):
            _fail("REMOTE_ACK_TRANSPORT_RECEIPT_LOCATOR_ORDER_INVALID")
        return evidence
