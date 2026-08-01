"""Root-local durable replay guard for V1 Witness current-term attestations.

This module implements only the persistence seam required by
``physical_operational_failover_v1_witness_term_revalidator``.  It deliberately
has no network, provider, database, traffic, writer, promotion, or failover
control.  In particular, a receipt means only that a request/attestation has
been durably recorded locally; it is never an authority to become writer.

The store is intentionally fail-closed.  Every reservation and consumption is
an fsync'd, append-only record.  A root-owned monotonic checkpoint outside the
mutable tree must attest the chain before and after every mutation, so a
whole-tree rollback cannot silently re-enable a replay.  There is no release,
delete, repair, or retry operation: an ambiguous external fetch burns its
revalidation identifier.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Protocol

from core import physical_operational_failover_v1_witness_term_revalidator as revalidator


__all__ = (
    "FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_FI",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_IR",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA",
    "PhysicalOperationalFailoverV1WitnessTermReplayCheckpoint",
    "PhysicalOperationalFailoverV1WitnessTermReplayGuard",
    "PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig",
    "PhysicalOperationalFailoverV1WitnessTermReplayGuardError",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-term-replay-guard-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DEFAULT_ENABLED = False

# Deployment owns this exact directory.  Callers can select neither a path nor
# a namespace; the local site is derived from the pinned V1 revalidator
# binding, and an installation binding makes a role switch fail closed.
FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-operational-failover-v1-witness-term-replay-guard"
)

PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_FI = (
    "webapp-fi-writer-admission"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_IR = (
    "webapp-ir-writer-admission"
)

_VERSION = 1
_MODE = "root-owned-v1-witness-current-term-durable-replay-guard-v1"
_LOCK_FILENAME = "guard.lock"
_ROOT_BINDING_FILENAME = "installation-binding.json"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_RECORDS_DIRECTORY = "records"
_MAX_RECORD_BYTES = 64 * 1024
_MAX_RECORDS = 16_384
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_RECORD_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.[A-Za-z0-9._-]+\.tmp$", re.ASCII)

_ROLE_SPECS: dict[str, tuple[str, str]] = {
    PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_FI: (
        "webapp_fi",
        "webapp-fi",
    ),
    PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_WEBAPP_IR: (
        "webapp_ir",
        "webapp-ir",
    ),
}


class PhysicalOperationalFailoverV1WitnessTermReplayGuardError(RuntimeError):
    """A local replay/reservation invariant was not safely provable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code)


class PhysicalOperationalFailoverV1WitnessTermReplayCheckpoint(Protocol):
    """Root-owned monotonic state held outside the mutable guard tree.

    Implementations must durably allow an exact replay of their last state or
    exactly one successor linked to it, and reject a lower sequence or a fork.
    A remote object store is not an implementation of this authority.
    """

    def attest_operational_failover_v1_witness_term_replay_state(
        self,
        *,
        configuration_sha256: str,
        binding_sha256: str,
        durable_guard_id: str,
        role: str,
        state_namespace: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None: ...


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig:
    """Pinned, default-off configuration with no caller-chosen state path."""

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DEFAULT_ENABLED
    role: str = ""
    revalidator_config: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig | None = None
    require_durable_rollback_checkpoint: bool = True


@dataclass(frozen=True)
class _Facts:
    role: str
    local_site: str
    state_namespace: str
    configuration_sha256: str
    binding_sha256: str
    durable_guard_id: str
    runtime_instance_id: str
    maximum_reservation_duration_seconds: int
    binding_payload: bytes
    root_binding_payload: bytes


@dataclass(frozen=True)
class _Reservation:
    reservation_id: str
    runtime_instance_id: str
    revalidation_id: str
    request_sha256: str
    requested_at: datetime
    reserved_at: datetime
    expires_at: datetime
    minimum_ledger_version: int
    previous_ledger_head_sha256: str | None
    record_sequence: int
    record_sha256: str


@dataclass(frozen=True)
class _Consumption:
    reservation_id: str
    revalidation_id: str
    request_sha256: str
    attestation_id: str
    attestation_nonce: str
    attestation_sha256: str
    ledger_version: int
    ledger_head_sha256: str
    consumed_at: datetime
    receipt_id: str
    record_sequence: int
    record_sha256: str


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    record_sha256: str
    event: str
    reservation: _Reservation | None
    consumption: _Consumption | None


@dataclass(frozen=True)
class _State:
    records: tuple[_Record, ...]
    reservations: dict[str, _Reservation]
    consumed_reservations: frozenset[str]
    revalidation_ids: frozenset[str]
    attestation_ids: frozenset[str]
    attestation_nonces: frozenset[str]
    attestation_sha256s: frozenset[str]
    receipt_ids: frozenset[str]
    latest_ledger_version: int
    latest_ledger_head_sha256: str | None
    clock_floor: datetime | None


@dataclass(frozen=True)
class _Storage:
    root_fd: int
    namespace_fd: int
    records_fd: int


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode_canonical(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not value or len(value) > _MAX_RECORD_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: _fail(code),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalOperationalFailoverV1WitnessTermReplayGuardError,
    ):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _sha(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _positive(value: object, *, code: str, permit_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if permit_zero else 1) or value > 2**63 - 1:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        result = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(code)
    if result.microsecond:
        _fail(code)
    return result


def _render_time(value: object, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIME_RE.fullmatch(value) is None:
        _fail(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        _fail(code)


def _facts(config: object) -> _Facts:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CONFIG_INVALID"
    if type(config) is not PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig:
        _fail(code)
    assert isinstance(config, PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig)
    if config.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA:
        _fail(code)
    if config.enabled is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DISABLED")
    if config.require_durable_rollback_checkpoint is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CHECKPOINT_REQUIRED")
    if type(config.role) is not str or config.role not in _ROLE_SPECS:
        _fail(code)
    try:
        revalidator_facts = revalidator._config(config.revalidator_config)
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
    local_site, namespace = _ROLE_SPECS[config.role]
    if revalidator_facts.binding.local_site != local_site:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_BINDING_MISMATCH")
    binding_payload = _canonical(
        {
            "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "role": config.role,
            "local_site": local_site,
            "state_namespace": namespace,
            "configuration_sha256": revalidator_facts.configuration_sha256,
            "binding_sha256": revalidator_facts.binding_sha256,
            "durable_guard_id": revalidator_facts.durable_guard_id,
            "maximum_reservation_duration_seconds": revalidator_facts.maximum_reservation_duration_seconds,
            "writer_authorized": False,
            "promotion_authorized": False,
            "traffic_authorized": False,
        },
        code=code,
    )
    # This root-level binding is intentionally even narrower: it makes any
    # deployment/configuration role switch reject before a new namespace is
    # accepted under the same fixed root.
    root_binding_payload = _canonical(
        {
            "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "role": config.role,
            "local_site": local_site,
            "state_namespace": namespace,
            "configuration_sha256": revalidator_facts.configuration_sha256,
            "binding_sha256": revalidator_facts.binding_sha256,
            "durable_guard_id": revalidator_facts.durable_guard_id,
        },
        code=code,
    )
    return _Facts(
        role=config.role,
        local_site=local_site,
        state_namespace=namespace,
        configuration_sha256=revalidator_facts.configuration_sha256,
        binding_sha256=revalidator_facts.binding_sha256,
        durable_guard_id=revalidator_facts.durable_guard_id,
        runtime_instance_id=revalidator_facts.runtime_instance_id,
        maximum_reservation_duration_seconds=revalidator_facts.maximum_reservation_duration_seconds,
        binding_payload=binding_payload,
        root_binding_payload=root_binding_payload,
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(hasattr(os, item) for item in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_PLATFORM_UNSUPPORTED")


def _metadata_tuple(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
    )


def _validate_ancestors(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE")
    _require_fd_platform()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE")
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT
    _validate_ancestors(root)
    descriptor = -1
    try:
        before = os.lstat(root)
        resolved = root.resolve(strict=True)
        if (
            resolved != root
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE")
        descriptor = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        opened = os.fstat(descriptor)
        after = os.lstat(root)
        if (
            _metadata_tuple(before) != _metadata_tuple(opened)
            or _metadata_tuple(after) != _metadata_tuple(before)
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_ROOT_UNSAFE"
        ) from exc


def _safe_child_metadata(
    parent_fd: int,
    name: str,
    *,
    directory: bool,
    code: str,
) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or (not stat.S_ISDIR(metadata.st_mode) if directory else not stat.S_ISREG(metadata.st_mode))
        or (directory and metadata.st_nlink < 2)
        or (not directory and metadata.st_nlink != 1)
        or stat.S_IMODE(metadata.st_mode) != (0o700 if directory else 0o600)
    ):
        _fail(code)
    return metadata


def _listdir(parent_fd: int, *, code: str) -> list[str]:
    try:
        names = os.listdir(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
    if any(type(name) is not str or not name or "/" in name or "\\" in name for name in names):
        _fail(code)
    return names


def _ensure_namespace(root_fd: int, *, facts: _Facts) -> int:
    descriptor = -1
    try:
        # Permit only the two closed, compiled namespace names while checking
        # the immutable root binding below.  A configuration role switch is
        # reported by that binding; an extra role namespace under an otherwise
        # valid installation is residue and is rejected.
        allowed = {_ROOT_BINDING_FILENAME, *(spec[1] for spec in _ROLE_SPECS.values())}
        names = _listdir(root_fd, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RESIDUE")
        namespaces: set[str] = set()
        for name in names:
            if name not in allowed:
                _fail(
                    "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_TEMP_RESIDUE"
                    if _TEMP_NAME_RE.fullmatch(name)
                    else "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RESIDUE"
                )
            _safe_child_metadata(
                root_fd,
                name,
                directory=name != _ROOT_BINDING_FILENAME,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_UNSAFE",
            )
            if name != _ROOT_BINDING_FILENAME:
                namespaces.add(name)
        if _ROOT_BINDING_FILENAME not in names and any(
            name != facts.state_namespace for name in namespaces
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RESIDUE")
        _write_create_only_at(
            root_fd,
            _ROOT_BINDING_FILENAME,
            facts.root_binding_payload,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROLE_CONFIG_SWITCH",
        )
        if any(name != facts.state_namespace for name in namespaces):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ROOT_RESIDUE")
        created = False
        try:
            os.mkdir(facts.state_namespace, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            facts.state_namespace,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(
            root_fd,
            facts.state_namespace,
            directory=True,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            facts.state_namespace,
            directory=True,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_UNSAFE")
        return descriptor
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_UNSAFE"
        ) from exc


def _ensure_records_directory(namespace_fd: int) -> int:
    descriptor = -1
    try:
        created = False
        try:
            os.mkdir(_RECORDS_DIRECTORY, 0o700, dir_fd=namespace_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _RECORDS_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=namespace_fd,
        )
        if created:
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(namespace_fd)
        before = _safe_child_metadata(
            namespace_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_UNSAFE")
        return descriptor
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_UNSAFE"
        ) from exc


def _open_lock(namespace_fd: int) -> int:
    descriptor = -1
    try:
        created = False
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=namespace_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=namespace_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
            os.fdatasync(descriptor)
            os.fsync(namespace_fd)
        before = _safe_child_metadata(
            namespace_fd,
            _LOCK_FILENAME,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            namespace_fd,
            _LOCK_FILENAME,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_LOCK_OPEN_FAILED"
        ) from exc


def _validate_namespace_entries(namespace_fd: int) -> None:
    known = {_LOCK_FILENAME, _BINDING_FILENAME, _CURRENT_FILENAME, _RECORDS_DIRECTORY}
    for name in _listdir(namespace_fd, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_RESIDUE"):
        if name not in known:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_RESIDUE"
            )
        _safe_child_metadata(
            namespace_fd,
            name,
            directory=name == _RECORDS_DIRECTORY,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_NAMESPACE_UNSAFE",
        )


@contextmanager
def _locked_storage(*, facts: _Facts) -> Iterator[_Storage]:
    root_fd = -1
    namespace_fd = -1
    records_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root()
        namespace_fd = _ensure_namespace(root_fd, facts=facts)
        records_fd = _ensure_records_directory(namespace_fd)
        lock_fd = _open_lock(namespace_fd)
        _validate_namespace_entries(namespace_fd)
        yield _Storage(root_fd=root_fd, namespace_fd=namespace_fd, records_fd=records_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, records_fd, namespace_fd, root_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def _read_file_at(parent_fd: int, name: str, *, code: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        before = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        metadata = os.fstat(descriptor)
        after = _safe_child_metadata(parent_fd, name, directory=False, code=code)
        if (
            _metadata_tuple(before) != _metadata_tuple(metadata)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or not 1 <= metadata.st_size <= _MAX_RECORD_BYTES
        ):
            _fail(code)
        remaining = metadata.st_size
        chunks = bytearray()
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            chunks.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(chunks)
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(descriptor: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError as exc:
            raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    if (
        type(name) is not str
        or not name
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
        or type(payload) is not bytes
        or not 1 <= len(payload) <= _MAX_RECORD_BYTES
    ):
        _fail(code)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            if _read_file_at(parent_fd, name, code=code) != payload:
                _fail(code)
            return
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _file_exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_UNSAFE"
        ) from exc


def _write_current_atomic(namespace_fd: int, payload: bytes) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_INVALID")
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        if _file_exists_at(namespace_fd, _CURRENT_FILENAME):
            _safe_child_metadata(
                namespace_fd,
                _CURRENT_FILENAME,
                directory=False,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_UNSAFE",
            )
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=namespace_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_UNSAFE")
        _write_all(
            descriptor,
            payload,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_WRITE_FAILED",
        )
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary, _CURRENT_FILENAME, src_dir_fd=namespace_fd, dst_dir_fd=namespace_fd)
        _safe_child_metadata(
            namespace_fd,
            _CURRENT_FILENAME,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_UNSAFE",
        )
        os.fsync(namespace_fd)
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _base_record_body(
    *, facts: _Facts, sequence: int, previous_record_sha256: str, event: str
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "role": facts.role,
        "local_site": facts.local_site,
        "state_namespace": facts.state_namespace,
        "configuration_sha256": facts.configuration_sha256,
        "binding_sha256": facts.binding_sha256,
        "durable_guard_id": facts.durable_guard_id,
        "sequence": sequence,
        "previous_record_sha256": previous_record_sha256,
        "event": event,
    }


def _record_payload(body: dict[str, object], *, code: str) -> tuple[bytes, str]:
    digest = hashlib.sha256(_canonical(body, code=code)).hexdigest()
    return _canonical({**body, "record_sha256": digest}, code=code), digest


def _reservation_body(
    *, facts: _Facts, sequence: int, previous_record_sha256: str, reservation: _Reservation
) -> dict[str, object]:
    return {
        **_base_record_body(
            facts=facts,
            sequence=sequence,
            previous_record_sha256=previous_record_sha256,
            event="reserve",
        ),
        "reservation_id": reservation.reservation_id,
        "runtime_instance_id": reservation.runtime_instance_id,
        "revalidation_id": reservation.revalidation_id,
        "request_sha256": reservation.request_sha256,
        "requested_at": _render_time(reservation.requested_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID"),
        "reserved_at": _render_time(reservation.reserved_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID"),
        "expires_at": _render_time(reservation.expires_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID"),
        "minimum_ledger_version": reservation.minimum_ledger_version,
        "previous_ledger_head_sha256": reservation.previous_ledger_head_sha256,
    }


def _consumption_body(
    *, facts: _Facts, sequence: int, previous_record_sha256: str, consumption: _Consumption
) -> dict[str, object]:
    return {
        **_base_record_body(
            facts=facts,
            sequence=sequence,
            previous_record_sha256=previous_record_sha256,
            event="consume",
        ),
        "reservation_id": consumption.reservation_id,
        "revalidation_id": consumption.revalidation_id,
        "request_sha256": consumption.request_sha256,
        "attestation_id": consumption.attestation_id,
        "attestation_nonce": consumption.attestation_nonce,
        "attestation_sha256": consumption.attestation_sha256,
        "ledger_version": consumption.ledger_version,
        "ledger_head_sha256": consumption.ledger_head_sha256,
        "consumed_at": _render_time(consumption.consumed_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID"),
        "receipt_id": consumption.receipt_id,
    }


def _validate_common_record(
    decoded: dict[str, object], *, facts: _Facts, sequence: int, previous: str, event: str, code: str
) -> None:
    common = _base_record_body(
        facts=facts,
        sequence=sequence,
        previous_record_sha256=previous,
        event=event,
    )
    for name, expected in common.items():
        if decoded.get(name) != expected:
            _fail(code)


def _record_from_payload(
    payload: bytes,
    *,
    facts: _Facts,
    expected_sequence: int,
    expected_previous_record_sha256: str,
) -> _Record:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID"
    decoded = _decode_canonical(payload, code=code)
    event = decoded.get("event")
    if event not in {"reserve", "consume"}:
        _fail(code)
    reserve_fields = {
        "schema", "version", "mode", "role", "local_site", "state_namespace",
        "configuration_sha256", "binding_sha256", "durable_guard_id", "sequence",
        "previous_record_sha256", "event", "reservation_id", "runtime_instance_id", "revalidation_id",
        "request_sha256", "requested_at", "reserved_at", "expires_at",
        "minimum_ledger_version", "previous_ledger_head_sha256", "record_sha256",
    }
    consume_fields = {
        "schema", "version", "mode", "role", "local_site", "state_namespace",
        "configuration_sha256", "binding_sha256", "durable_guard_id", "sequence",
        "previous_record_sha256", "event", "reservation_id", "revalidation_id",
        "request_sha256", "attestation_id", "attestation_nonce", "attestation_sha256",
        "ledger_version", "ledger_head_sha256", "consumed_at", "receipt_id", "record_sha256",
    }
    if set(decoded) != (reserve_fields if event == "reserve" else consume_fields):
        _fail(code)
    sequence = _positive(decoded["sequence"], code=code)
    previous = _sha(decoded["previous_record_sha256"], code=code, permit_zero=True)
    if sequence != expected_sequence or previous != expected_previous_record_sha256:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_ROLLBACK")
    _validate_common_record(
        decoded,
        facts=facts,
        sequence=sequence,
        previous=previous,
        event=event,
        code=code,
    )
    record_sha256 = _sha(decoded["record_sha256"], code=code)
    unsigned = dict(decoded)
    del unsigned["record_sha256"]
    expected_payload, expected_sha = _record_payload(unsigned, code=code)
    if payload != expected_payload or record_sha256 != expected_sha:
        _fail(code)
    if event == "reserve":
        reservation = _Reservation(
            reservation_id=_identifier(decoded["reservation_id"], code=code),
            runtime_instance_id=_identifier(decoded["runtime_instance_id"], code=code),
            revalidation_id=_identifier(decoded["revalidation_id"], code=code),
            request_sha256=_sha(decoded["request_sha256"], code=code),
            requested_at=_parse_time(decoded["requested_at"], code=code),
            reserved_at=_parse_time(decoded["reserved_at"], code=code),
            expires_at=_parse_time(decoded["expires_at"], code=code),
            minimum_ledger_version=_positive(decoded["minimum_ledger_version"], code=code, permit_zero=True),
            previous_ledger_head_sha256=None
            if decoded["previous_ledger_head_sha256"] is None
            else _sha(decoded["previous_ledger_head_sha256"], code=code),
            record_sequence=sequence,
            record_sha256=record_sha256,
        )
        if (
            reservation.reserved_at != reservation.requested_at
            or reservation.expires_at <= reservation.reserved_at
            or reservation.expires_at - reservation.reserved_at
            != timedelta(seconds=facts.maximum_reservation_duration_seconds)
            or (reservation.minimum_ledger_version == 0)
            != (reservation.previous_ledger_head_sha256 is None)
        ):
            _fail(code)
        return _Record(
            sequence=sequence,
            previous_record_sha256=previous,
            record_sha256=record_sha256,
            event=event,
            reservation=reservation,
            consumption=None,
        )
    consumption = _Consumption(
        reservation_id=_identifier(decoded["reservation_id"], code=code),
        revalidation_id=_identifier(decoded["revalidation_id"], code=code),
        request_sha256=_sha(decoded["request_sha256"], code=code),
        attestation_id=_identifier(decoded["attestation_id"], code=code),
        attestation_nonce=_nonce(decoded["attestation_nonce"], code=code),
        attestation_sha256=_sha(decoded["attestation_sha256"], code=code),
        ledger_version=_positive(decoded["ledger_version"], code=code),
        ledger_head_sha256=_sha(decoded["ledger_head_sha256"], code=code),
        consumed_at=_parse_time(decoded["consumed_at"], code=code),
        receipt_id=_identifier(decoded["receipt_id"], code=code),
        record_sequence=sequence,
        record_sha256=record_sha256,
    )
    return _Record(
        sequence=sequence,
        previous_record_sha256=previous,
        record_sha256=record_sha256,
        event=event,
        reservation=None,
        consumption=consumption,
    )


def _current_payload(*, facts: _Facts, record: _Record) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "role": facts.role,
            "state_namespace": facts.state_namespace,
            "configuration_sha256": facts.configuration_sha256,
            "binding_sha256": facts.binding_sha256,
            "durable_guard_id": facts.durable_guard_id,
            "sequence": record.sequence,
            "record_sha256": record.record_sha256,
            "event": record.event,
        },
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_INVALID",
    )


def _apply_records(records: tuple[_Record, ...], *, facts: _Facts) -> _State:
    reservations: dict[str, _Reservation] = {}
    consumed_reservations: set[str] = set()
    revalidation_ids: set[str] = set()
    attestation_ids: set[str] = set()
    attestation_nonces: set[str] = set()
    attestation_sha256s: set[str] = set()
    receipt_ids: set[str] = set()
    latest_version = 0
    latest_head: str | None = None
    clock_floor: datetime | None = None
    for record in records:
        if record.event == "reserve":
            reservation = record.reservation
            if reservation is None or record.consumption is not None:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID")
            if (
                reservation.reservation_id in reservations
                or reservation.revalidation_id in revalidation_ids
                or reservation.minimum_ledger_version != latest_version
                or reservation.previous_ledger_head_sha256 != latest_head
                or (clock_floor is not None and reservation.reserved_at < clock_floor)
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_ROLLBACK")
            reservations[reservation.reservation_id] = reservation
            revalidation_ids.add(reservation.revalidation_id)
            clock_floor = reservation.reserved_at
            continue
        consumption = record.consumption
        if consumption is None or record.reservation is not None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID")
        reservation = reservations.get(consumption.reservation_id)
        if reservation is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID")
        if (
            consumption.reservation_id in consumed_reservations
            or consumption.revalidation_id != reservation.revalidation_id
            or consumption.request_sha256 != reservation.request_sha256
            or consumption.consumed_at < reservation.reserved_at
            or consumption.consumed_at > reservation.expires_at
            or (clock_floor is not None and consumption.consumed_at < clock_floor)
            or consumption.attestation_id in attestation_ids
            or consumption.attestation_nonce in attestation_nonces
            or consumption.attestation_sha256 in attestation_sha256s
            or consumption.receipt_id in receipt_ids
            or consumption.ledger_version < latest_version
            or (
                consumption.ledger_version == latest_version
                and latest_head is not None
                and consumption.ledger_head_sha256 != latest_head
            )
            or (
                consumption.ledger_version > latest_version
                and consumption.ledger_head_sha256 == latest_head
            )
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_ROLLBACK")
        consumed_reservations.add(consumption.reservation_id)
        attestation_ids.add(consumption.attestation_id)
        attestation_nonces.add(consumption.attestation_nonce)
        attestation_sha256s.add(consumption.attestation_sha256)
        receipt_ids.add(consumption.receipt_id)
        latest_version = consumption.ledger_version
        latest_head = consumption.ledger_head_sha256
        clock_floor = consumption.consumed_at
    return _State(
        records=records,
        reservations=reservations,
        consumed_reservations=frozenset(consumed_reservations),
        revalidation_ids=frozenset(revalidation_ids),
        attestation_ids=frozenset(attestation_ids),
        attestation_nonces=frozenset(attestation_nonces),
        attestation_sha256s=frozenset(attestation_sha256s),
        receipt_ids=frozenset(receipt_ids),
        latest_ledger_version=latest_version,
        latest_ledger_head_sha256=latest_head,
        clock_floor=clock_floor,
    )


def _load_state(storage: _Storage, *, facts: _Facts) -> _State:
    _write_create_only_at(
        storage.namespace_fd,
        _BINDING_FILENAME,
        facts.binding_payload,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_BINDING_MISMATCH",
    )
    filenames: list[tuple[int, str, str]] = []
    for name in _listdir(storage.records_fd, code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_RESIDUE"):
        match = _RECORD_NAME_RE.fullmatch(name)
        if match is None:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORDS_RESIDUE"
            )
        _safe_child_metadata(
            storage.records_fd,
            name,
            directory=False,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_UNSAFE",
        )
        filenames.append((int(match.group(1)), match.group(2), name))
    if len(filenames) > _MAX_RECORDS:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_LIMIT")
    filenames.sort()
    sequence = 1
    previous = _ZERO_SHA256
    records: list[_Record] = []
    for file_sequence, file_sha, name in filenames:
        if file_sequence != sequence:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_ROLLBACK")
        record = _record_from_payload(
            _read_file_at(
                storage.records_fd,
                name,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID",
            ),
            facts=facts,
            expected_sequence=sequence,
            expected_previous_record_sha256=previous,
        )
        if record.record_sha256 != file_sha:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID")
        records.append(record)
        previous = record.record_sha256
        sequence += 1
    state = _apply_records(tuple(records), facts=facts)
    if not state.records:
        if _file_exists_at(storage.namespace_fd, _CURRENT_FILENAME):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_ROLLBACK")
        return state
    current = _read_file_at(
        storage.namespace_fd,
        _CURRENT_FILENAME,
        code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_INVALID",
    )
    if current != _current_payload(facts=facts, record=state.records[-1]):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CURRENT_ROLLBACK")
    return state


def _checkpoint(
    checkpoint: object, *, facts: _Facts, record: _Record | None
) -> None:
    callback = getattr(checkpoint, "attest_operational_failover_v1_witness_term_replay_state", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CHECKPOINT_MISSING")
    try:
        result = callback(
            configuration_sha256=facts.configuration_sha256,
            binding_sha256=facts.binding_sha256,
            durable_guard_id=facts.durable_guard_id,
            role=facts.role,
            state_namespace=facts.state_namespace,
            sequence=0 if record is None else record.sequence,
            previous_record_sha256=_ZERO_SHA256 if record is None else record.previous_record_sha256,
            record_sha256=_ZERO_SHA256 if record is None else record.record_sha256,
        )
    except PhysicalOperationalFailoverV1WitnessTermReplayGuardError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessTermReplayGuardError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CHECKPOINT_REJECTED"
        ) from exc
    if result is not None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CHECKPOINT_INVALID")


def _require_checkpoint_callback(checkpoint: object) -> None:
    if not callable(getattr(checkpoint, "attest_operational_failover_v1_witness_term_replay_state", None)):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CHECKPOINT_MISSING")


def _request(
    value: object, *, facts: _Facts
) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_REQUEST_INVALID"
    if type(value) is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest:
        _fail(code)
    assert isinstance(value, revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest)
    if (
        value.schema != revalidator.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA
        or value.configuration_sha256 != facts.configuration_sha256
        or value.durable_guard_id != facts.durable_guard_id
        or value.binding_sha256 != facts.binding_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_CONFIG_MISMATCH")
    if _identifier(value.runtime_instance_id, code=code) != facts.runtime_instance_id:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_CONFIG_MISMATCH")
    _identifier(value.revalidation_id, code=code)
    _sha(value.request_sha256, code=code)
    _utc(value.requested_at, code=code)
    return value


def _consumption(
    value: object, *, facts: _Facts
) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption:
    code = "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CONSUMPTION_INVALID"
    if type(value) is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption:
        _fail(code)
    assert isinstance(value, revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption)
    if (
        value.schema != revalidator.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA
        or value.configuration_sha256 != facts.configuration_sha256
        or value.durable_guard_id != facts.durable_guard_id
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CONSUMPTION_CONFIG_MISMATCH")
    _identifier(value.reservation_id, code=code)
    _identifier(value.revalidation_id, code=code)
    _sha(value.request_sha256, code=code)
    _identifier(value.attestation_id, code=code)
    _nonce(value.attestation_nonce, code=code)
    _sha(value.attestation_sha256, code=code)
    _positive(value.ledger_version, code=code)
    _sha(value.ledger_head_sha256, code=code)
    _utc(value.consumed_at, code=code)
    return value


class PhysicalOperationalFailoverV1WitnessTermReplayGuard:
    """Root-gated implementation of the V1 revalidator's durable guard seam.

    Returned values are the exact seam dataclasses expected by the revalidator.
    They intentionally contain no writer, promotion, or traffic authorization
    field.  The caller must still authenticate/fetch/verify a term and use the
    independent writer-admission transaction boundary.
    """

    def __init__(
        self,
        config: PhysicalOperationalFailoverV1WitnessTermReplayGuardConfig,
        *,
        rollback_checkpoint: PhysicalOperationalFailoverV1WitnessTermReplayCheckpoint | None,
    ) -> None:
        self._config = config
        self._rollback_checkpoint = rollback_checkpoint

    def reserve_revalidation(
        self,
        *,
        request: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservationRequest,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        """Durably burn one revalidation ID before an authenticated fetch."""

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        request = _request(request, facts=facts)
        with _locked_storage(facts=facts) as storage:
            state = _load_state(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=state.records[-1] if state.records else None)
            requested_at = _utc(
                request.requested_at,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_REQUEST_INVALID",
            )
            if state.clock_floor is not None and requested_at < state.clock_floor:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CLOCK_ROLLBACK")
            if request.revalidation_id in state.revalidation_ids:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_REVALIDATION_REPLAY")
            reservation = _Reservation(
                reservation_id="witness-term-reservation-v1-" + secrets.token_hex(32),
                runtime_instance_id=request.runtime_instance_id,
                revalidation_id=request.revalidation_id,
                request_sha256=request.request_sha256,
                requested_at=requested_at,
                reserved_at=requested_at,
                expires_at=requested_at + timedelta(seconds=facts.maximum_reservation_duration_seconds),
                minimum_ledger_version=state.latest_ledger_version,
                previous_ledger_head_sha256=state.latest_ledger_head_sha256,
                record_sequence=(state.records[-1].sequence + 1) if state.records else 1,
                record_sha256="",
            )
            if reservation.reservation_id in state.reservations:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_ID_COLLISION")
            previous = state.records[-1].record_sha256 if state.records else _ZERO_SHA256
            payload, digest = _record_payload(
                _reservation_body(
                    facts=facts,
                    sequence=reservation.record_sequence,
                    previous_record_sha256=previous,
                    reservation=reservation,
                ),
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID",
            )
            reservation = _Reservation(**{**reservation.__dict__, "record_sha256": digest})
            _write_create_only_at(
                storage.records_fd,
                f"{reservation.record_sequence:020d}-{digest}.json",
                payload,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_WRITE_FAILED",
            )
            record = _Record(
                sequence=reservation.record_sequence,
                previous_record_sha256=previous,
                record_sha256=digest,
                event="reserve",
                reservation=reservation,
                consumption=None,
            )
            _write_current_atomic(storage.namespace_fd, _current_payload(facts=facts, record=record))
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation(
                schema=request.schema,
                configuration_sha256=request.configuration_sha256,
                durable_guard_id=request.durable_guard_id,
                reservation_id=reservation.reservation_id,
                binding_sha256=request.binding_sha256,
                runtime_instance_id=request.runtime_instance_id,
                revalidation_id=request.revalidation_id,
                request_sha256=request.request_sha256,
                requested_at=requested_at,
                reserved_at=reservation.reserved_at,
                expires_at=reservation.expires_at,
                minimum_ledger_version=reservation.minimum_ledger_version,
                previous_ledger_head_sha256=reservation.previous_ledger_head_sha256,
            )

    def consume_attestation(
        self,
        *,
        reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
        consumption: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumption,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt:
        """Permanently consume one exact verified attestation for one reservation."""

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        consumption = _consumption(consumption, facts=facts)
        if type(reservation) is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_INVALID")
        with _locked_storage(facts=facts) as storage:
            state = _load_state(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=state.records[-1] if state.records else None)
            durable_reservation = state.reservations.get(consumption.reservation_id)
            if durable_reservation is None:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_UNKNOWN")
            # Compare every field that crosses the in-memory seam.  A forged
            # receipt cannot select a different request, ledger floor, or expiry.
            expected = (
                ("schema", reservation.schema, revalidator.PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REVALIDATOR_SCHEMA),
                ("configuration_sha256", reservation.configuration_sha256, facts.configuration_sha256),
                ("durable_guard_id", reservation.durable_guard_id, facts.durable_guard_id),
                ("reservation_id", reservation.reservation_id, durable_reservation.reservation_id),
                ("binding_sha256", reservation.binding_sha256, facts.binding_sha256),
                ("runtime_instance_id", reservation.runtime_instance_id, facts.runtime_instance_id),
                ("revalidation_id", reservation.revalidation_id, durable_reservation.revalidation_id),
                ("request_sha256", reservation.request_sha256, durable_reservation.request_sha256),
                ("requested_at", reservation.requested_at, durable_reservation.requested_at),
                ("reserved_at", reservation.reserved_at, durable_reservation.reserved_at),
                ("expires_at", reservation.expires_at, durable_reservation.expires_at),
                ("minimum_ledger_version", reservation.minimum_ledger_version, durable_reservation.minimum_ledger_version),
                ("previous_ledger_head_sha256", reservation.previous_ledger_head_sha256, durable_reservation.previous_ledger_head_sha256),
            )
            if any(actual != wanted for _name, actual, wanted in expected):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_MISMATCH")
            consumed_at = _utc(
                consumption.consumed_at,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CONSUMPTION_INVALID",
            )
            if (
                durable_reservation.reservation_id in state.consumed_reservations
                or consumed_at < durable_reservation.reserved_at
                or consumed_at > durable_reservation.expires_at
                or (state.clock_floor is not None and consumed_at < state.clock_floor)
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RESERVATION_EXPIRED_OR_CONSUMED")
            if (
                consumption.revalidation_id != durable_reservation.revalidation_id
                or consumption.request_sha256 != durable_reservation.request_sha256
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_CONSUMPTION_BINDING_MISMATCH")
            if (
                consumption.attestation_id in state.attestation_ids
                or consumption.attestation_nonce in state.attestation_nonces
                or consumption.attestation_sha256 in state.attestation_sha256s
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_ATTESTATION_REPLAY")
            if (
                consumption.ledger_version < state.latest_ledger_version
                or (
                    consumption.ledger_version == state.latest_ledger_version
                    and state.latest_ledger_head_sha256 is not None
                    and consumption.ledger_head_sha256 != state.latest_ledger_head_sha256
                )
                or (
                    consumption.ledger_version > state.latest_ledger_version
                    and consumption.ledger_head_sha256 == state.latest_ledger_head_sha256
                )
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_LEDGER_ROLLBACK")
            sequence = state.records[-1].sequence + 1 if state.records else 1
            previous = state.records[-1].record_sha256 if state.records else _ZERO_SHA256
            receipt_id = "witness-term-consumption-v1-" + secrets.token_hex(32)
            if receipt_id in state.receipt_ids:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECEIPT_ID_COLLISION")
            durable_consumption = _Consumption(
                reservation_id=consumption.reservation_id,
                revalidation_id=consumption.revalidation_id,
                request_sha256=consumption.request_sha256,
                attestation_id=consumption.attestation_id,
                attestation_nonce=consumption.attestation_nonce,
                attestation_sha256=consumption.attestation_sha256,
                ledger_version=consumption.ledger_version,
                ledger_head_sha256=consumption.ledger_head_sha256,
                consumed_at=consumed_at,
                receipt_id=receipt_id,
                record_sequence=sequence,
                record_sha256="",
            )
            payload, digest = _record_payload(
                _consumption_body(
                    facts=facts,
                    sequence=sequence,
                    previous_record_sha256=previous,
                    consumption=durable_consumption,
                ),
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_INVALID",
            )
            durable_consumption = _Consumption(**{**durable_consumption.__dict__, "record_sha256": digest})
            _write_create_only_at(
                storage.records_fd,
                f"{sequence:020d}-{digest}.json",
                payload,
                code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_REPLAY_GUARD_RECORD_WRITE_FAILED",
            )
            record = _Record(
                sequence=sequence,
                previous_record_sha256=previous,
                record_sha256=digest,
                event="consume",
                reservation=None,
                consumption=durable_consumption,
            )
            _write_current_atomic(storage.namespace_fd, _current_payload(facts=facts, record=record))
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationConsumptionReceipt(
                schema=consumption.schema,
                configuration_sha256=consumption.configuration_sha256,
                durable_guard_id=consumption.durable_guard_id,
                reservation_id=consumption.reservation_id,
                revalidation_id=consumption.revalidation_id,
                request_sha256=consumption.request_sha256,
                attestation_id=consumption.attestation_id,
                attestation_nonce=consumption.attestation_nonce,
                attestation_sha256=consumption.attestation_sha256,
                ledger_version=consumption.ledger_version,
                ledger_head_sha256=consumption.ledger_head_sha256,
                consumed_at=consumed_at,
                receipt_id=receipt_id,
            )
