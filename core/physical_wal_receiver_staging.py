"""Local-only receiver staging boundary for physical PostgreSQL/Object Storage.

This module consumes an already verified
``VerifiedPhysicalWalObjectStorageBundle`` through two mandatory injected
interfaces: an exact-version reader and an age decryptor.  It intentionally
provides neither adapter and never contacts Object Storage, starts PostgreSQL,
recovers a standby, promotes a writer, runs a subprocess, or changes routing.

It stages a new immutable candidate beneath a fixed local receiver root, then
writes local O_EXCL consumption records.  The only successful public status is
``staged-not-replay-verified``: local staging and local single-consumption are
not a PostgreSQL replay receipt and not a synchronous remote-apply claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES,
    PhysicalWalImmutableObject,
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)


PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA = "gold-trade-physical-wal-receiver-staging-v1"
PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-receiver-stage-receipt-v1"
)
PHYSICAL_WAL_RECEIVER_CONSUME_RECORD_SCHEMA = (
    "gold-trade-physical-wal-receiver-consume-record-v1"
)
PHYSICAL_WAL_RECEIVER_COMPLETION_RECORD_SCHEMA = (
    "gold-trade-physical-wal-receiver-completion-record-v1"
)
PHYSICAL_WAL_RECEIVER_STAGING_STATUS = "staged-not-replay-verified"
PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS = "blocked"
PHYSICAL_WAL_RECEIVER_STAGING_DEFAULT_ENABLED = False

MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES = 512 * 1024
MAX_PHYSICAL_WAL_RECEIVER_IO_CHUNK_BYTES = 1024 * 1024

_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_WAL_SEGMENT_NAME_RE = re.compile(r"^[0-9A-F]{24}$")
_SYSTEM_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]{0,19}$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "route_binding_sha256",
        "candidate_path",
        "manifest_sha256es",
        "object_versions",
        "artifacts",
        "receipt_sha256",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "artifact_id",
        "kind",
        "object_key",
        "version_id",
        "ciphertext_relative_path",
        "ciphertext_sha256",
        "ciphertext_bytes",
        "plaintext_relative_path",
        "plaintext_sha256",
        "plaintext_bytes",
        "wal_segment_name",
        "wal_start_lsn",
        "wal_end_lsn",
    }
)
_OBJECT_VERSION_FIELDS = frozenset({"object_key", "version_id"})
_CONSUME_FIELDS = frozenset(
    {
        "schema",
        "record_kind",
        "bundle_id",
        "route_binding_sha256",
        "candidate_path",
        "stage_receipt_sha256",
        "manifest_sha256",
        "object_key",
        "version_id",
    }
)
_COMPLETION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "bundle_id",
        "route_binding_sha256",
        "candidate_path",
        "stage_receipt_sha256",
        "manifest_sha256es",
        "object_versions",
    }
)


class PhysicalWalReceiverStagingError(ValueError):
    """A local physical-WAL staging request fails closed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWalReceiverStagingPin:
    """Root-pinned directed route, baseline, recipient, and Writer-term facts.

    ``destination_site`` is the local receiver role.  It is intentionally
    part of the verified bundle/pin binding rather than being hard-coded to
    WA-IR, so the same pull-only staging boundary can handle both normal
    FI→IR replication and the witnessed IR→FI failback direction.
    """

    source_site: str
    destination_site: str
    source_public_key: bytes
    destination_age_recipient: str
    campaign_id: str
    release_sha: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    database_system_identifier: str
    timeline_id: int
    wal_segment_size_bytes: int
    baseline_wal_lsn: str
    wal_chain_start_lsn: str
    base_backup_end_lsn: str
    route_binding_sha256: str


@dataclass(frozen=True)
class PhysicalWalReceiverStagingConfig:
    """Fixed absolute, non-symlink local roots supplied by root-only config."""

    receiver_root: Path
    state_root: Path


@dataclass(frozen=True)
class PhysicalWalExactVersionReadback:
    """Reader metadata for bytes written to the supplied ciphertext FD."""

    object_key: str
    version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int


@dataclass(frozen=True)
class PhysicalWalDecryptionReadback:
    """Decryptor metadata for bytes written to the supplied plaintext FD."""

    object_key: str
    version_id: str
    age_recipient: str
    plaintext_sha256: str
    plaintext_bytes: int


class PhysicalWalExactVersionReader(Protocol):
    """Injected reader with no default S3/HTTP implementation."""

    def read_exact_to_fd(
        self,
        *,
        object_key: str,
        version_id: str,
        destination_fd: int,
    ) -> PhysicalWalExactVersionReadback:
        """Write only the requested exact object version to ``destination_fd``."""


class PhysicalWalDecryptor(Protocol):
    """Injected decryptor with no default age/subprocess implementation."""

    def decrypt_to_fd(
        self,
        *,
        ciphertext_fd: int,
        destination_fd: int,
        object_key: str,
        version_id: str,
        expected_age_recipient: str,
    ) -> PhysicalWalDecryptionReadback:
        """Decrypt the supplied FD into the supplied FD or raise an error."""


@dataclass(frozen=True)
class PhysicalWalReceiverStagingResult:
    """Non-authorizing result: staging is never a database replay proof."""

    status: str
    reason_codes: tuple[str, ...]
    candidate_path: Path | None = None
    stage_receipt_path: Path | None = None
    bundle_id: str | None = None
    receiver_site: str | None = None
    manifest_sha256es: tuple[str, ...] = ()
    idempotent: bool = False

    @property
    def staged(self) -> bool:
        return self.status == PHYSICAL_WAL_RECEIVER_STAGING_STATUS and not self.reason_codes


@dataclass(frozen=True)
class _NormalisedConfig:
    receiver_root: Path
    state_root: Path


@dataclass(frozen=True)
class _ArtifactPlan:
    artifact_id: str
    kind: str
    object: PhysicalWalImmutableObject
    ciphertext_relative_path: str
    plaintext_relative_path: str
    expected_plaintext_sha256: str | None
    expected_plaintext_bytes: int | None
    wal_segment_name: str | None
    wal_start_lsn: str | None
    wal_end_lsn: str | None


@dataclass(frozen=True)
class _StagedArtifact:
    plan: _ArtifactPlan
    plaintext_sha256: str
    plaintext_bytes: int


def _fail(code: str) -> None:
    raise PhysicalWalReceiverStagingError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("LOCAL_RECEIPT_DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("LOCAL_RECEIPT_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalWalReceiverStagingError(code) from exc


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    return _text(value, pattern=SHA256_RE, code=code)


def _positive_int(value: object, *, code: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        _fail(code)
    return value


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        _fail(code)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError):
        _fail(code)
    return value


def _object_key(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(code)
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    if len(encoded) > 1024:
        _fail(code)
    if any(part in {"", ".", ".."} for part in value.split("/")) or not value.endswith(".age"):
        _fail(code)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}", value):
        _fail(code)
    return value


def _version_id(value: object, *, code: str) -> str:
    if not isinstance(value, str) or value.lower() in {"null", "none", "latest", "current"}:
        _fail(code)
    if VERSION_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _wal_segment_size(value: object, *, code: str) -> int:
    if type(value) is not int or value not in PHYSICAL_WAL_SUPPORTED_SEGMENT_SIZES_BYTES:
        _fail(code)
    return value


def _wal_filename_for(*, timeline_id: int, start_lsn_value: int, segment_size: int) -> str:
    if start_lsn_value % segment_size:
        _fail("WAL_START_LSN_NOT_ALIGNED")
    segments_per_log = 0x100000000 // segment_size
    segment_number = start_lsn_value // segment_size
    log = segment_number // segments_per_log
    segment = segment_number % segments_per_log
    if log > 0xFFFFFFFF or segment > 0xFFFFFFFF:
        _fail("WAL_FILENAME_GEOMETRY_OVERFLOW")
    return f"{timeline_id:08X}{log:08X}{segment:08X}"


def _route_payload(pin: PhysicalWalReceiverStagingPin) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
        "source_site": pin.source_site,
        "destination_site": pin.destination_site,
        "source_public_key_sha256": hashlib.sha256(pin.source_public_key).hexdigest(),
        "destination_age_recipient": pin.destination_age_recipient,
        "campaign_id": pin.campaign_id,
        "release_sha": pin.release_sha,
        "writer_epoch": pin.writer_epoch,
        "writer_lease_id": pin.writer_lease_id,
        "witnessed_term_proof_sha256": pin.witnessed_term_proof_sha256,
        "baseline_generation_id": pin.baseline_generation_id,
        "baseline_manifest_sha256": pin.baseline_manifest_sha256,
        "database_system_identifier": pin.database_system_identifier,
        "timeline_id": pin.timeline_id,
        "wal_segment_size_bytes": pin.wal_segment_size_bytes,
        "baseline_wal_lsn": pin.baseline_wal_lsn,
        "wal_chain_start_lsn": pin.wal_chain_start_lsn,
        "base_backup_end_lsn": pin.base_backup_end_lsn,
    }


def derive_physical_wal_receiver_staging_route_binding_sha256(
    pin: PhysicalWalReceiverStagingPin,
) -> str:
    """Return the canonical route/baseline/term pin digest without I/O."""

    normalised = _normalise_pin(pin, verify_route_hash=False)
    return hashlib.sha256(_canonical(_route_payload(normalised), code="ROUTE_BINDING_CANONICAL_INVALID")).hexdigest()


def _normalise_pin(
    value: object,
    *,
    verify_route_hash: bool,
) -> PhysicalWalReceiverStagingPin:
    if type(value) is not PhysicalWalReceiverStagingPin:
        _fail("RECEIVER_PIN_INVALID")
    if (
        not isinstance(value.source_site, str)
        or not isinstance(value.destination_site, str)
        or value.source_site not in WEBAPP_SITES
        or value.destination_site not in WEBAPP_SITES
        or value.source_site == value.destination_site
    ):
        _fail("RECEIVER_PIN_ROUTE_INVALID")
    source_key = _public_key(value.source_public_key, code="RECEIVER_PIN_SOURCE_KEY_INVALID")
    recipient = _text(
        value.destination_age_recipient,
        pattern=AGE_RECIPIENT_RE,
        code="RECEIVER_PIN_DESTINATION_RECIPIENT_INVALID",
    )
    campaign = _text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code="RECEIVER_PIN_CAMPAIGN_INVALID")
    release = _text(value.release_sha, pattern=RELEASE_SHA_RE, code="RECEIVER_PIN_RELEASE_INVALID")
    epoch = _positive_int(value.writer_epoch, code="RECEIVER_PIN_TERM_EPOCH_INVALID")
    lease = _text(value.writer_lease_id, pattern=LEASE_ID_RE, code="RECEIVER_PIN_TERM_LEASE_INVALID")
    proof = _sha256(value.witnessed_term_proof_sha256, code="RECEIVER_PIN_TERM_PROOF_INVALID")
    generation = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="RECEIVER_PIN_BASE_GENERATION_INVALID",
    )
    baseline_hash = _sha256(value.baseline_manifest_sha256, code="RECEIVER_PIN_BASE_MANIFEST_INVALID")
    system_identifier = _text(
        value.database_system_identifier,
        pattern=_SYSTEM_IDENTIFIER_RE,
        code="RECEIVER_PIN_SYSTEM_IDENTIFIER_INVALID",
    )
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        _fail("RECEIVER_PIN_TIMELINE_INVALID")
    segment_size = _wal_segment_size(value.wal_segment_size_bytes, code="RECEIVER_PIN_WAL_SIZE_INVALID")
    baseline_lsn, baseline_lsn_value = _lsn(value.baseline_wal_lsn, code="RECEIVER_PIN_BASE_LSN_INVALID")
    chain_start, chain_start_value = _lsn(
        value.wal_chain_start_lsn, code="RECEIVER_PIN_WAL_CHAIN_START_INVALID"
    )
    backup_end, backup_end_value = _lsn(
        value.base_backup_end_lsn, code="RECEIVER_PIN_BASE_END_LSN_INVALID"
    )
    if (
        backup_end_value <= baseline_lsn_value
        or chain_start_value % segment_size
        or chain_start_value > baseline_lsn_value
        or baseline_lsn_value >= chain_start_value + segment_size
    ):
        _fail("RECEIVER_PIN_BASELINE_WAL_GEOMETRY_INVALID")
    normalised = PhysicalWalReceiverStagingPin(
        source_site=value.source_site,
        destination_site=value.destination_site,
        source_public_key=source_key,
        destination_age_recipient=recipient,
        campaign_id=campaign,
        release_sha=release,
        writer_epoch=epoch,
        writer_lease_id=lease,
        witnessed_term_proof_sha256=proof,
        baseline_generation_id=generation,
        baseline_manifest_sha256=baseline_hash,
        database_system_identifier=system_identifier,
        timeline_id=value.timeline_id,
        wal_segment_size_bytes=segment_size,
        baseline_wal_lsn=baseline_lsn,
        wal_chain_start_lsn=chain_start,
        base_backup_end_lsn=backup_end,
        route_binding_sha256=value.route_binding_sha256,
    )
    expected_route_hash = hashlib.sha256(
        _canonical(_route_payload(normalised), code="ROUTE_BINDING_CANONICAL_INVALID")
    ).hexdigest()
    if verify_route_hash and _sha256(
        value.route_binding_sha256, code="RECEIVER_PIN_ROUTE_BINDING_INVALID"
    ) != expected_route_hash:
        _fail("RECEIVER_PIN_ROUTE_BINDING_INVALID")
    return PhysicalWalReceiverStagingPin(
        **{**normalised.__dict__, "route_binding_sha256": expected_route_hash}
    )


def build_physical_wal_receiver_staging_pin(
    *,
    source_site: str,
    destination_site: str,
    source_public_key: bytes,
    destination_age_recipient: str,
    campaign_id: str,
    release_sha: str,
    writer_epoch: int,
    writer_lease_id: str,
    witnessed_term_proof_sha256: str,
    baseline_generation_id: str,
    baseline_manifest_sha256: str,
    database_system_identifier: str,
    timeline_id: int,
    wal_segment_size_bytes: int,
    baseline_wal_lsn: str,
    wal_chain_start_lsn: str,
    base_backup_end_lsn: str,
) -> PhysicalWalReceiverStagingPin:
    """Construct a normalized root pin; this does not contact a Witness."""

    provisional = PhysicalWalReceiverStagingPin(
        source_site=source_site,
        destination_site=destination_site,
        source_public_key=source_public_key,
        destination_age_recipient=destination_age_recipient,
        campaign_id=campaign_id,
        release_sha=release_sha,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        database_system_identifier=database_system_identifier,
        timeline_id=timeline_id,
        wal_segment_size_bytes=wal_segment_size_bytes,
        baseline_wal_lsn=baseline_wal_lsn,
        wal_chain_start_lsn=wal_chain_start_lsn,
        base_backup_end_lsn=base_backup_end_lsn,
        route_binding_sha256="0" * 64,
    )
    normalised = _normalise_pin(provisional, verify_route_hash=False)
    return PhysicalWalReceiverStagingPin(
        **{
            **normalised.__dict__,
            "route_binding_sha256": hashlib.sha256(
                _canonical(_route_payload(normalised), code="ROUTE_BINDING_CANONICAL_INVALID")
            ).hexdigest(),
        }
    )


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
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail(code)
    return resolved


def _normalise_config(value: object) -> _NormalisedConfig:
    if type(value) is not PhysicalWalReceiverStagingConfig:
        _fail("RECEIVER_STAGING_CONFIG_INVALID")
    receiver = _secure_root(value.receiver_root, code="RECEIVER_ROOT_UNSAFE")
    state_root = _secure_root(value.state_root, code="RECEIVER_STATE_ROOT_UNSAFE")
    try:
        receiver.relative_to(state_root)
        overlap = True
    except ValueError:
        try:
            state_root.relative_to(receiver)
            overlap = True
        except ValueError:
            overlap = False
    if overlap:
        _fail("RECEIVER_ROOTS_OVERLAP")
    return _NormalisedConfig(receiver_root=receiver, state_root=state_root)


def _secure_child(parent: Path, name: str, *, create: bool) -> Path:
    if not _SAFE_COMPONENT_RE.fullmatch(name):
        _fail("LOCAL_PATH_COMPONENT_INVALID")
    path = parent / name
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            _fail("LOCAL_DIRECTORY_CREATE_FAILED")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("LOCAL_DIRECTORY_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("LOCAL_DIRECTORY_UNSAFE")
    return path


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("LOCAL_PLATFORM_NO_DIRECTORY_FSYNC")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail("LOCAL_DIRECTORY_FSYNC_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("LOCAL_DIRECTORY_FSYNC_FAILED")
    finally:
        os.close(descriptor)


def _open_new_file(path: Path, *, code: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LOCAL_PLATFORM_NO_NOFOLLOW")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        _fail(code)
    except OSError:
        _fail("LOCAL_FILE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("LOCAL_FILE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_existing_file(path: Path, *, code: str) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("LOCAL_PLATFORM_NO_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _fail(code)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            _fail("LOCAL_FILE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(code)
            view = view[written:]
    except OSError:
        _fail(code)


def _digest_fd(descriptor: int, *, code: str) -> tuple[str, int]:
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o400}
        ):
            _fail("LOCAL_FILE_UNSAFE")
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, MAX_PHYSICAL_WAL_RECEIVER_IO_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        if total != metadata.st_size:
            _fail(code)
        return digest.hexdigest(), total
    except OSError:
        _fail(code)


def _freeze_file(descriptor: int) -> None:
    try:
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o400
        ):
            _fail("LOCAL_FILE_UNSAFE")
    except OSError:
        _fail("LOCAL_FILE_FREEZE_FAILED")


def _hash_existing_file(path: Path, *, code: str) -> tuple[str, int]:
    descriptor = _open_existing_file(path, code=code)
    try:
        return _digest_fd(descriptor, code=code)
    finally:
        os.close(descriptor)


def _write_canonical_o_excl(path: Path, value: Mapping[str, Any], *, code: str) -> bytes:
    payload = _canonical(value, code=code)
    descriptor = _open_new_file(path, code=code)
    try:
        _write_all(descriptor, payload, code=code)
        _freeze_file(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)
    return payload


def _read_canonical_json(path: Path, *, code: str) -> tuple[dict[str, Any], bytes]:
    descriptor = _open_existing_file(path, code=code)
    try:
        try:
            size = os.fstat(descriptor).st_size
            if not 1 <= size <= MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES:
                _fail(code)
            payload = bytearray()
            while len(payload) < size:
                chunk = os.read(descriptor, min(MAX_PHYSICAL_WAL_RECEIVER_IO_CHUNK_BYTES, size - len(payload)))
                if not chunk:
                    _fail(code)
                payload.extend(chunk)
            if os.read(descriptor, 1):
                _fail(code)
        except OSError:
            _fail(code)
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalWalReceiverStagingError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict) or _canonical(value, code=code) != raw:
        _fail(code)
    return value, raw


def _reader_method(value: object) -> Any:
    method = getattr(value, "read_exact_to_fd", None)
    if not callable(method):
        _fail("EXACT_VERSION_READER_REQUIRED")
    return method


def _decryptor_method(value: object) -> Any:
    method = getattr(value, "decrypt_to_fd", None)
    if not callable(method):
        _fail("DECRYPTOR_REQUIRED")
    return method


def _normalise_readback(value: object, *, plan: _ArtifactPlan) -> PhysicalWalExactVersionReadback:
    if type(value) is not PhysicalWalExactVersionReadback:
        _fail("EXACT_VERSION_READER_RECEIPT_INVALID")
    object_key = _object_key(value.object_key, code="EXACT_VERSION_READER_RECEIPT_INVALID")
    version_id = _version_id(value.version_id, code="EXACT_VERSION_READER_RECEIPT_INVALID")
    digest = _sha256(value.ciphertext_sha256, code="EXACT_VERSION_READER_RECEIPT_INVALID")
    size = _positive_int(value.ciphertext_bytes, code="EXACT_VERSION_READER_RECEIPT_INVALID")
    if (
        object_key != plan.object.object_key
        or version_id != plan.object.version_id
        or digest != plan.object.ciphertext_sha256
        or size != plan.object.ciphertext_bytes
    ):
        _fail("EXACT_VERSION_READER_RECEIPT_FOREIGN_OR_ALIAS")
    return PhysicalWalExactVersionReadback(object_key, version_id, digest, size)


def _normalise_decrypt_readback(value: object, *, plan: _ArtifactPlan, pin: PhysicalWalReceiverStagingPin) -> PhysicalWalDecryptionReadback:
    if type(value) is not PhysicalWalDecryptionReadback:
        _fail("DECRYPTOR_RECEIPT_INVALID")
    object_key = _object_key(value.object_key, code="DECRYPTOR_RECEIPT_INVALID")
    version_id = _version_id(value.version_id, code="DECRYPTOR_RECEIPT_INVALID")
    recipient = _text(value.age_recipient, pattern=AGE_RECIPIENT_RE, code="DECRYPTOR_RECEIPT_INVALID")
    digest = _sha256(value.plaintext_sha256, code="DECRYPTOR_RECEIPT_INVALID")
    size = _positive_int(value.plaintext_bytes, code="DECRYPTOR_RECEIPT_INVALID")
    if (
        object_key != plan.object.object_key
        or version_id != plan.object.version_id
        or recipient != pin.destination_age_recipient
        or plan.object.age_recipient != pin.destination_age_recipient
    ):
        _fail("DECRYPTOR_RECEIPT_FOREIGN_OR_WRONG_RECIPIENT")
    return PhysicalWalDecryptionReadback(object_key, version_id, recipient, digest, size)


def _ensure_parent_for_relative(candidate: Path, relative_path: str) -> Path:
    parts = relative_path.split("/")
    if not parts or any(not _SAFE_COMPONENT_RE.fullmatch(part) for part in parts):
        _fail("CANDIDATE_ARTIFACT_PATH_INVALID")
    parent = candidate
    for part in parts[:-1]:
        parent = _secure_child(parent, part, create=True)
    return parent / parts[-1]


def _existing_path_for_relative(candidate: Path, relative_path: str) -> Path:
    """Resolve an existing candidate artifact without traversing a symlink.

    ``O_NOFOLLOW`` protects the leaf file, but not an intermediate directory.
    Validate each component again before reading a completed candidate, because
    a retry must never follow a substituted ``material``/``wal`` directory.
    """

    parts = relative_path.split("/")
    if not parts or any(not _SAFE_COMPONENT_RE.fullmatch(part) for part in parts):
        _fail("CANDIDATE_ARTIFACT_PATH_INVALID")
    parent = candidate
    for part in parts[:-1]:
        parent = _secure_child(parent, part, create=False)
    return parent / parts[-1]


def _stage_artifact(
    *,
    candidate: Path,
    plan: _ArtifactPlan,
    pin: PhysicalWalReceiverStagingPin,
    reader: object,
    decryptor: object,
) -> _StagedArtifact:
    ciphertext_path = _ensure_parent_for_relative(candidate, plan.ciphertext_relative_path)
    plaintext_path = _ensure_parent_for_relative(candidate, plan.plaintext_relative_path)
    reader_call = _reader_method(reader)
    decrypt_call = _decryptor_method(decryptor)
    ciphertext_fd = _open_new_file(ciphertext_path, code="CANDIDATE_CIPHERTEXT_ALREADY_EXISTS")
    try:
        try:
            readback = reader_call(
                object_key=plan.object.object_key,
                version_id=plan.object.version_id,
                destination_fd=ciphertext_fd,
            )
        except PhysicalWalReceiverStagingError:
            raise
        except Exception as exc:
            raise PhysicalWalReceiverStagingError("EXACT_VERSION_READER_FAILED") from exc
        expected_readback = _normalise_readback(readback, plan=plan)
        actual_cipher_hash, actual_cipher_size = _digest_fd(
            ciphertext_fd, code="CIPHERTEXT_READBACK_UNREADABLE"
        )
        if (
            actual_cipher_hash != expected_readback.ciphertext_sha256
            or actual_cipher_size != expected_readback.ciphertext_bytes
        ):
            _fail("CIPHERTEXT_READBACK_HASH_OR_SIZE_MISMATCH")
        _freeze_file(ciphertext_fd)
    finally:
        os.close(ciphertext_fd)
    ciphertext_read_fd = _open_existing_file(ciphertext_path, code="CIPHERTEXT_STAGING_FILE_UNSAFE")
    try:
        reread_cipher_hash, reread_cipher_size = _digest_fd(
            ciphertext_read_fd, code="CIPHERTEXT_STAGING_FILE_UNSAFE"
        )
        if (
            reread_cipher_hash != plan.object.ciphertext_sha256
            or reread_cipher_size != plan.object.ciphertext_bytes
        ):
            _fail("CIPHERTEXT_PREDECRYPT_HASH_OR_SIZE_MISMATCH")
        try:
            os.lseek(ciphertext_read_fd, 0, os.SEEK_SET)
        except OSError:
            _fail("CIPHERTEXT_STAGING_FILE_UNSAFE")
        plaintext_fd = _open_new_file(plaintext_path, code="CANDIDATE_PLAINTEXT_ALREADY_EXISTS")
        try:
            try:
                decrypt_readback = decrypt_call(
                    ciphertext_fd=ciphertext_read_fd,
                    destination_fd=plaintext_fd,
                    object_key=plan.object.object_key,
                    version_id=plan.object.version_id,
                    expected_age_recipient=pin.destination_age_recipient,
                )
            except PhysicalWalReceiverStagingError:
                raise
            except Exception as exc:
                raise PhysicalWalReceiverStagingError("DECRYPTOR_FAILED") from exc
            expected_plain = _normalise_decrypt_readback(decrypt_readback, plan=plan, pin=pin)
            actual_plain_hash, actual_plain_size = _digest_fd(
                plaintext_fd, code="PLAINTEXT_READBACK_UNREADABLE"
            )
            if (
                actual_plain_hash != expected_plain.plaintext_sha256
                or actual_plain_size != expected_plain.plaintext_bytes
            ):
                _fail("PLAINTEXT_READBACK_HASH_OR_SIZE_MISMATCH")
            if plan.expected_plaintext_sha256 is not None and (
                actual_plain_hash != plan.expected_plaintext_sha256
                or actual_plain_size != plan.expected_plaintext_bytes
            ):
                _fail("PLAINTEXT_DOES_NOT_MATCH_SIGNED_INVENTORY_BINDING")
            if plan.kind == "wal":
                _verify_staged_wal_geometry(plan=plan, plaintext_bytes=actual_plain_size, pin=pin)
            _freeze_file(plaintext_fd)
            post_decrypt_cipher_hash, post_decrypt_cipher_size = _digest_fd(
                ciphertext_read_fd, code="CIPHERTEXT_STAGING_FILE_UNSAFE"
            )
            if (
                post_decrypt_cipher_hash != plan.object.ciphertext_sha256
                or post_decrypt_cipher_size != plan.object.ciphertext_bytes
            ):
                _fail("CIPHERTEXT_POSTDECRYPT_HASH_OR_SIZE_MISMATCH")
        finally:
            os.close(plaintext_fd)
    finally:
        os.close(ciphertext_read_fd)
    _fsync_directory(ciphertext_path.parent)
    if plaintext_path.parent != ciphertext_path.parent:
        _fsync_directory(plaintext_path.parent)
    return _StagedArtifact(
        plan=plan,
        plaintext_sha256=actual_plain_hash,
        plaintext_bytes=actual_plain_size,
    )


def _verify_staged_wal_geometry(
    *,
    plan: _ArtifactPlan,
    plaintext_bytes: int,
    pin: PhysicalWalReceiverStagingPin,
) -> None:
    if (
        plan.wal_segment_name is None
        or plan.wal_start_lsn is None
        or plan.wal_end_lsn is None
        or _WAL_SEGMENT_NAME_RE.fullmatch(plan.wal_segment_name) is None
    ):
        _fail("WAL_STAGING_PLAN_INVALID")
    start_lsn, start_value = _lsn(plan.wal_start_lsn, code="WAL_START_LSN_INVALID")
    end_lsn, end_value = _lsn(plan.wal_end_lsn, code="WAL_END_LSN_INVALID")
    expected_name = _wal_filename_for(
        timeline_id=pin.timeline_id,
        start_lsn_value=start_value,
        segment_size=pin.wal_segment_size_bytes,
    )
    if (
        plan.wal_segment_name != expected_name
        or end_value != start_value + pin.wal_segment_size_bytes
        or plaintext_bytes != pin.wal_segment_size_bytes
    ):
        _fail("STAGED_WAL_FILENAME_RANGE_OR_SIZE_INVALID")
    if Path(plan.plaintext_relative_path).name != plan.wal_segment_name:
        _fail("STAGED_WAL_FILENAME_PATH_INVALID")
    if start_lsn != plan.wal_start_lsn or end_lsn != plan.wal_end_lsn:
        _fail("STAGED_WAL_LSN_NOT_CANONICAL")


def _plan_artifacts(
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
) -> tuple[_ArtifactPlan, ...]:
    plans: list[_ArtifactPlan] = [
        _ArtifactPlan(
            artifact_id="base-backup",
            kind="base-backup",
            object=bundle.baseline.base_backup_object,
            ciphertext_relative_path="material/base-backup.age",
            plaintext_relative_path="material/base-backup.plain",
            expected_plaintext_sha256=None,
            expected_plaintext_bytes=None,
            wal_segment_name=None,
            wal_start_lsn=None,
            wal_end_lsn=None,
        )
    ]
    seen_wal: set[str] = set()
    for wal_manifest in bundle.wal_manifests:
        for segment in wal_manifest.segments:
            if segment.wal_segment_name in seen_wal:
                _fail("BUNDLE_WAL_FILENAME_REPLAYED")
            seen_wal.add(segment.wal_segment_name)
            plans.append(
                _ArtifactPlan(
                    artifact_id="wal-" + segment.wal_segment_name,
                    kind="wal",
                    object=segment.object,
                    ciphertext_relative_path="material/wal/" + segment.wal_segment_name + ".age",
                    plaintext_relative_path="material/wal/" + segment.wal_segment_name,
                    expected_plaintext_sha256=None,
                    expected_plaintext_bytes=pin.wal_segment_size_bytes,
                    wal_segment_name=segment.wal_segment_name,
                    wal_start_lsn=segment.start_lsn,
                    wal_end_lsn=segment.end_lsn,
                )
            )
    for shard in bundle.blob_frontier.inventory_shards:
        item = f"{shard.ordinal:08d}"
        plans.append(
            _ArtifactPlan(
                artifact_id="blob-inventory-" + item,
                kind="blob-inventory",
                object=shard.object,
                ciphertext_relative_path="material/blob-inventory/" + item + ".age",
                plaintext_relative_path="material/blob-inventory/" + item + ".inventory",
                expected_plaintext_sha256=shard.plaintext_sha256,
                expected_plaintext_bytes=shard.plaintext_bytes,
                wal_segment_name=None,
                wal_start_lsn=None,
                wal_end_lsn=None,
            )
        )
    object_pairs = [(item.object.object_key, item.object.version_id) for item in plans]
    paths = [
        path
        for item in plans
        for path in (item.ciphertext_relative_path, item.plaintext_relative_path)
    ]
    if len(set(object_pairs)) != len(object_pairs) or len(set(paths)) != len(paths):
        _fail("BUNDLE_OBJECT_OR_PATH_REPLAYED")
    if any(item.object.age_recipient != pin.destination_age_recipient for item in plans):
        _fail("BUNDLE_DESTINATION_RECIPIENT_FOREIGN")
    return tuple(plans)


def _bundle_for_pin(
    value: object,
    *,
    pin: PhysicalWalReceiverStagingPin,
) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError) as exc:
        raise PhysicalWalReceiverStagingError("BUNDLE_UNVERIFIED_OR_PARTIAL") from exc
    baseline = bundle.baseline
    term = baseline.writer_term
    if (
        baseline.source_public_key != pin.source_public_key
        or baseline.source_site != pin.source_site
        or baseline.destination_site != pin.destination_site
        or baseline.campaign_id != pin.campaign_id
        or baseline.release_sha != pin.release_sha
        or term.epoch != pin.writer_epoch
        or term.lease_id != pin.writer_lease_id
        or term.witnessed_term_proof_sha256 != pin.witnessed_term_proof_sha256
        or baseline.baseline_generation_id != pin.baseline_generation_id
        or baseline.manifest_sha256 != pin.baseline_manifest_sha256
        or baseline.database_system_identifier != pin.database_system_identifier
        or baseline.timeline_id != pin.timeline_id
        or baseline.wal_segment_size_bytes != pin.wal_segment_size_bytes
        or baseline.baseline_wal_lsn != pin.baseline_wal_lsn
        or baseline.wal_chain_start_lsn != pin.wal_chain_start_lsn
        or baseline.base_backup_end_lsn != pin.base_backup_end_lsn
    ):
        _fail("BUNDLE_PINNED_ROUTE_BASELINE_OR_TERM_MISMATCH")
    if not bundle.blob_frontier.objects_complete:
        _fail("BUNDLE_BLOB_FRONTIER_INCOMPLETE")
    return bundle


def _bundle_id(bundle: VerifiedPhysicalWalObjectStorageBundle, pin: PhysicalWalReceiverStagingPin) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA,
                "route_binding_sha256": pin.route_binding_sha256,
                "manifest_sha256es": list(bundle.manifest_sha256es),
            },
            code="BUNDLE_ID_CANONICAL_INVALID",
        )
    ).hexdigest()


def _candidate_directory(config: _NormalisedConfig, bundle_id: str, *, create: bool) -> Path:
    candidates = _secure_child(config.receiver_root, "candidates", create=True)
    if not create:
        return candidates / bundle_id
    path = candidates / bundle_id
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        _fail("CANDIDATE_ALREADY_EXISTS")
    except OSError:
        _fail("CANDIDATE_CREATE_FAILED")
    try:
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("CANDIDATE_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("CANDIDATE_UNSAFE")
    _fsync_directory(candidates)
    return path


def _existing_candidate(config: _NormalisedConfig, bundle_id: str) -> Path | None:
    candidates = _secure_child(config.receiver_root, "candidates", create=True)
    path = candidates / bundle_id
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("CANDIDATE_UNSAFE")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail("CANDIDATE_UNSAFE")
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("CANDIDATE_UNSAFE")
    return path


def _quarantine_partial_candidate(config: _NormalisedConfig, candidate: Path, bundle_id: str) -> None:
    quarantine = _secure_child(config.receiver_root, "quarantine", create=True)
    target = quarantine / (bundle_id + "-" + secrets.token_hex(8))
    try:
        os.rename(candidate, target)
    except OSError:
        _fail("PARTIAL_CANDIDATE_QUARANTINE_FAILED")
    _fsync_directory(candidate.parent)
    _fsync_directory(quarantine)


def _artifact_record(item: _StagedArtifact) -> dict[str, Any]:
    plan = item.plan
    return {
        "artifact_id": plan.artifact_id,
        "kind": plan.kind,
        "object_key": plan.object.object_key,
        "version_id": plan.object.version_id,
        "ciphertext_relative_path": plan.ciphertext_relative_path,
        "ciphertext_sha256": plan.object.ciphertext_sha256,
        "ciphertext_bytes": plan.object.ciphertext_bytes,
        "plaintext_relative_path": plan.plaintext_relative_path,
        "plaintext_sha256": item.plaintext_sha256,
        "plaintext_bytes": item.plaintext_bytes,
        "wal_segment_name": plan.wal_segment_name,
        "wal_start_lsn": plan.wal_start_lsn,
        "wal_end_lsn": plan.wal_end_lsn,
    }


def _object_versions(plans: Sequence[_ArtifactPlan]) -> list[dict[str, str]]:
    return [
        {"object_key": plan.object.object_key, "version_id": plan.object.version_id}
        for plan in plans
    ]


def _build_stage_receipt(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    bundle_id: str,
    staged: Sequence[_StagedArtifact],
) -> dict[str, Any]:
    unsigned = {
        "schema": PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA,
        "status": PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
        "bundle_id": bundle_id,
        "route_binding_sha256": pin.route_binding_sha256,
        "candidate_path": str(candidate),
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "object_versions": _object_versions(tuple(item.plan for item in staged)),
        "artifacts": [_artifact_record(item) for item in staged],
    }
    return {
        **unsigned,
        "receipt_sha256": hashlib.sha256(
            _canonical(unsigned, code="STAGE_RECEIPT_CANONICAL_INVALID")
        ).hexdigest(),
    }


def _validate_stage_receipt(
    *,
    candidate: Path,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    bundle_id: str,
    plans: Sequence[_ArtifactPlan],
) -> tuple[dict[str, Any], str]:
    receipt_path = candidate / "stage-receipt.json"
    receipt, raw = _read_canonical_json(receipt_path, code="STAGE_RECEIPT_INVALID")
    value = _exact_mapping(receipt, fields=_RECEIPT_FIELDS, code="STAGE_RECEIPT_INVALID")
    if (
        value["schema"] != PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA
        or value["status"] != PHYSICAL_WAL_RECEIVER_STAGING_STATUS
        or value["bundle_id"] != bundle_id
        or value["route_binding_sha256"] != pin.route_binding_sha256
        or value["candidate_path"] != str(candidate)
    ):
        _fail("STAGE_RECEIPT_FOREIGN_OR_STATUS_INVALID")
    if not isinstance(value["manifest_sha256es"], list) or tuple(value["manifest_sha256es"]) != bundle.manifest_sha256es:
        _fail("STAGE_RECEIPT_MANIFEST_BINDING_INVALID")
    if not isinstance(value["object_versions"], list) or len(value["object_versions"]) != len(plans):
        _fail("STAGE_RECEIPT_OBJECT_BINDING_INVALID")
    expected_objects = _object_versions(plans)
    for actual, expected in zip(value["object_versions"], expected_objects, strict=True):
        if _exact_mapping(actual, fields=_OBJECT_VERSION_FIELDS, code="STAGE_RECEIPT_OBJECT_BINDING_INVALID") != expected:
            _fail("STAGE_RECEIPT_OBJECT_BINDING_INVALID")
    if not isinstance(value["artifacts"], list) or len(value["artifacts"]) != len(plans):
        _fail("STAGE_RECEIPT_ARTIFACT_BINDING_INVALID")
    for actual, plan in zip(value["artifacts"], plans, strict=True):
        record = _exact_mapping(actual, fields=_ARTIFACT_FIELDS, code="STAGE_RECEIPT_ARTIFACT_BINDING_INVALID")
        expected_static = {
            "artifact_id": plan.artifact_id,
            "kind": plan.kind,
            "object_key": plan.object.object_key,
            "version_id": plan.object.version_id,
            "ciphertext_relative_path": plan.ciphertext_relative_path,
            "ciphertext_sha256": plan.object.ciphertext_sha256,
            "ciphertext_bytes": plan.object.ciphertext_bytes,
            "plaintext_relative_path": plan.plaintext_relative_path,
            "wal_segment_name": plan.wal_segment_name,
            "wal_start_lsn": plan.wal_start_lsn,
            "wal_end_lsn": plan.wal_end_lsn,
        }
        if any(record[key] != expected for key, expected in expected_static.items()):
            _fail("STAGE_RECEIPT_ARTIFACT_BINDING_INVALID")
        plain_hash = _sha256(record["plaintext_sha256"], code="STAGE_RECEIPT_ARTIFACT_BINDING_INVALID")
        plain_bytes = _positive_int(record["plaintext_bytes"], code="STAGE_RECEIPT_ARTIFACT_BINDING_INVALID")
        if plan.expected_plaintext_sha256 is not None and (
            plain_hash != plan.expected_plaintext_sha256 or plain_bytes != plan.expected_plaintext_bytes
        ):
            _fail("STAGE_RECEIPT_INVENTORY_BINDING_INVALID")
        if plan.kind == "wal":
            _verify_staged_wal_geometry(plan=plan, plaintext_bytes=plain_bytes, pin=pin)
        ciphertext_path = _existing_path_for_relative(
            candidate, plan.ciphertext_relative_path
        )
        plaintext_path = _existing_path_for_relative(
            candidate, plan.plaintext_relative_path
        )
        actual_cipher_hash, actual_cipher_bytes = _hash_existing_file(
            ciphertext_path, code="STAGED_CIPHERTEXT_TAMPERED_OR_MISSING"
        )
        actual_plain_hash, actual_plain_bytes = _hash_existing_file(
            plaintext_path, code="STAGED_PLAINTEXT_TAMPERED_OR_MISSING"
        )
        if (
            actual_cipher_hash != plan.object.ciphertext_sha256
            or actual_cipher_bytes != plan.object.ciphertext_bytes
            or actual_plain_hash != plain_hash
            or actual_plain_bytes != plain_bytes
        ):
            _fail("STAGED_ARTIFACT_HASH_OR_SIZE_MISMATCH")
    unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
    receipt_hash = _sha256(value["receipt_sha256"], code="STAGE_RECEIPT_INVALID")
    if hashlib.sha256(_canonical(unsigned, code="STAGE_RECEIPT_CANONICAL_INVALID")).hexdigest() != receipt_hash:
        _fail("STAGE_RECEIPT_HASH_INVALID")
    return value, receipt_hash


def _record_path(state_root: Path, *, kind: str, identifier: str) -> Path:
    if SHA256_RE.fullmatch(identifier) is None:
        _fail("LOCAL_RECORD_IDENTIFIER_INVALID")
    consumed = _secure_child(state_root, "consumed", create=True)
    subgroup = _secure_child(consumed, kind, create=True)
    return subgroup / (identifier + ".json")


def _read_or_create_record(path: Path, expected: Mapping[str, Any], *, code: str) -> None:
    payload = _canonical(expected, code=code)
    try:
        descriptor = _open_new_file(path, code="LOCAL_RECORD_EXISTS")
    except PhysicalWalReceiverStagingError as exc:
        if exc.code != "LOCAL_RECORD_EXISTS":
            raise
        actual, raw = _read_canonical_json(path, code=code)
        if raw != payload or actual != dict(expected):
            _fail(code)
        return
    try:
        _write_all(descriptor, payload, code=code)
        _freeze_file(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _manifest_consume_record(
    *,
    bundle_id: str,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    stage_receipt_sha256: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_RECEIVER_CONSUME_RECORD_SCHEMA,
        "record_kind": "manifest",
        "bundle_id": bundle_id,
        "route_binding_sha256": pin.route_binding_sha256,
        "candidate_path": str(candidate),
        "stage_receipt_sha256": stage_receipt_sha256,
        "manifest_sha256": manifest_sha256,
        "object_key": None,
        "version_id": None,
    }


def _object_consume_record(
    *,
    bundle_id: str,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    stage_receipt_sha256: str,
    object_key: str,
    version_id: str,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_RECEIVER_CONSUME_RECORD_SCHEMA,
        "record_kind": "object-version",
        "bundle_id": bundle_id,
        "route_binding_sha256": pin.route_binding_sha256,
        "candidate_path": str(candidate),
        "stage_receipt_sha256": stage_receipt_sha256,
        "manifest_sha256": None,
        "object_key": object_key,
        "version_id": version_id,
    }


def _completion_record(
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    bundle_id: str,
    stage_receipt_sha256: str,
    plans: Sequence[_ArtifactPlan],
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_WAL_RECEIVER_COMPLETION_RECORD_SCHEMA,
        "status": PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
        "bundle_id": bundle_id,
        "route_binding_sha256": pin.route_binding_sha256,
        "candidate_path": str(candidate),
        "stage_receipt_sha256": stage_receipt_sha256,
        "manifest_sha256es": list(bundle.manifest_sha256es),
        "object_versions": _object_versions(plans),
    }


def _claim_local_consumption(
    *,
    config: _NormalisedConfig,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    bundle_id: str,
    stage_receipt_sha256: str,
    plans: Sequence[_ArtifactPlan],
) -> None:
    for manifest_hash in bundle.manifest_sha256es:
        path = _record_path(config.state_root, kind="manifests", identifier=manifest_hash)
        _read_or_create_record(
            path,
            _manifest_consume_record(
                bundle_id=bundle_id,
                pin=pin,
                candidate=candidate,
                stage_receipt_sha256=stage_receipt_sha256,
                manifest_sha256=manifest_hash,
            ),
            code="LOCAL_MANIFEST_REPLAY_OR_CONSUME_CONFLICT",
        )
    for plan in plans:
        object_identifier = hashlib.sha256(
            _canonical(
                {"object_key": plan.object.object_key, "version_id": plan.object.version_id},
                code="OBJECT_RECORD_CANONICAL_INVALID",
            )
        ).hexdigest()
        path = _record_path(config.state_root, kind="objects", identifier=object_identifier)
        _read_or_create_record(
            path,
            _object_consume_record(
                bundle_id=bundle_id,
                pin=pin,
                candidate=candidate,
                stage_receipt_sha256=stage_receipt_sha256,
                object_key=plan.object.object_key,
                version_id=plan.object.version_id,
            ),
            code="LOCAL_OBJECT_VERSION_REPLAY_OR_CONSUME_CONFLICT",
        )
    completed = _secure_child(config.state_root, "completed", create=True)
    _read_or_create_record(
        completed / (bundle_id + ".json"),
        _completion_record(
            bundle=bundle,
            pin=pin,
            candidate=candidate,
            bundle_id=bundle_id,
            stage_receipt_sha256=stage_receipt_sha256,
            plans=plans,
        ),
        code="LOCAL_COMPLETION_RECORD_CONFLICT",
    )


def _candidate_has_completion(
    *,
    config: _NormalisedConfig,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    pin: PhysicalWalReceiverStagingPin,
    candidate: Path,
    bundle_id: str,
    stage_receipt_sha256: str,
    plans: Sequence[_ArtifactPlan],
) -> bool:
    completed = _secure_child(config.state_root, "completed", create=True)
    path = completed / (bundle_id + ".json")
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        _fail("LOCAL_COMPLETION_RECORD_UNSAFE")
    expected = _completion_record(
        bundle=bundle,
        pin=pin,
        candidate=candidate,
        bundle_id=bundle_id,
        stage_receipt_sha256=stage_receipt_sha256,
        plans=plans,
    )
    actual, raw = _read_canonical_json(path, code="LOCAL_COMPLETION_RECORD_CONFLICT")
    if raw != _canonical(expected, code="LOCAL_COMPLETION_RECORD_CONFLICT") or actual != expected:
        _fail("LOCAL_COMPLETION_RECORD_CONFLICT")
    return True


def _staged_result(
    *,
    candidate: Path,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    bundle_id: str,
    idempotent: bool,
) -> PhysicalWalReceiverStagingResult:
    return PhysicalWalReceiverStagingResult(
        status=PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
        reason_codes=(),
        candidate_path=candidate,
        stage_receipt_path=candidate / "stage-receipt.json",
        bundle_id=bundle_id,
        receiver_site=bundle.baseline.destination_site,
        manifest_sha256es=bundle.manifest_sha256es,
        idempotent=idempotent,
    )


def _stage_or_resume(
    *,
    bundle_value: object,
    pin_value: object,
    config_value: object,
    exact_version_reader: object,
    decryptor: object,
) -> PhysicalWalReceiverStagingResult:
    _reader_method(exact_version_reader)
    _decryptor_method(decryptor)
    pin = _normalise_pin(pin_value, verify_route_hash=True)
    config = _normalise_config(config_value)
    bundle = _bundle_for_pin(bundle_value, pin=pin)
    plans = _plan_artifacts(bundle, pin)
    bundle_id = _bundle_id(bundle, pin)
    existing = _existing_candidate(config, bundle_id)
    if existing is not None:
        try:
            _receipt, stage_receipt_sha256 = _validate_stage_receipt(
                candidate=existing,
                bundle=bundle,
                pin=pin,
                bundle_id=bundle_id,
                plans=plans,
            )
        except PhysicalWalReceiverStagingError as exc:
            if exc.code == "STAGE_RECEIPT_INVALID":
                _quarantine_partial_candidate(config, existing, bundle_id)
            else:
                raise
        else:
            completed = _candidate_has_completion(
                config=config,
                bundle=bundle,
                pin=pin,
                candidate=existing,
                bundle_id=bundle_id,
                stage_receipt_sha256=stage_receipt_sha256,
                plans=plans,
            )
            if not completed:
                _claim_local_consumption(
                    config=config,
                    bundle=bundle,
                    pin=pin,
                    candidate=existing,
                    bundle_id=bundle_id,
                    stage_receipt_sha256=stage_receipt_sha256,
                    plans=plans,
                )
            return _staged_result(
                candidate=existing,
                bundle=bundle,
                bundle_id=bundle_id,
                idempotent=True,
            )
    candidate = _candidate_directory(config, bundle_id, create=True)
    staged = tuple(
        _stage_artifact(
            candidate=candidate,
            plan=plan,
            pin=pin,
            reader=exact_version_reader,
            decryptor=decryptor,
        )
        for plan in plans
    )
    receipt = _build_stage_receipt(
        bundle=bundle,
        pin=pin,
        candidate=candidate,
        bundle_id=bundle_id,
        staged=staged,
    )
    _write_canonical_o_excl(candidate / "stage-receipt.json", receipt, code="STAGE_RECEIPT_ALREADY_EXISTS")
    _fsync_directory(candidate)
    _receipt, stage_receipt_sha256 = _validate_stage_receipt(
        candidate=candidate,
        bundle=bundle,
        pin=pin,
        bundle_id=bundle_id,
        plans=plans,
    )
    _claim_local_consumption(
        config=config,
        bundle=bundle,
        pin=pin,
        candidate=candidate,
        bundle_id=bundle_id,
        stage_receipt_sha256=stage_receipt_sha256,
        plans=plans,
    )
    return _staged_result(
        candidate=candidate,
        bundle=bundle,
        bundle_id=bundle_id,
        idempotent=False,
    )


def stage_physical_wal_object_storage_bundle(
    *,
    bundle: object,
    pin: object,
    config: object,
    exact_version_reader: object,
    decryptor: object,
) -> PhysicalWalReceiverStagingResult:
    """Stage a pinned physical bundle locally, never start/recover/promote PostgreSQL.

    A blocked result contains only a stable non-secret reason code.  A staged
    result is explicitly not a remote replay or synchronous-apply receipt.
    """

    try:
        return _stage_or_resume(
            bundle_value=bundle,
            pin_value=pin,
            config_value=config,
            exact_version_reader=exact_version_reader,
            decryptor=decryptor,
        )
    except PhysicalWalReceiverStagingError as exc:
        return PhysicalWalReceiverStagingResult(
            status=PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS,
            reason_codes=(exc.code,),
        )
    except OSError:
        return PhysicalWalReceiverStagingResult(
            status=PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS,
            reason_codes=("LOCAL_IO_FAILURE",),
        )
    except Exception:
        return PhysicalWalReceiverStagingResult(
            status=PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS,
            reason_codes=("UNEXPECTED_LOCAL_STAGING_FAILURE",),
        )


__all__ = (
    "MAX_PHYSICAL_WAL_RECEIVER_IO_CHUNK_BYTES",
    "MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES",
    "PHYSICAL_WAL_RECEIVER_BLOCKED_STATUS",
    "PHYSICAL_WAL_RECEIVER_COMPLETION_RECORD_SCHEMA",
    "PHYSICAL_WAL_RECEIVER_CONSUME_RECORD_SCHEMA",
    "PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_RECEIVER_STAGING_DEFAULT_ENABLED",
    "PHYSICAL_WAL_RECEIVER_STAGING_SCHEMA",
    "PHYSICAL_WAL_RECEIVER_STAGING_STATUS",
    "PhysicalWalDecryptionReadback",
    "PhysicalWalDecryptor",
    "PhysicalWalExactVersionReadback",
    "PhysicalWalExactVersionReader",
    "PhysicalWalReceiverStagingConfig",
    "PhysicalWalReceiverStagingError",
    "PhysicalWalReceiverStagingPin",
    "PhysicalWalReceiverStagingResult",
    "build_physical_wal_receiver_staging_pin",
    "derive_physical_wal_receiver_staging_route_binding_sha256",
    "stage_physical_wal_object_storage_bundle",
)
