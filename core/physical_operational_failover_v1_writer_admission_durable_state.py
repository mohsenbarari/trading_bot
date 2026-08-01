"""Root-local durable state foundation for V1 writer admission.

This module deliberately persists *only* the local state machine from
``physical_operational_failover_v1_writer_admission``.  It is not a Writer,
Witness, database, traffic, promotion, or external-effect implementation.
In particular, a successful local state write is never an authorization to
write data, promote a site, or route traffic.

The normal writer-admission module intentionally has no I/O.  This companion
foundation supplies the narrow missing persistence seam while preserving that
separation:

* the state directory is a fixed, root-owned location (callers cannot choose
  it);
* an immutable binding file pins the local site, release, generation, and
  admission policy; runtime instance identifiers are intentionally excluded
  from that persistent binding so a restart can be restored safely;
* current state is a canonical, descriptor-validated record atomically
  replaced only after its data and parent directory are fsync'd;
* every normal transition compare-and-swaps the exact prior state digest,
  revision, and fence generation; and
* a distinct injected root-owned monotonic checkpoint attests the complete
  head.  Without it, a privileged whole-tree rollback is not detectable, so
  this module refuses to operate.

``restore_for_runtime`` is the only intended startup path.  It reads a raw
structural record, invokes the existing V1 explicit restore boundary, persists
the resulting fence-generation/revision advance, and returns a state that
*requires fresh Witness revalidation*.  It deliberately does not return a
writer permit.  A caller must still obtain a fresh term through the existing
V1 revalidator and atomically couple a later writer-admission CAS with the
real database transaction or external effect.  That final coupling is outside
this local-file foundation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Iterator, Protocol

from core import physical_operational_failover_v1_writer_admission as admission


_ADMISSION_MODULE = admission


__all__ = (
    "FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA",
    "PhysicalOperationalFailoverV1WriterAdmissionDurableStateCheckpoint",
    "PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore",
    "PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError",
    "RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-writer-admission-durable-state-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DEFAULT_ENABLED = False

# Deployment must provision this exact directory root:root mode 0700.  It is
# intentionally a module constant rather than configuration.  Tests may patch
# the symbol locally, but application callers never receive a path parameter.
FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-operational-failover-v1-writer-admission"
)

_VERSION = 1
_MODE = "root-owned-v1-writer-admission-durable-state-v1"
_LOCK_FILENAME = "writer-admission.lock"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_MAX_RECORD_BYTES = 2 * 1024 * 1024
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TEMP_NAME_RE = re.compile(r"^\.[A-Za-z0-9._-]+\.tmp$", re.ASCII)
_TIME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})T"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<microsecond>\d{6}))?Z$",
    re.ASCII,
)


class PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(RuntimeError):
    """The root-local durable-state boundary rejected unsafe state."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code)


class PhysicalOperationalFailoverV1WriterAdmissionDurableStateCheckpoint(Protocol):
    """Independent root-owned monotonic checkpoint for the local state head.

    The implementation must live outside the mutable state directory.  It
    must accept an exact replay of its current tuple or its direct successor,
    and reject rollback, divergent same-version heads, and invalid branches.
    This module supplies no fallback because local files alone cannot detect a
    privileged rollback of the whole directory.
    """

    def attest_v1_writer_admission_state(
        self,
        *,
        binding_sha256: str,
        writer_admission_schema: str,
        config_identity_sha256: str,
        revision: int,
        fence_generation: int,
        previous_record_sha256: str,
        state_sha256: str,
        record_sha256: str,
    ) -> None: ...


@dataclass(frozen=True)
class RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig:
    """Default-off pinned configuration; no caller-selected storage location."""

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DEFAULT_ENABLED
    writer_admission_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig | None = None
    require_durable_rollback_checkpoint: bool = True


@dataclass(frozen=True)
class _Facts:
    writer_config: admission.PhysicalOperationalFailoverV1WriterAdmissionConfig
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
    runtime_instance_id: str
    config_identity_payload: dict[str, object]
    config_identity_sha256: str
    binding_payload: bytes
    binding_sha256: str


@dataclass(frozen=True)
class _Record:
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState
    state_sha256: str
    previous_record_sha256: str
    record_sha256: str


@dataclass(frozen=True)
class _Storage:
    root_fd: int


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
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DUPLICATE_JSON_KEY")
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
        PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError,
    ):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _nonnegative(value: object, *, code: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not 0 <= value <= 2**63 - 1:
        _fail(code)
    return value


def _writer_call(function: object, *args: object, code: str, **kwargs: object) -> object:
    if not callable(function):
        _fail(code)
    # The writer-admission module keeps its structural validators private and
    # requires their failure code explicitly.  Public transition functions do
    # not accept that keyword, so inject it only for those exact validators.
    if function in {
        admission._binding,
        admission._term_snapshot,
        admission._utc,
    }:
        kwargs["code"] = code
    try:
        return function(*args, **kwargs)
    except admission.PhysicalOperationalFailoverV1WriterAdmissionError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc


def _binding_mapping(
    value: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
    *,
    code: str,
) -> dict[str, object]:
    checked = _writer_call(admission._binding, value, code=code)
    if checked is not value:
        _fail(code)
    return {
        "cluster_id": value.cluster_id,
        "local_site": value.local_site,
        "release_sha": value.release_sha,
        "generation_id": value.generation_id,
    }


_BINDING_FIELDS = frozenset({"cluster_id", "local_site", "release_sha", "generation_id"})


def _binding_from_mapping(value: object, *, code: str) -> admission.PhysicalOperationalFailoverV1WriterAdmissionBinding:
    fields = _exact_mapping(value, fields=_BINDING_FIELDS, code=code)
    binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
        cluster_id=fields["cluster_id"],
        local_site=fields["local_site"],
        release_sha=fields["release_sha"],
        generation_id=fields["generation_id"],
    )
    _writer_call(admission._binding, binding, code=code)
    if _binding_mapping(binding, code=code) != fields:
        _fail(code)
    return binding


def _render_time(value: object, *, code: str) -> str:
    checked = _writer_call(admission._utc, value, code=code)
    if type(checked) is not datetime:
        _fail(code)
    normalized = checked.astimezone(timezone.utc)
    prefix = (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
    )
    return prefix + (f".{normalized.microsecond:06d}Z" if normalized.microsecond else "Z")


def _parse_time(value: object, *, code: str) -> datetime:
    if type(value) is not str:
        _fail(code)
    match = _TIME_RE.fullmatch(value)
    if match is None:
        _fail(code)
    groups = match.groupdict()
    try:
        parsed = datetime(
            int(groups["year"]),
            int(groups["month"]),
            int(groups["day"]),
            int(groups["hour"]),
            int(groups["minute"]),
            int(groups["second"]),
            0 if groups["microsecond"] is None else int(groups["microsecond"]),
            tzinfo=timezone.utc,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    if _render_time(parsed, code=code) != value:
        _fail(code)
    return parsed


_TERM_FIELDS = frozenset(
    {
        "holder_site",
        "writer_epoch",
        "writer_lease_id",
        "evidence_id",
        "revalidation_id",
        "issued_at",
        "expires_at",
    }
)


def _term_mapping(
    value: admission.PhysicalOperationalFailoverV1WriterTermSnapshot,
    *,
    code: str,
) -> dict[str, object]:
    checked = _writer_call(admission._term_snapshot, value, code=code)
    if checked is not value:
        _fail(code)
    return {
        "holder_site": value.holder_site,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": value.writer_lease_id,
        "evidence_id": value.evidence_id,
        "revalidation_id": value.revalidation_id,
        "issued_at": _render_time(value.issued_at, code=code),
        "expires_at": _render_time(value.expires_at, code=code),
    }


def _term_from_mapping(
    value: object,
    *,
    code: str,
) -> admission.PhysicalOperationalFailoverV1WriterTermSnapshot:
    fields = _exact_mapping(value, fields=_TERM_FIELDS, code=code)
    term = admission.PhysicalOperationalFailoverV1WriterTermSnapshot(
        holder_site=fields["holder_site"],
        writer_epoch=fields["writer_epoch"],
        writer_lease_id=fields["writer_lease_id"],
        evidence_id=fields["evidence_id"],
        revalidation_id=fields["revalidation_id"],
        issued_at=_parse_time(fields["issued_at"], code=code),
        expires_at=_parse_time(fields["expires_at"], code=code),
    )
    _writer_call(admission._term_snapshot, term, code=code)
    if _term_mapping(term, code=code) != fields:
        _fail(code)
    return term


_STATE_FIELDS = frozenset(
    {
        "schema",
        "binding",
        "revision",
        "highest_writer_epoch",
        "active_term",
        "revalidated_runtime_instance_id",
        "clock_floor",
        "fence_generation",
        "fenced",
        "fence_reason",
        "requires_fresh_witness_revalidation",
    }
)


def _state_mapping(
    value: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
    code: str,
) -> dict[str, object]:
    checked = _writer_call(
        admission._state,
        value,
        binding=facts.binding,
        allow_unattested=True,
        code=code,
    )
    if checked is not value:
        _fail(code)
    return {
        "schema": value.schema,
        "binding": _binding_mapping(value.binding, code=code),
        "revision": value.revision,
        "highest_writer_epoch": value.highest_writer_epoch,
        "active_term": None
        if value.active_term is None
        else _term_mapping(value.active_term, code=code),
        "revalidated_runtime_instance_id": value.revalidated_runtime_instance_id,
        "clock_floor": None if value.clock_floor is None else _render_time(value.clock_floor, code=code),
        "fence_generation": value.fence_generation,
        "fenced": value.fenced,
        "fence_reason": value.fence_reason,
        "requires_fresh_witness_revalidation": value.requires_fresh_witness_revalidation,
    }


def _state_from_mapping(
    value: object,
    *,
    facts: _Facts,
    code: str,
) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
    fields = _exact_mapping(value, fields=_STATE_FIELDS, code=code)
    binding = _binding_from_mapping(fields["binding"], code=code)
    if binding != facts.binding:
        _fail(code)
    state = admission.PhysicalOperationalFailoverV1WriterAdmissionState(
        schema=fields["schema"],
        binding=binding,
        revision=fields["revision"],
        highest_writer_epoch=fields["highest_writer_epoch"],
        active_term=None
        if fields["active_term"] is None
        else _term_from_mapping(fields["active_term"], code=code),
        revalidated_runtime_instance_id=fields["revalidated_runtime_instance_id"],
        clock_floor=None if fields["clock_floor"] is None else _parse_time(fields["clock_floor"], code=code),
        fence_generation=fields["fence_generation"],
        fenced=fields["fenced"],
        fence_reason=fields["fence_reason"],
        requires_fresh_witness_revalidation=fields["requires_fresh_witness_revalidation"],
    )
    _writer_call(
        admission._state,
        state,
        binding=facts.binding,
        allow_unattested=True,
        code=code,
    )
    if _state_mapping(state, facts=facts, code=code) != fields:
        _fail(code)
    return state


def _raw_startup_state(*, facts: _Facts) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
    initial = _writer_call(
        admission.new_physical_operational_failover_v1_writer_admission_state,
        binding=facts.binding,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BOOTSTRAP_INVALID",
    )
    if type(initial) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BOOTSTRAP_INVALID")
    # Round-tripping through our strict mapping deliberately strips the
    # process-local capability.  The returned state is structural only.
    return _state_from_mapping(
        _state_mapping(
            initial,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BOOTSTRAP_INVALID",
        ),
        facts=facts,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BOOTSTRAP_INVALID",
    )


def _facts(config: object) -> _Facts:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CONFIG_INVALID"
    if type(config) is not RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig:
        _fail(code)
    assert isinstance(config, RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig)
    if config.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA:
        _fail(code)
    if config.enabled is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DISABLED")
    if config.require_durable_rollback_checkpoint is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CHECKPOINT_REQUIRED")
    if type(config.writer_admission_config) is not admission.PhysicalOperationalFailoverV1WriterAdmissionConfig:
        _fail(code)
    writer_config = config.writer_admission_config
    parsed = _writer_call(admission._config, writer_config, code=code)
    if not isinstance(parsed, tuple) or len(parsed) != 5:
        _fail(code)
    binding, runtime_instance_id, safety_margin, maximum_duration, maximum_age = parsed
    if (
        type(binding) is not admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
        or type(runtime_instance_id) is not str
        or any(type(item) is not int for item in (safety_margin, maximum_duration, maximum_age))
    ):
        _fail(code)
    identity = {
        "writer_admission_schema": admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SCHEMA,
        "writer_admission_state_schema": admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_STATE_SCHEMA,
        "binding": _binding_mapping(binding, code=code),
        # Runtime identity is intentionally not persisted here.  A changed
        # runtime id can only proceed through restore_for_runtime, which clears
        # it and forces fresh Witness revalidation before writer use.
        "safety_margin_seconds": safety_margin,
        "maximum_term_duration_seconds": maximum_duration,
        "maximum_evidence_age_seconds": maximum_age,
    }
    identity_payload = _canonical(identity, code=code)
    identity_sha = hashlib.sha256(identity_payload).hexdigest()
    binding_body = {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "config_identity": identity,
        "config_identity_sha256": identity_sha,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_authorized": False,
    }
    binding_payload = _canonical(binding_body, code=code)
    return _Facts(
        writer_config=writer_config,
        binding=binding,
        runtime_instance_id=runtime_instance_id,
        config_identity_payload=identity,
        config_identity_sha256=identity_sha,
        binding_payload=binding_payload,
        binding_sha256=hashlib.sha256(binding_payload).hexdigest(),
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not all(hasattr(os, item) for item in ("O_NOFOLLOW", "O_DIRECTORY", "fdatasync")):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_PLATFORM_UNSUPPORTED")


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
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_UNSAFE"
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail(code)
    _require_fd_platform()
    descriptor = -1
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
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
                _fail(code)
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_UNSAFE"
    _validate_ancestors(root)
    descriptor = -1
    try:
        before = os.lstat(root)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISDIR(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o700
        ):
            _fail(code)
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
            _fail(code)
        return descriptor
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc


def _safe_child_metadata(
    parent_fd: int,
    name: str,
    *,
    code: str,
) -> os.stat_result:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        _fail(code)
    return metadata


def _listdir(parent_fd: int, *, code: str) -> list[str]:
    try:
        names = os.listdir(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    if any(type(name) is not str or not name or "/" in name or "\\" in name for name in names):
        _fail(code)
    return names


def _open_lock(root_fd: int) -> int:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_LOCK_UNSAFE"
    descriptor = -1
    try:
        created = False
        try:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=root_fd,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                _LOCK_FILENAME,
                os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=root_fd,
            )
        if created:
            os.fchmod(descriptor, 0o600)
            os.fdatasync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(root_fd, _LOCK_FILENAME, code=code)
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(root_fd, _LOCK_FILENAME, code=code)
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail(code)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_LOCK_OPEN_FAILED"
        ) from exc


def _validate_root_entries(root_fd: int) -> None:
    known = {_LOCK_FILENAME, _BINDING_FILENAME, _CURRENT_FILENAME}
    for name in _listdir(root_fd, code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_RESIDUE"):
        if name not in known:
            _fail(
                "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_TEMP_RESIDUE"
                if _TEMP_NAME_RE.fullmatch(name)
                else "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_RESIDUE"
            )
        _safe_child_metadata(
            root_fd,
            name,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ROOT_CHILD_UNSAFE",
        )


@contextmanager
def _locked_storage() -> Iterator[_Storage]:
    root_fd = -1
    lock_fd = -1
    try:
        root_fd = _open_secure_root()
        lock_fd = _open_lock(root_fd)
        _validate_root_entries(root_fd)
        yield _Storage(root_fd=root_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, root_fd):
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
        before = _safe_child_metadata(parent_fd, name, code=code)
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(parent_fd, name, code=code)
        if (
            _metadata_tuple(before) != _metadata_tuple(opened)
            or _metadata_tuple(after) != _metadata_tuple(before)
            or not 1 <= opened.st_size <= _MAX_RECORD_BYTES
        ):
            _fail(code)
        remaining = opened.st_size
        result = bytearray()
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail(code)
            result.extend(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(result)
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
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
            raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(
    parent_fd: int,
    name: str,
    payload: bytes,
    *,
    code: str,
    allow_exact_existing: bool,
) -> None:
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
            if allow_exact_existing and _read_file_at(parent_fd, name, code=code) == payload:
                return
            _fail(code)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_FILE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _ensure_absent_or_safe_current(root_fd: int) -> None:
    try:
        os.stat(_CURRENT_FILENAME, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE"
        ) from exc
    _safe_child_metadata(
        root_fd,
        _CURRENT_FILENAME,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE",
    )


def _write_current_atomic(root_fd: int, payload: bytes) -> None:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_WRITE_FAILED"
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail(code)
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        _ensure_absent_or_safe_current(root_fd)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary, _CURRENT_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        _safe_child_metadata(
            root_fd,
            _CURRENT_FILENAME,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE",
        )
        os.fsync(root_fd)
        if _read_file_at(root_fd, _CURRENT_FILENAME, code=code) != payload:
            _fail(code)
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        raise
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _state_sha256(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
    code: str,
) -> str:
    return hashlib.sha256(_canonical(_state_mapping(state, facts=facts, code=code), code=code)).hexdigest()


def _record_payload(
    *,
    facts: _Facts,
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    previous_record_sha256: str,
) -> tuple[bytes, _Record]:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RECORD_INVALID"
    previous = _sha256(previous_record_sha256, code=code, permit_zero=True)
    state_mapping = _state_mapping(state, facts=facts, code=code)
    state_sha = hashlib.sha256(_canonical(state_mapping, code=code)).hexdigest()
    body = {
        "schema": PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "binding_sha256": facts.binding_sha256,
        "config_identity_sha256": facts.config_identity_sha256,
        "revision": state.revision,
        "fence_generation": state.fence_generation,
        "previous_record_sha256": previous,
        "state_sha256": state_sha,
        "state": state_mapping,
    }
    record_sha = hashlib.sha256(_canonical(body, code=code)).hexdigest()
    payload = _canonical({**body, "record_sha256": record_sha}, code=code)
    return payload, _Record(
        state=state,
        state_sha256=state_sha,
        previous_record_sha256=previous,
        record_sha256=record_sha,
    )


_RECORD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "binding_sha256",
        "config_identity_sha256",
        "revision",
        "fence_generation",
        "previous_record_sha256",
        "state_sha256",
        "state",
        "record_sha256",
    }
)


def _record_from_payload(payload: bytes, *, facts: _Facts) -> _Record:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RECORD_INVALID"
    fields = _exact_mapping(_decode_canonical(payload, code=code), fields=_RECORD_FIELDS, code=code)
    state = _state_from_mapping(fields["state"], facts=facts, code=code)
    revision = _nonnegative(fields["revision"], code=code)
    fence_generation = _nonnegative(fields["fence_generation"], code=code)
    previous = _sha256(fields["previous_record_sha256"], code=code, permit_zero=True)
    state_sha = _sha256(fields["state_sha256"], code=code)
    record_sha = _sha256(fields["record_sha256"], code=code)
    if (
        fields["schema"] != PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_SCHEMA
        or fields["version"] != _VERSION
        or fields["mode"] != _MODE
        or fields["binding_sha256"] != facts.binding_sha256
        or fields["config_identity_sha256"] != facts.config_identity_sha256
        or revision != state.revision
        or fence_generation != state.fence_generation
        or state_sha != _state_sha256(state, facts=facts, code=code)
    ):
        _fail(code)
    expected_payload, expected = _record_payload(
        facts=facts,
        state=state,
        previous_record_sha256=previous,
    )
    if payload != expected_payload or record_sha != expected.record_sha256:
        _fail(code)
    return expected


def _load_current(storage: _Storage, *, facts: _Facts) -> _Record | None:
    _write_create_only_at(
        storage.root_fd,
        _BINDING_FILENAME,
        facts.binding_payload,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BINDING_MISMATCH",
        allow_exact_existing=True,
    )
    try:
        os.stat(_CURRENT_FILENAME, dir_fd=storage.root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE"
        ) from exc
    _safe_child_metadata(
        storage.root_fd,
        _CURRENT_FILENAME,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_UNSAFE",
    )
    return _record_from_payload(
        _read_file_at(
            storage.root_fd,
            _CURRENT_FILENAME,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CURRENT_INVALID",
        ),
        facts=facts,
    )


def _require_checkpoint_callback(checkpoint: object) -> None:
    if not callable(getattr(checkpoint, "attest_v1_writer_admission_state", None)):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CHECKPOINT_MISSING")


def _checkpoint(
    checkpoint: object,
    *,
    facts: _Facts,
    record: _Record | None,
) -> None:
    callback = getattr(checkpoint, "attest_v1_writer_admission_state", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CHECKPOINT_MISSING")
    try:
        result = callback(
            binding_sha256=facts.binding_sha256,
            writer_admission_schema=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_SCHEMA,
            config_identity_sha256=facts.config_identity_sha256,
            revision=0 if record is None else record.state.revision,
            fence_generation=0 if record is None else record.state.fence_generation,
            previous_record_sha256=_ZERO_SHA256 if record is None else record.previous_record_sha256,
            state_sha256=_ZERO_SHA256 if record is None else record.state_sha256,
            record_sha256=_ZERO_SHA256 if record is None else record.record_sha256,
        )
    except PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreError(
            "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CHECKPOINT_REJECTED"
        ) from exc
    if result is not None:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_CHECKPOINT_INVALID")


def _runtime_compatible(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
    code: str,
) -> None:
    _state_mapping(state, facts=facts, code=code)
    # A nonempty runtime marker may only be the current root-owned runtime.
    # A restored state intentionally has None and requires a fresh revalidate.
    if (
        state.revalidated_runtime_instance_id is not None
        and state.revalidated_runtime_instance_id != facts.runtime_instance_id
    ):
        _fail(code)
    if not state.requires_fresh_witness_revalidation and state.revalidated_runtime_instance_id is None:
        _fail(code)


def _same_state(
    left: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    right: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
) -> bool:
    return _state_mapping(
        left,
        facts=facts,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_STATE_INVALID",
    ) == _state_mapping(
        right,
        facts=facts,
        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_STATE_INVALID",
    )


def _bootstrap_prior_matches(
    prior: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    *,
    facts: _Facts,
) -> bool:
    return _same_state(prior, _raw_startup_state(facts=facts), facts=facts)


def _validate_transition(
    transition: object,
    *,
    facts: _Facts,
) -> tuple[
    admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    admission.PhysicalOperationalFailoverV1WriterAdmissionState,
]:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_TRANSITION_INVALID"
    if type(transition) is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition:
        _fail(code)
    assert isinstance(transition, admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition)
    prior = transition.prior_state
    next_state = _writer_call(
        admission.apply_physical_operational_failover_v1_writer_admission_state_transition,
        state=prior,
        transition=transition,
        code=code,
    )
    if (
        type(next_state) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState
        or next_state is not transition.next_state
    ):
        _fail(code)
    _runtime_compatible(prior, facts=facts, code=code)
    _runtime_compatible(next_state, facts=facts, code=code)
    return prior, next_state


def _expected_matches(
    *,
    expected_revision: object,
    expected_fence_generation: object,
    prior: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
) -> None:
    code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_EXPECTED_HEAD_INVALID"
    revision = _nonnegative(expected_revision, code=code)
    generation = _nonnegative(expected_fence_generation, code=code)
    if revision != prior.revision or generation != prior.fence_generation:
        _fail(code)


def _persist_candidate(
    storage: _Storage,
    *,
    facts: _Facts,
    checkpoint: object,
    current: _Record | None,
    candidate: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
) -> _Record:
    previous = _ZERO_SHA256 if current is None else current.record_sha256
    payload, record = _record_payload(
        facts=facts,
        state=candidate,
        previous_record_sha256=previous,
    )
    _write_current_atomic(storage.root_fd, payload)
    # If this checkpoint fails after the atomic write, the outcome is
    # intentionally ambiguous and must be reconciled by reading current; do
    # not return success and invite a retry that could race a real effect.
    _checkpoint(checkpoint, facts=facts, record=record)
    readback = _load_current(storage, facts=facts)
    _checkpoint(checkpoint, facts=facts, record=readback)
    if readback != record:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_READBACK_INVALID")
    return record


class _SnapshotRestorer:
    """One-use structural source passed to the existing pure restore boundary."""

    def __init__(
        self,
        *,
        binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    ) -> None:
        self._binding = binding
        self._state = state
        self._used = False

    def restore_writer_admission_state(
        self,
        *,
        binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        if self._used or binding != self._binding:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_SOURCE_INVALID")
        self._used = True
        return self._state


class PhysicalOperationalFailoverV1WriterAdmissionDurableStateStore:
    """Root-gated state-store foundation; it never admits a writer by itself."""

    def __init__(
        self,
        config: RootOwnedPhysicalOperationalFailoverV1WriterAdmissionDurableStateStoreConfig,
        *,
        rollback_checkpoint: PhysicalOperationalFailoverV1WriterAdmissionDurableStateCheckpoint | None,
    ) -> None:
        self._config = config
        self._rollback_checkpoint = rollback_checkpoint

    def read_current_structural_state(
        self,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState | None:
        """Read only raw structural state; it carries no writer capability."""

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        with _locked_storage() as storage:
            current = _load_current(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=current)
            return None if current is None else current.state

    def restore_writer_admission_state(
        self,
        *,
        binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        """Protocol-compatible raw restore source; prefer ``restore_for_runtime``.

        This deliberately returns an *unattested* structural state.  It is
        useful only as input to the existing explicit V1 restore function,
        which must then be atomically persisted through ``restore_for_runtime``.
        """

        facts = _facts(self._config)
        if binding != facts.binding:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_BINDING_MISMATCH")
        current = self.read_current_structural_state()
        return _raw_startup_state(facts=facts) if current is None else current

    def compare_and_swap_state_transition(
        self,
        *,
        expected_revision: int,
        expected_fence_generation: int,
        transition: admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition,
    ) -> bool:
        """Persist one valid V1 transition using exact revision/fence CAS.

        ``True`` says the local record was durably replaced and read back;
        ``False`` says the state head changed before this attempt.  Neither
        result authorizes any database write, promotion, traffic action, or
        external effect.
        """

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        prior, candidate = _validate_transition(transition, facts=facts)
        _expected_matches(
            expected_revision=expected_revision,
            expected_fence_generation=expected_fence_generation,
            prior=prior,
        )
        with _locked_storage() as storage:
            current = _load_current(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=current)
            if current is None:
                if not _bootstrap_prior_matches(prior, facts=facts):
                    return False
            else:
                if (
                    current.state.revision != prior.revision
                    or current.state.fence_generation != prior.fence_generation
                    or current.state_sha256 != _state_sha256(
                        prior,
                        facts=facts,
                        code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_TRANSITION_INVALID",
                    )
                ):
                    return False
            _persist_candidate(
                storage,
                facts=facts,
                checkpoint=self._rollback_checkpoint,
                current=current,
                candidate=candidate,
            )
            return True

    def persist_state_transition(
        self,
        *,
        transition: admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition,
    ) -> bool:
        """Convenience form deriving exact CAS values from the V1 transition."""

        if type(transition) is not admission.PhysicalOperationalFailoverV1WriterAdmissionStateTransition:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_TRANSITION_INVALID")
        return self.compare_and_swap_state_transition(
            expected_revision=transition.prior_state.revision,
            expected_fence_generation=transition.prior_state.fence_generation,
            transition=transition,
        )

    def persist_writer_admission(
        self,
        *,
        admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
    ) -> bool:
        """Implement the narrow durable-boundary shape without doing the effect.

        The caller still has to couple this exact successful CAS with the real
        transaction commit or external-effect operation.  This method itself
        only writes the local V1 state record.
        """

        writer_module = _ADMISSION_MODULE
        writer_admission = admission
        code = "OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_ADMISSION_INVALID"
        facts = _facts(self._config)
        if (
            type(writer_admission) is not writer_module.PhysicalOperationalFailoverV1WriterAdmission
            or writer_admission._capability is not writer_module._ADMISSION_CAPABILITY
        ):
            _fail(code)
        transition = writer_admission.state_transition
        candidate = _writer_call(
            writer_module.apply_physical_operational_failover_v1_writer_admission_state_transition,
            state=transition.prior_state,
            transition=transition,
            code=code,
        )
        operation = writer_admission.operation
        if (
            candidate is not transition.next_state
            or type(candidate) is not writer_module.PhysicalOperationalFailoverV1WriterAdmissionState
            or candidate.active_term != writer_admission.term
            or type(operation) is not writer_module.PhysicalOperationalFailoverV1WriterOperation
            or operation._capability is not writer_module._OPERATION_CAPABILITY
            or operation.operation_kind
            not in {
                writer_module.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
                writer_module.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT,
            }
            or operation.runtime_instance_id != facts.runtime_instance_id
            or operation.opened_state_revision > transition.prior_state.revision
            or operation.fence_generation != transition.prior_state.fence_generation
            or operation.evidence_id != writer_admission.term.evidence_id
            or operation.writer_epoch != writer_admission.term.writer_epoch
            or operation.writer_lease_id != writer_admission.term.writer_lease_id
        ):
            _fail(code)
        admitted_at = _writer_call(writer_module._utc, writer_admission.admitted_at, code=code)
        opened_at = _writer_call(writer_module._utc, operation.opened_at, code=code)
        if (
            type(admitted_at) is not datetime
            or type(opened_at) is not datetime
            or opened_at > admitted_at
            or candidate.clock_floor != admitted_at
        ):
            _fail(code)
        return self.persist_state_transition(transition=transition)

    def restore_for_runtime(
        self,
        *,
        now: datetime,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        """Root-only restore-and-persist path that forces fresh revalidation.

        The returned state cannot be used to begin a writer operation until a
        fresh Witness revalidation has produced and durably persisted a new
        V1 transition.
        """

        facts = _facts(self._config)
        _require_root()
        _require_checkpoint_callback(self._rollback_checkpoint)
        with _locked_storage() as storage:
            source = _load_current(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=source)
        raw_source = _raw_startup_state(facts=facts) if source is None else source.state
        restored = _writer_call(
            admission.restore_physical_operational_failover_v1_writer_admission_state,
            config=facts.writer_config,
            state_restorer=_SnapshotRestorer(binding=facts.binding, state=raw_source),
            now=now,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_INVALID",
        )
        if type(restored) is not admission.PhysicalOperationalFailoverV1WriterAdmissionState:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_INVALID")
        if (
            restored.requires_fresh_witness_revalidation is not True
            or restored.revalidated_runtime_instance_id is not None
            or restored.revision != raw_source.revision + 1
            or restored.fence_generation != raw_source.fence_generation + 1
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_INVALID")
        _runtime_compatible(
            restored,
            facts=facts,
            code="OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_INVALID",
        )
        with _locked_storage() as storage:
            current = _load_current(storage, facts=facts)
            _checkpoint(self._rollback_checkpoint, facts=facts, record=current)
            if (source is None) != (current is None):
                _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_STALE")
            if source is not None and (
                current is None
                or current.record_sha256 != source.record_sha256
                or current.state_sha256 != source.state_sha256
                or not _same_state(current.state, raw_source, facts=facts)
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WRITER_ADMISSION_DURABLE_STATE_RESTORE_STALE")
            _persist_candidate(
                storage,
                facts=facts,
                checkpoint=self._rollback_checkpoint,
                current=current,
                candidate=restored,
            )
        return restored
