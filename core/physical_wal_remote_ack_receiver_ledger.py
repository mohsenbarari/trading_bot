"""Root-only durable receipt ledger for physical-WAL remote acknowledgements.

This is deliberately a local persistence and signing boundary.  It does not
connect to Object Storage, a peer, PostgreSQL, the Witness, a shell, Docker,
or a network.  A caller must first verify a signed source request with
``core.physical_wal_remote_ack`` and must separately provide a verified,
request-bound receiver recovery observation.  This module then atomically
persists one exact signed receipt before it returns it.

The recovery observation is a *typed adapter boundary*, not an implementation
of PostgreSQL replay.  Its verifier checks that the observation is fresh and
bound to every signed request frontier/object/version pin, but it never opens
or queries PostgreSQL.  A future trusted local adapter must establish the
underlying recovery fact before presenting the observation here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Iterator

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core.physical_wal_remote_ack import (
    MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS,
    MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES,
    PhysicalWalRemoteAckBinding,
    PhysicalWalRemoteAckError,
    PhysicalWalRemoteAckObjectVersion,
    VerifiedPhysicalWalRemoteAckRequest,
    build_physical_wal_remote_ack_receipt,
    require_verified_physical_wal_remote_ack_request,
    verify_physical_wal_remote_ack_evidence,
)


__all__ = (
    "DEFAULT_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES",
    "PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA",
    "PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_VERSION",
    "PhysicalWalRemoteAckReceiverLedgerConfig",
    "PhysicalWalRemoteAckReceiverLedgerError",
    "PhysicalWalRemoteAckReceiverLedgerResult",
    "PhysicalWalRemoteAckReceiverRecoveryEvidence",
    "VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence",
    "derive_physical_wal_remote_ack_receiver_request_binding_sha256",
    "issue_physical_wal_remote_ack_receiver_receipt",
    "require_verified_physical_wal_remote_ack_receiver_recovery_evidence",
    "verify_physical_wal_remote_ack_receiver_recovery_evidence",
)


PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA = (
    "gold-trade-physical-wal-remote-ack-receiver-ledger-v1"
)
PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_VERSION = 1
PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES = 256
MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_ENTRIES = 4_096
MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_BYTES = 64 * 1024 * 1024
MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_RECOVERY_EVIDENCE_AGE_SECONDS = (
    30
)

_LEDGER_DIRECTORY = "physical-wal-remote-ack-ledger"
_LEDGER_FILENAME = "ledger.json"
_LOCK_FILENAME = "ledger.lock"
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$", re.ASCII)
_LEDGER_FIELDS = frozenset(
    {
        "schema",
        "version",
        "configuration_sha256",
        "entries",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "request_id",
        "request_nonce",
        "source_request_sha256",
        "source_request_base64",
        "request_binding_sha256",
        "receiver_recovery_evidence_sha256",
        "receiver_replay_lsn",
        "receiver_observed_at",
        "receipt_id",
        "receipt_nonce",
        "destination_receipt_sha256",
        "destination_receipt_base64",
        "acknowledged_at",
    }
)
_VERIFIED_RECOVERY_EVIDENCE_CAPABILITY = object()


class PhysicalWalRemoteAckReceiverLedgerError(ValueError):
    """The root-only remote-ack ledger cannot safely return a receipt."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalRemoteAckReceiverLedgerConfig:
    """Root-owned local state and exact signer/route pins for one direction.

    ``enabled`` defaults to false.  The state root must already exist as an
    absolute, root-owned ``0700`` directory; this class never follows a path
    supplied by a source request.  A ledger is intentionally tied to one exact
    remote-ack binding and one pair of Ed25519 public keys.
    """

    state_root: Path | None = None
    expected_binding: PhysicalWalRemoteAckBinding | None = None
    expected_source_public_key: bytes | None = None
    expected_destination_public_key: bytes | None = None
    enabled: bool = PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_DEFAULT_ENABLED
    maximum_entries: int = DEFAULT_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_MAXIMUM_ENTRIES


@dataclass(frozen=True)
class PhysicalWalRemoteAckReceiverRecoveryEvidence:
    """A separately collected local recovery observation.

    The values are intentionally non-secret.  This type does not run or query
    PostgreSQL and is not, by itself, proof that PostgreSQL replayed anything.
    The caller's trusted local recovery adapter is responsible for producing a
    truthful observation before ``verify_*`` binds it to a signed request.
    """

    source_request_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_site: str
    source_site: str
    destination_site: str
    request_binding_sha256: str
    manifest_sha256es: tuple[str, ...]
    object_versions: tuple[PhysicalWalRemoteAckObjectVersion, ...]
    replay_lsn: str
    observed_at: datetime
    in_recovery: bool
    role: str


@dataclass(frozen=True)
class VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence:
    """Opaque, request-bound recovery observation accepted by this process.

    This capability remains less than a Writer/promotion permit.  It is
    deliberately tied to exactly one canonical source request and cannot be
    reused for a different request, route, manifest set, object-version set,
    or replay frontier.
    """

    evidence: PhysicalWalRemoteAckReceiverRecoveryEvidence
    source_request_sha256: str
    request_binding_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalRemoteAckReceiverLedgerResult:
    """One durably committed exact receipt, never an operational authority."""

    destination_receipt: bytes
    destination_receipt_sha256: str
    source_request_sha256: str
    receipt_id: str
    receipt_nonce: str
    acknowledged_at: datetime
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    ledger_path: Path
    idempotent: bool


@dataclass(frozen=True)
class _NormalisedConfig:
    state_root: Path
    expected_binding: PhysicalWalRemoteAckBinding
    expected_source_public_key: bytes
    expected_destination_public_key: bytes
    maximum_entries: int
    configuration_sha256: str


@dataclass(frozen=True)
class _LedgerEntry:
    request_id: str
    request_nonce: str
    source_request_sha256: str
    source_request: bytes
    request_binding_sha256: str
    receiver_recovery_evidence_sha256: str
    receiver_replay_lsn: str
    receiver_observed_at: datetime
    receipt_id: str
    receipt_nonce: str
    destination_receipt_sha256: str
    destination_receipt: bytes
    acknowledged_at: datetime


def _fail(code: str) -> None:
    raise PhysicalWalRemoteAckReceiverLedgerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("LEDGER_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("LEDGER_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError(code) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _id(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _REQUEST_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(code)
    return normalized


def _timestamp_text(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _binding_mapping(value: PhysicalWalRemoteAckBinding) -> dict[str, Any]:
    """Serialize a request binding only after its opaque request was verified."""

    return {
        "source_site": value.source_site,
        "destination_site": value.destination_site,
        "destination_age_recipient": value.destination_age_recipient,
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "stream_generation_id": value.stream_generation_id,
        "baseline_generation_id": value.baseline_generation_id,
        "baseline_manifest_sha256": value.baseline_manifest_sha256,
        "writer_term": {
            "writer_holder_site": value.writer_term.writer_holder_site,
            "writer_epoch": value.writer_term.writer_epoch,
            "writer_lease_id": value.writer_term.writer_lease_id,
            "witnessed_term_proof_sha256": value.writer_term.witnessed_term_proof_sha256,
        },
        "target_acknowledged_wal_lsn": value.target_acknowledged_wal_lsn,
        "blob_object_frontier_wal_lsn": value.blob_object_frontier_wal_lsn,
        "objects_complete": value.objects_complete,
        "manifest_sha256es": list(value.manifest_sha256es),
        "object_versions": [
            {"object_key": item.object_key, "version_id": item.version_id}
            for item in value.object_versions
        ],
    }


def _request_binding_sha256(value: PhysicalWalRemoteAckBinding) -> str:
    return hashlib.sha256(
        _canonical(_binding_mapping(value), code="REQUEST_BINDING_CANONICAL_INVALID")
    ).hexdigest()


def derive_physical_wal_remote_ack_receiver_request_binding_sha256(
    *,
    source_request: object,
    now: datetime,
) -> str:
    """Return the stable binding pin for one already-verified source request.

    A recovery adapter uses this non-secret value to demonstrate that its
    observation concerns the same route, term, manifest set, and immutable
    object-version set as the exact request it was handed.  It never accepts a
    raw request and does not perform I/O.
    """

    try:
        request = require_verified_physical_wal_remote_ack_request(source_request, now=now)
    except PhysicalWalRemoteAckError as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("SOURCE_REQUEST_UNVERIFIED_OR_STALE") from exc
    return _request_binding_sha256(request.binding)


def _configuration_sha256(
    *,
    binding: PhysicalWalRemoteAckBinding,
    source_public_key: bytes,
    destination_public_key: bytes,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "binding": _binding_mapping(binding),
                "source_public_key_sha256": hashlib.sha256(source_public_key).hexdigest(),
                "destination_public_key_sha256": hashlib.sha256(destination_public_key).hexdigest(),
            },
            code="LEDGER_CONFIGURATION_CANONICAL_INVALID",
        )
    ).hexdigest()


def _object_versions(value: object, *, code: str) -> tuple[PhysicalWalRemoteAckObjectVersion, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(code)
    result: list[PhysicalWalRemoteAckObjectVersion] = []
    for item in value:
        if type(item) is not PhysicalWalRemoteAckObjectVersion:
            _fail(code)
        key = item.object_key
        version = item.version_id
        if not isinstance(key, str) or not isinstance(version, str):
            _fail(code)
        result.append(item)
    normalized = tuple(sorted(result, key=lambda item: (item.object_key, item.version_id)))
    if not normalized or len({(item.object_key, item.version_id) for item in normalized}) != len(normalized):
        _fail(code)
    return normalized


def _manifest_hashes(value: object, *, code: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(code)
    normalized = tuple(sorted(_sha256(item, code=code) for item in value))
    if not normalized or len(set(normalized)) != len(normalized):
        _fail(code)
    return normalized


def _normalise_recovery_evidence(
    value: object,
    *,
    source_request: VerifiedPhysicalWalRemoteAckRequest,
    now: datetime,
) -> PhysicalWalRemoteAckReceiverRecoveryEvidence:
    if type(value) is not PhysicalWalRemoteAckReceiverRecoveryEvidence:
        _fail("RECOVERY_EVIDENCE_INVALID")
    observed_now = _utc(now, code="RECOVERY_EVIDENCE_CLOCK_INVALID")
    request_hash = hashlib.sha256(source_request.source_request).hexdigest()
    if _sha256(value.source_request_sha256, code="RECOVERY_EVIDENCE_REQUEST_HASH_INVALID") != request_hash:
        _fail("RECOVERY_EVIDENCE_REQUEST_HASH_MISMATCH")
    recovery_hash = _sha256(
        value.receiver_recovery_evidence_sha256,
        code="RECOVERY_EVIDENCE_HASH_INVALID",
    )
    if (
        value.receiver_site != source_request.binding.destination_site
        or value.destination_site != source_request.binding.destination_site
        or value.source_site != source_request.binding.source_site
    ):
        _fail("RECOVERY_EVIDENCE_ROUTE_MISMATCH")
    binding_hash = _sha256(value.request_binding_sha256, code="RECOVERY_EVIDENCE_BINDING_INVALID")
    expected_binding_hash = _request_binding_sha256(source_request.binding)
    if binding_hash != expected_binding_hash:
        _fail("RECOVERY_EVIDENCE_BINDING_MISMATCH")
    manifests = _manifest_hashes(value.manifest_sha256es, code="RECOVERY_EVIDENCE_MANIFESTS_INVALID")
    if manifests != source_request.binding.manifest_sha256es:
        _fail("RECOVERY_EVIDENCE_MANIFESTS_MISMATCH")
    objects = _object_versions(value.object_versions, code="RECOVERY_EVIDENCE_OBJECTS_INVALID")
    if objects != source_request.binding.object_versions:
        _fail("RECOVERY_EVIDENCE_OBJECTS_MISMATCH")
    replay_lsn, replay_value = _lsn(value.replay_lsn, code="RECOVERY_EVIDENCE_REPLAY_LSN_INVALID")
    _target_lsn, target_value = _lsn(
        source_request.binding.target_acknowledged_wal_lsn,
        code="RECOVERY_EVIDENCE_TARGET_LSN_INVALID",
    )
    if replay_value < target_value:
        _fail("RECOVERY_EVIDENCE_REPLAY_LSN_BEHIND_TARGET")
    observed_at = _utc(value.observed_at, code="RECOVERY_EVIDENCE_TIME_INVALID")
    if observed_at < source_request.issued_at:
        _fail("RECOVERY_EVIDENCE_PREDATES_REQUEST")
    if observed_at > observed_now + timedelta(seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_FUTURE_SKEW_SECONDS):
        _fail("RECOVERY_EVIDENCE_TIME_INVALID")
    if observed_at < observed_now - timedelta(
        seconds=MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_RECOVERY_EVIDENCE_AGE_SECONDS
    ):
        _fail("RECOVERY_EVIDENCE_TIME_STALE")
    if type(value.in_recovery) is not bool or not value.in_recovery or value.role != "standby":
        _fail("RECOVERY_EVIDENCE_NOT_STANDBY_RECOVERY")
    return PhysicalWalRemoteAckReceiverRecoveryEvidence(
        source_request_sha256=request_hash,
        receiver_recovery_evidence_sha256=recovery_hash,
        receiver_site=source_request.binding.destination_site,
        source_site=source_request.binding.source_site,
        destination_site=source_request.binding.destination_site,
        request_binding_sha256=expected_binding_hash,
        manifest_sha256es=manifests,
        object_versions=objects,
        replay_lsn=replay_lsn,
        observed_at=observed_at,
        in_recovery=True,
        role="standby",
    )


def verify_physical_wal_remote_ack_receiver_recovery_evidence(
    *,
    source_request: object,
    recovery_evidence: object,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence:
    """Bind a supplied recovery observation to one already-verified request.

    This function is pure: it neither contacts nor opens PostgreSQL.  Its
    result only says that an independently supplied observation has the exact
    request pins and reports a standby replay LSN at/after the requested LSN.
    """

    try:
        request = require_verified_physical_wal_remote_ack_request(source_request, now=now)
    except PhysicalWalRemoteAckError as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("SOURCE_REQUEST_UNVERIFIED_OR_STALE") from exc
    evidence = _normalise_recovery_evidence(
        recovery_evidence,
        source_request=request,
        now=now,
    )
    result = VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence(
        evidence=evidence,
        source_request_sha256=hashlib.sha256(request.source_request).hexdigest(),
        request_binding_sha256=_request_binding_sha256(request.binding),
    )
    object.__setattr__(result, "_capability", _VERIFIED_RECOVERY_EVIDENCE_CAPABILITY)
    return result


def require_verified_physical_wal_remote_ack_receiver_recovery_evidence(
    value: object,
    *,
    source_request: object,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence:
    """Recheck a recovery capability against its exact current request."""

    if (
        type(value) is not VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence
        or value._capability is not _VERIFIED_RECOVERY_EVIDENCE_CAPABILITY
    ):
        _fail("VERIFIED_RECOVERY_EVIDENCE_REQUIRED")
    verified = verify_physical_wal_remote_ack_receiver_recovery_evidence(
        source_request=source_request,
        recovery_evidence=value.evidence,
        now=now,
    )
    if (
        verified.source_request_sha256 != value.source_request_sha256
        or verified.request_binding_sha256 != value.request_binding_sha256
    ):
        _fail("VERIFIED_RECOVERY_EVIDENCE_TAMPERED")
    return value


def _secure_root(value: object, *, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail(code)
    try:
        resolved = value.resolve(strict=True)
        metadata = os.lstat(value)
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


def _normalise_config(value: object) -> _NormalisedConfig:
    if type(value) is not PhysicalWalRemoteAckReceiverLedgerConfig:
        _fail("LEDGER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("REMOTE_ACK_RECEIVER_LEDGER_DISABLED")
    if os.geteuid() != 0:
        _fail("ROOT_RUNTIME_REQUIRED")
    root = _secure_root(value.state_root, code="LEDGER_STATE_ROOT_UNSAFE")
    if type(value.expected_binding) is not PhysicalWalRemoteAckBinding:
        _fail("LEDGER_EXPECTED_BINDING_INVALID")
    source = _public_key(value.expected_source_public_key, code="LEDGER_SOURCE_PUBLIC_KEY_INVALID")
    destination = _public_key(
        value.expected_destination_public_key,
        code="LEDGER_DESTINATION_PUBLIC_KEY_INVALID",
    )
    if type(value.maximum_entries) is not int or not 1 <= value.maximum_entries <= MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_ENTRIES:
        _fail("LEDGER_MAXIMUM_ENTRIES_INVALID")
    # The request verifier is the authority that normalizes this binding.  The
    # configuration becomes usable only after its exact equality check below.
    try:
        configuration_sha256 = _configuration_sha256(
            binding=value.expected_binding,
            source_public_key=source,
            destination_public_key=destination,
        )
    except (AttributeError, TypeError, PhysicalWalRemoteAckReceiverLedgerError) as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("LEDGER_EXPECTED_BINDING_INVALID") from exc
    return _NormalisedConfig(
        state_root=root,
        expected_binding=value.expected_binding,
        expected_source_public_key=source,
        expected_destination_public_key=destination,
        maximum_entries=value.maximum_entries,
        configuration_sha256=configuration_sha256,
    )


def _secure_child(parent: Path, name: str) -> Path:
    if _SAFE_COMPONENT_RE.fullmatch(name) is None:
        _fail("LEDGER_PATH_COMPONENT_INVALID")
    path = parent / name
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        _fail("LEDGER_DIRECTORY_CREATE_FAILED")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("LEDGER_DIRECTORY_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("LEDGER_DIRECTORY_UNSAFE")
    return path


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("LEDGER_PLATFORM_NO_DIRECTORY_FSYNC")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("LEDGER_DIRECTORY_FSYNC_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("LEDGER_DIRECTORY_FSYNC_FAILED")
    finally:
        os.close(descriptor)


def _open_lock(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        _fail("LEDGER_LOCK_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def _locked_ledger_directory(config: _NormalisedConfig) -> Iterator[tuple[Path, Path]]:
    directory = _secure_child(config.state_root, _LEDGER_DIRECTORY)
    lock_path = directory / _LOCK_FILENAME
    descriptor = _open_lock(lock_path)
    try:
        yield directory, directory / _LEDGER_FILENAME
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _open_existing_ledger(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise
    except OSError:
        _fail("LEDGER_STATE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_BYTES
        ):
            _fail("LEDGER_STATE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_canonical_ledger(path: Path) -> dict[str, Any] | None:
    try:
        descriptor = _open_existing_ledger(path)
    except FileNotFoundError:
        return None
    try:
        size = os.fstat(descriptor).st_size
        payload = bytearray()
        while len(payload) < size:
            try:
                chunk = os.read(descriptor, size - len(payload))
            except OSError:
                _fail("LEDGER_STATE_READ_FAILED")
            if not chunk:
                _fail("LEDGER_STATE_READ_FAILED")
            payload.extend(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("LEDGER_STATE_READ_FAILED")
        except OSError:
            _fail("LEDGER_STATE_READ_FAILED")
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalRemoteAckReceiverLedgerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("LEDGER_STATE_JSON_INVALID") from exc
    if not isinstance(parsed, dict) or _canonical(parsed, code="LEDGER_STATE_CANONICAL_INVALID") != raw:
        _fail("LEDGER_STATE_CANONICAL_INVALID")
    return parsed


def _decode_stored_bytes(value: object, *, code: str) -> bytes:
    if not isinstance(value, str):
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii", "strict"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    if not 1 <= len(decoded) <= MAX_PHYSICAL_WAL_REMOTE_ACK_BYTES:
        _fail(code)
    return decoded


def _entry_from_mapping(value: object, *, config: _NormalisedConfig) -> _LedgerEntry:
    entry = _exact_mapping(value, fields=_ENTRY_FIELDS, code="LEDGER_ENTRY_FIELDS_INVALID")
    request_id = _id(entry["request_id"], code="LEDGER_ENTRY_IDENTITY_INVALID")
    request_nonce = _nonce(entry["request_nonce"], code="LEDGER_ENTRY_IDENTITY_INVALID")
    receipt_id = _id(entry["receipt_id"], code="LEDGER_ENTRY_IDENTITY_INVALID")
    receipt_nonce = _nonce(entry["receipt_nonce"], code="LEDGER_ENTRY_IDENTITY_INVALID")
    if len({request_id, request_nonce, receipt_id, receipt_nonce}) != 4:
        _fail("LEDGER_ENTRY_IDENTITY_REUSED")
    source_request = _decode_stored_bytes(entry["source_request_base64"], code="LEDGER_ENTRY_REQUEST_INVALID")
    destination_receipt = _decode_stored_bytes(
        entry["destination_receipt_base64"], code="LEDGER_ENTRY_RECEIPT_INVALID"
    )
    source_hash = _sha256(entry["source_request_sha256"], code="LEDGER_ENTRY_REQUEST_HASH_INVALID")
    receipt_hash = _sha256(
        entry["destination_receipt_sha256"], code="LEDGER_ENTRY_RECEIPT_HASH_INVALID"
    )
    if hashlib.sha256(source_request).hexdigest() != source_hash:
        _fail("LEDGER_ENTRY_REQUEST_HASH_MISMATCH")
    if hashlib.sha256(destination_receipt).hexdigest() != receipt_hash:
        _fail("LEDGER_ENTRY_RECEIPT_HASH_MISMATCH")
    binding_hash = _sha256(entry["request_binding_sha256"], code="LEDGER_ENTRY_BINDING_INVALID")
    recovery_hash = _sha256(
        entry["receiver_recovery_evidence_sha256"], code="LEDGER_ENTRY_RECOVERY_INVALID"
    )
    replay_lsn, replay_value = _lsn(entry["receiver_replay_lsn"], code="LEDGER_ENTRY_RECOVERY_INVALID")
    observed_at = _timestamp(entry["receiver_observed_at"], code="LEDGER_ENTRY_RECOVERY_INVALID")
    acknowledged_at = _timestamp(entry["acknowledged_at"], code="LEDGER_ENTRY_ACK_TIME_INVALID")
    try:
        verified = verify_physical_wal_remote_ack_evidence(
            source_request=source_request,
            destination_receipt=destination_receipt,
            expected_binding=config.expected_binding,
            expected_source_public_key=config.expected_source_public_key,
            expected_destination_public_key=config.expected_destination_public_key,
            now=acknowledged_at,
        )
    except PhysicalWalRemoteAckError as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("LEDGER_ENTRY_SIGNATURE_OR_ROUTE_INVALID") from exc
    if (
        verified.request_id != request_id
        or verified.request_nonce != request_nonce
        or verified.receipt_id != receipt_id
        or verified.receipt_nonce != receipt_nonce
        or verified.acknowledged_at != acknowledged_at
        or binding_hash != _request_binding_sha256(verified.binding)
    ):
        _fail("LEDGER_ENTRY_BINDING_MISMATCH")
    _target_lsn, target_value = _lsn(
        verified.binding.target_acknowledged_wal_lsn,
        code="LEDGER_ENTRY_BINDING_MISMATCH",
    )
    if replay_value < target_value or observed_at < verified.issued_at or observed_at > acknowledged_at:
        _fail("LEDGER_ENTRY_RECOVERY_MISMATCH")
    return _LedgerEntry(
        request_id=request_id,
        request_nonce=request_nonce,
        source_request_sha256=source_hash,
        source_request=source_request,
        request_binding_sha256=binding_hash,
        receiver_recovery_evidence_sha256=recovery_hash,
        receiver_replay_lsn=replay_lsn,
        receiver_observed_at=observed_at,
        receipt_id=receipt_id,
        receipt_nonce=receipt_nonce,
        destination_receipt_sha256=receipt_hash,
        destination_receipt=destination_receipt,
        acknowledged_at=acknowledged_at,
    )


def _load_entries(path: Path, *, config: _NormalisedConfig) -> tuple[_LedgerEntry, ...]:
    value = _read_canonical_ledger(path)
    if value is None:
        return ()
    ledger = _exact_mapping(value, fields=_LEDGER_FIELDS, code="LEDGER_STATE_FIELDS_INVALID")
    if (
        ledger["schema"] != PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA
        or ledger["version"] != PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_VERSION
        or _sha256(ledger["configuration_sha256"], code="LEDGER_CONFIGURATION_INVALID")
        != config.configuration_sha256
        or not isinstance(ledger["entries"], list)
    ):
        _fail("LEDGER_CONFIGURATION_CONFLICT")
    if len(ledger["entries"]) > config.maximum_entries:
        _fail("LEDGER_ENTRY_LIMIT_EXCEEDED")
    entries = tuple(_entry_from_mapping(item, config=config) for item in ledger["entries"])
    if tuple(sorted(entries, key=lambda item: item.request_id)) != entries:
        _fail("LEDGER_ENTRY_ORDER_INVALID")
    request_ids = {entry.request_id for entry in entries}
    request_nonces = {entry.request_nonce for entry in entries}
    receipt_ids = {entry.receipt_id for entry in entries}
    receipt_nonces = {entry.receipt_nonce for entry in entries}
    if (
        len(request_ids) != len(entries)
        or len(request_nonces) != len(entries)
        or len(receipt_ids) != len(entries)
        or len(receipt_nonces) != len(entries)
        or request_ids & receipt_ids
        or request_nonces & receipt_nonces
    ):
        _fail("LEDGER_REPLAY_INDEX_CONFLICT")
    return entries


def _entry_mapping(value: _LedgerEntry) -> dict[str, Any]:
    return {
        "request_id": value.request_id,
        "request_nonce": value.request_nonce,
        "source_request_sha256": value.source_request_sha256,
        "source_request_base64": base64.b64encode(value.source_request).decode("ascii"),
        "request_binding_sha256": value.request_binding_sha256,
        "receiver_recovery_evidence_sha256": value.receiver_recovery_evidence_sha256,
        "receiver_replay_lsn": value.receiver_replay_lsn,
        "receiver_observed_at": _timestamp_text(
            value.receiver_observed_at, code="LEDGER_ENTRY_TIME_INVALID"
        ),
        "receipt_id": value.receipt_id,
        "receipt_nonce": value.receipt_nonce,
        "destination_receipt_sha256": value.destination_receipt_sha256,
        "destination_receipt_base64": base64.b64encode(value.destination_receipt).decode("ascii"),
        "acknowledged_at": _timestamp_text(value.acknowledged_at, code="LEDGER_ENTRY_TIME_INVALID"),
    }


def _ledger_mapping(*, config: _NormalisedConfig, entries: Sequence[_LedgerEntry]) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_SCHEMA,
        "version": PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "entries": [_entry_mapping(item) for item in sorted(entries, key=lambda item: item.request_id)],
    }


def _write_atomic_ledger(path: Path, *, value: Mapping[str, Any]) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LEDGER_PLATFORM_NO_NOFOLLOW")
    payload = _canonical(value, code="LEDGER_STATE_CANONICAL_INVALID")
    if not 1 <= len(payload) <= MAX_PHYSICAL_WAL_REMOTE_ACK_RECEIVER_LEDGER_BYTES:
        _fail("LEDGER_STATE_SIZE_INVALID")
    temporary = path.parent / ("." + path.name + "." + secrets.token_hex(16) + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    replaced = False
    try:
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except OSError:
            _fail("LEDGER_TEMPORARY_OPEN_FAILED")
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("LEDGER_TEMPORARY_UNSAFE")
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except OSError:
                _fail("LEDGER_TEMPORARY_WRITE_FAILED")
            if written <= 0:
                _fail("LEDGER_TEMPORARY_WRITE_FAILED")
            view = view[written:]
        try:
            os.fsync(descriptor)
        except OSError:
            _fail("LEDGER_TEMPORARY_FSYNC_FAILED")
        try:
            os.close(descriptor)
        except OSError:
            _fail("LEDGER_TEMPORARY_CLOSE_FAILED")
        finally:
            descriptor = -1
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(path.parent)
        metadata = os.lstat(path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("LEDGER_STATE_UNSAFE")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                # The future random temporary is inaccessible to non-root
                # users because the enclosing directory is checked as 0700.
                pass


def _require_request_for_config(
    value: object,
    *,
    config: _NormalisedConfig,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckRequest:
    try:
        request = require_verified_physical_wal_remote_ack_request(value, now=now)
    except PhysicalWalRemoteAckError as exc:
        raise PhysicalWalRemoteAckReceiverLedgerError("SOURCE_REQUEST_UNVERIFIED_OR_STALE") from exc
    if (
        request.binding != config.expected_binding
        or request.source_public_key != config.expected_source_public_key
        or request.binding.destination_site == request.binding.source_site
    ):
        _fail("SOURCE_REQUEST_ROUTE_OR_BINDING_MISMATCH")
    return request


def _destination_signer_matches(value: object, *, expected_public_key: bytes) -> None:
    """Ask the existing receipt builder to validate the signer without writes."""

    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - environment dependency.
        raise PhysicalWalRemoteAckReceiverLedgerError("DESTINATION_SIGNER_INVALID") from exc
    if not isinstance(value, Ed25519PrivateKey):
        _fail("DESTINATION_SIGNER_INVALID")
    try:
        actual = value.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except ValueError:
        _fail("DESTINATION_SIGNER_INVALID")
    if actual != expected_public_key:
        _fail("DESTINATION_SIGNER_ROUTE_MISMATCH")


def _receipt_identity() -> tuple[str, str]:
    # URL-safe tokens are constrained further by the explicit protocol regexes
    # in the receipt builder.  The loop makes the collision property explicit.
    while True:
        receipt_id = "remote-ack-" + secrets.token_urlsafe(24)
        receipt_nonce = secrets.token_urlsafe(32)
        if _REQUEST_ID_RE.fullmatch(receipt_id) and _NONCE_RE.fullmatch(receipt_nonce):
            return receipt_id, receipt_nonce


def _result_from_entry(
    entry: _LedgerEntry,
    *,
    ledger_path: Path,
    idempotent: bool,
) -> PhysicalWalRemoteAckReceiverLedgerResult:
    return PhysicalWalRemoteAckReceiverLedgerResult(
        destination_receipt=entry.destination_receipt,
        destination_receipt_sha256=entry.destination_receipt_sha256,
        source_request_sha256=entry.source_request_sha256,
        receipt_id=entry.receipt_id,
        receipt_nonce=entry.receipt_nonce,
        acknowledged_at=entry.acknowledged_at,
        receiver_recovery_evidence_sha256=entry.receiver_recovery_evidence_sha256,
        receiver_replay_lsn=entry.receiver_replay_lsn,
        ledger_path=ledger_path,
        idempotent=idempotent,
    )


def issue_physical_wal_remote_ack_receiver_receipt(
    *,
    config: object,
    source_request: object,
    recovery_evidence: object | None,
    destination_signer: object | None,
    now: datetime,
) -> PhysicalWalRemoteAckReceiverLedgerResult:
    """Persist and return an exact remote-ack receipt, or fail closed.

    The source request must already be a capability returned by
    ``verify_physical_wal_remote_ack_request``.  A *new* receipt also requires
    recovery evidence separately verified by this module.  New receipts are
    generated and fsync-committed under an exclusive root-owned ledger lock
    before they are returned.  A retry with the same request ID and exact
    request hash returns the pre-existing byte-for-byte receipt without a
    second signature or a new recovery observation; a reused request ID/nonce
    with any different request fails closed.
    """

    normalized = _normalise_config(config)
    observed_now = _utc(now, code="LEDGER_CLOCK_INVALID")
    request = _require_request_for_config(source_request, config=normalized, now=observed_now)
    request_hash = hashlib.sha256(request.source_request).hexdigest()
    binding_hash = _request_binding_sha256(request.binding)

    with _locked_ledger_directory(normalized) as (_directory, ledger_path):
        entries = _load_entries(ledger_path, config=normalized)
        by_request_id = {entry.request_id: entry for entry in entries}
        by_request_nonce = {entry.request_nonce: entry for entry in entries}
        existing = by_request_id.get(request.request_id)
        if existing is not None:
            if existing.source_request_sha256 != request_hash or existing.source_request != request.source_request:
                _fail("REQUEST_ID_REUSE_CONFLICT")
            if existing.request_nonce != request.request_nonce:
                _fail("REQUEST_ID_NONCE_CONFLICT")
            if existing.request_binding_sha256 != binding_hash:
                _fail("REQUEST_IDEMPOTENCY_BINDING_CONFLICT")
            return _result_from_entry(existing, ledger_path=ledger_path, idempotent=True)
        nonce_entry = by_request_nonce.get(request.request_nonce)
        if nonce_entry is not None:
            _fail("REQUEST_NONCE_REUSE_CONFLICT")
        if len(entries) >= normalized.maximum_entries:
            _fail("LEDGER_ENTRY_LIMIT_EXCEEDED")
        verified_recovery = require_verified_physical_wal_remote_ack_receiver_recovery_evidence(
            recovery_evidence,
            source_request=request,
            now=observed_now,
        )
        evidence = verified_recovery.evidence
        if (
            verified_recovery.source_request_sha256 != request_hash
            or verified_recovery.request_binding_sha256 != binding_hash
        ):
            _fail("RECOVERY_EVIDENCE_REQUEST_BINDING_MISMATCH")
        if destination_signer is None:
            _fail("DESTINATION_SIGNER_REQUIRED")
        _destination_signer_matches(
            destination_signer,
            expected_public_key=normalized.expected_destination_public_key,
        )
        receipt_id, receipt_nonce = _receipt_identity()
        identities = {
            entry.request_id for entry in entries
        } | {
            entry.request_nonce for entry in entries
        } | {
            entry.receipt_id for entry in entries
        } | {
            entry.receipt_nonce for entry in entries
        } | {request.request_id, request.request_nonce}
        while receipt_id in identities or receipt_nonce in identities or receipt_id == receipt_nonce:
            receipt_id, receipt_nonce = _receipt_identity()
        try:
            receipt_mapping = build_physical_wal_remote_ack_receipt(
                source_request=request.source_request,
                receipt_id=receipt_id,
                receipt_nonce=receipt_nonce,
                acknowledged_at=observed_now,
                destination_signer=destination_signer,
            )
            receipt_raw = _canonical(receipt_mapping, code="DESTINATION_RECEIPT_CANONICAL_INVALID")
            # Re-verify the pair before it becomes durable state.  This is not
            # merely a signer check: it proves the receipt binds this exact raw
            # request, the configured route, and the supplied destination key.
            verify_physical_wal_remote_ack_evidence(
                source_request=request.source_request,
                destination_receipt=receipt_raw,
                expected_binding=normalized.expected_binding,
                expected_source_public_key=normalized.expected_source_public_key,
                expected_destination_public_key=normalized.expected_destination_public_key,
                now=observed_now,
            )
        except PhysicalWalRemoteAckError as exc:
            raise PhysicalWalRemoteAckReceiverLedgerError("DESTINATION_RECEIPT_INVALID") from exc
        entry = _LedgerEntry(
            request_id=request.request_id,
            request_nonce=request.request_nonce,
            source_request_sha256=request_hash,
            source_request=request.source_request,
            request_binding_sha256=binding_hash,
            receiver_recovery_evidence_sha256=evidence.receiver_recovery_evidence_sha256,
            receiver_replay_lsn=evidence.replay_lsn,
            receiver_observed_at=evidence.observed_at,
            receipt_id=receipt_id,
            receipt_nonce=receipt_nonce,
            destination_receipt_sha256=hashlib.sha256(receipt_raw).hexdigest(),
            destination_receipt=receipt_raw,
            acknowledged_at=observed_now,
        )
        _write_atomic_ledger(
            ledger_path,
            value=_ledger_mapping(config=normalized, entries=(*entries, entry)),
        )
        return _result_from_entry(entry, ledger_path=ledger_path, idempotent=False)
