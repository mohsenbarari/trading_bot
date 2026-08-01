"""Fail-closed, local-only durable ledger for strict-runtime attestations.

The physical PostgreSQL renderer deliberately continues to refuse the
``strict_zero_loss`` profile.  This module is a smaller, non-authorizing
runtime prerequisite: after the existing strict-installation gate has
verified its four fresh root-owned attestations, a root-only caller can append
their *hashes* to one crash-safe local ledger.  The raw attestation bytes are
never persisted here and this module never opens a network connection,
executes a process, starts Docker, contacts PostgreSQL, changes a promotion
term, or grants a launch/write/Full-Matrix permission.

The ledger is tied to one exact strict-installation request, so its campaign,
release, manifest, route, term, strict-replay identity, writer-admission
integration and phase cannot be relabelled.  Entries are ordered by the four
required components, chained by digest, protected by an exclusive root-owned
lock, and atomically rewritten with file and directory ``fsync``.  A retry
after an uncertain crash is deliberately rejected as a replay rather than
silently executing the attestation step twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, Iterator

from core.append_only_sync_delta_batch import (
    SHA256_RE,
    canonical_json_bytes,
)
import core.physical_postgres_strict_runtime_installation_gate as _strict_gate


__all__ = (
    "DEFAULT_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_MAXIMUM_ENTRIES",
    "MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_BYTES",
    "PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE",
    "PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA",
    "PhysicalDurableReplayAttestationLedgerConfig",
    "PhysicalDurableReplayAttestationLedgerError",
    "PhysicalDurableReplayAttestationLedgerExpectation",
    "RecordedPhysicalDurableReplayAttestation",
    "VerifiedPhysicalDurableReplayAttestationLedger",
    "append_physical_durable_replay_attestation",
    "build_physical_durable_replay_attestation_ledger_expectation",
    "require_verified_physical_durable_replay_attestation_ledger",
    "verify_physical_durable_replay_attestation_ledger",
)


PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA = (
    "gold-trade-physical-durable-replay-attestation-ledger-v1"
)
PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_DEFAULT_ENABLED = False
PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE = (
    "strict-durable-replay-installation-attestation-v1"
)

DEFAULT_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_MAXIMUM_ENTRIES = 4
MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_BYTES = 512 * 1024
MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_BYTES = 64 * 1024

_LEDGER_VERSION = 1
_LEDGER_FILENAME = "strict-durable-replay-attestation-ledger.json"
_LOCK_FILENAME = "strict-durable-replay-attestation-ledger.lock"
_ENTRY_DOMAIN = (
    b"gold-trade-physical-durable-replay-attestation-ledger-entry-v1\x00"
)
_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00$",
    re.ASCII,
)
_LEDGER_FIELDS = frozenset(
    {
        "schema",
        "version",
        "configuration_sha256",
        "expectation_binding_sha256",
        "entries",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence",
        "record_id",
        "component",
        "phase",
        "campaign_id",
        "release_sha",
        "manifest_lock_sha256",
        "installation_binding_sha256",
        "strict_request_sha256",
        "route_binding_sha256",
        "writer_term_sha256",
        "strict_remote_durable_replay_identity_sha256",
        "writer_admission_integration_sha256",
        "expectation_binding_sha256",
        "direct_fi_to_ir_ssh",
        "direct_fi_to_ir_scp",
        "direct_fi_to_ir_postgres_control",
        "attestation_sha256",
        "attestation_bytes",
        "recorded_at",
        "previous_entry_sha256",
        "entry_sha256",
    }
)

_EXPECTATION_CAPABILITY = object()
_VERIFIED_CAPABILITY = object()


class PhysicalDurableReplayAttestationLedgerError(ValueError):
    """A local durable-replay attestation ledger rejection code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalDurableReplayAttestationLedgerExpectation:
    """Exact non-secret strict-gate binding required by this ledger.

    Instances are minted only from an opaque request accepted by the existing
    strict installation gate.  They are evidence expectations, never runtime
    execution authority.
    """

    strict_installation_request: (
        _strict_gate.PhysicalPostgresStrictRuntimeInstallationRequest
    )
    phase: str
    expectation_binding_sha256: str
    expected_attestation_sha256es: tuple[tuple[str, str], ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalDurableReplayAttestationLedgerConfig:
    """Default-off root-owned local state configuration.

    ``state_root`` must already be an absolute root-owned ``0700`` directory.
    It is intentionally not created by this module.  The ledger persists only
    hashes, sizes and binding metadata; no secret or raw attestation payload
    is written to it.
    """

    state_root: Path | None = None
    expectation: PhysicalDurableReplayAttestationLedgerExpectation | None = None
    enabled: bool = PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_DEFAULT_ENABLED
    maximum_entries: int = DEFAULT_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_MAXIMUM_ENTRIES


@dataclass(frozen=True)
class RecordedPhysicalDurableReplayAttestation:
    """A local persisted hash observation; explicitly non-authorizing."""

    ledger_path: Path
    record_id: str
    component: str
    sequence: int
    attestation_sha256: str
    entry_sha256: str
    recorded_at: datetime
    not_a_launch_authorization: bool = True


@dataclass(frozen=True)
class VerifiedPhysicalDurableReplayAttestationLedger:
    """An opaque completed local ledger observation, never launch authority."""

    ledger_path: Path
    expectation_binding_sha256: str
    strict_request_sha256: str
    ledger_sha256: str
    entry_sha256es: tuple[tuple[str, str], ...]
    verified_at: datetime
    strict_rendering_still_refused_by_scaffold: bool = True
    not_a_launch_authorization: bool = True
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ExpectationFacts:
    request: _strict_gate.PhysicalPostgresStrictRuntimeInstallationRequest
    phase: str
    expectation_binding_sha256: str
    expected_attestation_sha256es: tuple[tuple[str, str], ...]

    def expected_hash(self, component: str) -> str:
        for name, value in self.expected_attestation_sha256es:
            if name == component:
                return value
        raise KeyError(component)


@dataclass(frozen=True)
class _ConfigFacts:
    state_root: Path
    expectation: _ExpectationFacts
    maximum_entries: int
    configuration_sha256: str


def _fail(code: str) -> None:
    raise PhysicalDurableReplayAttestationLedgerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("DURABLE_REPLAY_LEDGER_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("DURABLE_REPLAY_LEDGER_JSON_CONSTANT_FORBIDDEN")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalDurableReplayAttestationLedgerError(code) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _record_id(value: object, *, code: str) -> str:
    if type(value) is not str or _SAFE_RECORD_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat() != value:
        _fail(code)
    return normalized


def _render_timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat()


def _phase(value: object, *, code: str) -> str:
    if value != PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE:
        _fail(code)
    return PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE


def _strict_request(value: object) -> _strict_gate.PhysicalPostgresStrictRuntimeInstallationRequest:
    try:
        return _strict_gate.require_physical_postgres_strict_runtime_installation_request(
            value
        )
    except (AttributeError, _strict_gate.PhysicalPostgresStrictRuntimeInstallationError) as exc:
        raise PhysicalDurableReplayAttestationLedgerError(
            "DURABLE_REPLAY_LEDGER_STRICT_REQUEST_INVALID"
        ) from exc


def _request_component_hashes(
    request: _strict_gate.PhysicalPostgresStrictRuntimeInstallationRequest,
) -> tuple[tuple[str, str], ...]:
    if (
        tuple(name for name, _value in request.components)
        != _strict_gate.STRICT_DURABLE_REPLAY_COMPONENTS
    ):
        _fail("DURABLE_REPLAY_LEDGER_STRICT_REQUEST_INVALID")
    values = tuple(
        (
            component,
            _sha256(
                expectation.installation_attestation_sha256,
                code="DURABLE_REPLAY_LEDGER_STRICT_REQUEST_INVALID",
            ),
        )
        for component, expectation in request.components
    )
    if len({value for _component, value in values}) != len(values):
        _fail("DURABLE_REPLAY_LEDGER_EXPECTED_ATTESTATION_REUSED")
    return values


def _expectation_mapping(
    *,
    request: _strict_gate.PhysicalPostgresStrictRuntimeInstallationRequest,
    phase: str,
    expected_attestation_sha256es: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA,
        "version": _LEDGER_VERSION,
        "phase": phase,
        "strict_request_sha256": request.request_sha256,
        "manifest_lock_sha256": request.manifest_lock_sha256,
        "installation_binding_sha256": request.installation_binding_sha256,
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "route_binding_sha256": request.route_binding_sha256,
        "writer_term_sha256": request.writer_term_sha256,
        "strict_remote_durable_replay_identity_sha256": (
            request.strict_remote_durable_replay_identity_sha256
        ),
        "writer_admission_integration_sha256": request.writer_admission_integration_sha256,
        "expected_attestation_sha256es": {
            component: digest for component, digest in expected_attestation_sha256es
        },
        "direct_fi_to_ir_ssh": False,
        "direct_fi_to_ir_scp": False,
        "direct_fi_to_ir_postgres_control": False,
        "not_a_launch_authorization": True,
    }


def build_physical_durable_replay_attestation_ledger_expectation(
    strict_installation_request: object,
    *,
    phase: str = PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_PHASE,
) -> PhysicalDurableReplayAttestationLedgerExpectation:
    """Bind this ledger to one exact strict-installation gate request.

    This is pure local validation.  It cannot make the strict renderer accept
    a profile and cannot read or write a ledger.
    """

    request = _strict_request(strict_installation_request)
    normalized_phase = _phase(phase, code="DURABLE_REPLAY_LEDGER_PHASE_INVALID")
    expected = _request_component_hashes(request)
    binding = hashlib.sha256(
        _canonical(
            _expectation_mapping(
                request=request,
                phase=normalized_phase,
                expected_attestation_sha256es=expected,
            ),
            code="DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID",
        )
    ).hexdigest()
    result = PhysicalDurableReplayAttestationLedgerExpectation(
        strict_installation_request=request,
        phase=normalized_phase,
        expectation_binding_sha256=binding,
        expected_attestation_sha256es=expected,
    )
    object.__setattr__(result, "_capability", _EXPECTATION_CAPABILITY)
    return result


def _normalise_expectation(value: object) -> _ExpectationFacts:
    if (
        type(value) is not PhysicalDurableReplayAttestationLedgerExpectation
        or value._capability is not _EXPECTATION_CAPABILITY
    ):
        _fail("DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID")
    request = _strict_request(value.strict_installation_request)
    phase = _phase(value.phase, code="DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID")
    expected = _request_component_hashes(request)
    if value.expected_attestation_sha256es != expected:
        _fail("DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID")
    binding = hashlib.sha256(
        _canonical(
            _expectation_mapping(
                request=request,
                phase=phase,
                expected_attestation_sha256es=expected,
            ),
            code="DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID",
        )
    ).hexdigest()
    if _sha256(
        value.expectation_binding_sha256,
        code="DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID",
    ) != binding:
        _fail("DURABLE_REPLAY_LEDGER_EXPECTATION_INVALID")
    return _ExpectationFacts(
        request=request,
        phase=phase,
        expectation_binding_sha256=binding,
        expected_attestation_sha256es=expected,
    )


def _secure_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail("DURABLE_REPLAY_LEDGER_STATE_ROOT_UNSAFE")
    try:
        metadata = os.lstat(value)
        resolved = value.resolve(strict=True)
    except OSError:
        _fail("DURABLE_REPLAY_LEDGER_STATE_ROOT_UNSAFE")
    if (
        resolved != value
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        _fail("DURABLE_REPLAY_LEDGER_STATE_ROOT_UNSAFE")
    return resolved


def _configuration_mapping(
    expectation: _ExpectationFacts,
    *,
    maximum_entries: int,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA,
        "version": _LEDGER_VERSION,
        "expectation_binding_sha256": expectation.expectation_binding_sha256,
        "strict_request_sha256": expectation.request.request_sha256,
        "maximum_entries": maximum_entries,
        "not_a_launch_authorization": True,
    }


def _normalise_config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalDurableReplayAttestationLedgerConfig:
        _fail("DURABLE_REPLAY_LEDGER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("DURABLE_REPLAY_LEDGER_DISABLED")
    if os.geteuid() != 0:
        _fail("DURABLE_REPLAY_LEDGER_ROOT_RUNTIME_REQUIRED")
    expectation = _normalise_expectation(value.expectation)
    if (
        type(value.maximum_entries) is not int
        or value.maximum_entries
        != DEFAULT_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_MAXIMUM_ENTRIES
    ):
        _fail("DURABLE_REPLAY_LEDGER_MAXIMUM_ENTRIES_INVALID")
    configuration_sha256 = hashlib.sha256(
        _canonical(
            _configuration_mapping(expectation, maximum_entries=value.maximum_entries),
            code="DURABLE_REPLAY_LEDGER_CONFIG_INVALID",
        )
    ).hexdigest()
    return _ConfigFacts(
        state_root=_secure_root(value.state_root),
        expectation=expectation,
        maximum_entries=value.maximum_entries,
        configuration_sha256=configuration_sha256,
    )


def _ledger_path(config: _ConfigFacts) -> Path:
    return config.state_root / _LEDGER_FILENAME


def _lock_path(config: _ConfigFacts) -> Path:
    return config.state_root / _LOCK_FILENAME


def _safe_lock(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("DURABLE_REPLAY_LEDGER_PLATFORM_NO_NOFOLLOW")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        _fail("DURABLE_REPLAY_LEDGER_LOCK_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("DURABLE_REPLAY_LEDGER_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


@contextmanager
def _locked_ledger(config: _ConfigFacts) -> Iterator[None]:
    descriptor = _safe_lock(_lock_path(config))
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _open_existing_ledger(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("DURABLE_REPLAY_LEDGER_PLATFORM_NO_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        raise
    except OSError:
        _fail("DURABLE_REPLAY_LEDGER_STATE_OPEN_FAILED")
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_BYTES
        ):
            _fail("DURABLE_REPLAY_LEDGER_STATE_UNSAFE")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_raw_ledger(path: Path) -> bytes | None:
    try:
        descriptor = _open_existing_ledger(path)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(descriptor)
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            try:
                chunk = os.read(descriptor, remaining)
            except OSError:
                _fail("DURABLE_REPLAY_LEDGER_STATE_READ_FAILED")
            if not chunk:
                _fail("DURABLE_REPLAY_LEDGER_STATE_READ_FAILED")
            chunks.append(chunk)
            remaining -= len(chunk)
        try:
            if os.read(descriptor, 1):
                _fail("DURABLE_REPLAY_LEDGER_STATE_CHANGED_DURING_READ")
        except OSError:
            _fail("DURABLE_REPLAY_LEDGER_STATE_READ_FAILED")
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mode != before.st_mode
            or after.st_uid != before.st_uid
            or after.st_nlink != before.st_nlink
        ):
            _fail("DURABLE_REPLAY_LEDGER_STATE_CHANGED_DURING_READ")
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _entry_without_hash(
    *,
    sequence: int,
    record_id: str,
    component: str,
    attestation_sha256: str,
    attestation_bytes: int,
    recorded_at: datetime,
    previous_entry_sha256: str | None,
    expectation: _ExpectationFacts,
) -> dict[str, Any]:
    request = expectation.request
    return {
        "sequence": sequence,
        "record_id": record_id,
        "component": component,
        "phase": expectation.phase,
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "manifest_lock_sha256": request.manifest_lock_sha256,
        "installation_binding_sha256": request.installation_binding_sha256,
        "strict_request_sha256": request.request_sha256,
        "route_binding_sha256": request.route_binding_sha256,
        "writer_term_sha256": request.writer_term_sha256,
        "strict_remote_durable_replay_identity_sha256": (
            request.strict_remote_durable_replay_identity_sha256
        ),
        "writer_admission_integration_sha256": request.writer_admission_integration_sha256,
        "expectation_binding_sha256": expectation.expectation_binding_sha256,
        "direct_fi_to_ir_ssh": False,
        "direct_fi_to_ir_scp": False,
        "direct_fi_to_ir_postgres_control": False,
        "attestation_sha256": attestation_sha256,
        "attestation_bytes": attestation_bytes,
        "recorded_at": _render_timestamp(
            recorded_at, code="DURABLE_REPLAY_LEDGER_RECORD_TIME_INVALID"
        ),
        "previous_entry_sha256": previous_entry_sha256,
    }


def _entry_sha256(unsigned: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _ENTRY_DOMAIN
        + _canonical(unsigned, code="DURABLE_REPLAY_LEDGER_ENTRY_CANONICAL_INVALID")
    ).hexdigest()


def _entry_mapping(
    *,
    sequence: int,
    record_id: str,
    component: str,
    attestation_sha256: str,
    attestation_bytes: int,
    recorded_at: datetime,
    previous_entry_sha256: str | None,
    expectation: _ExpectationFacts,
) -> dict[str, Any]:
    unsigned = _entry_without_hash(
        sequence=sequence,
        record_id=record_id,
        component=component,
        attestation_sha256=attestation_sha256,
        attestation_bytes=attestation_bytes,
        recorded_at=recorded_at,
        previous_entry_sha256=previous_entry_sha256,
        expectation=expectation,
    )
    return {**unsigned, "entry_sha256": _entry_sha256(unsigned)}


def _exact_mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return dict(value)


def _parse_canonical_ledger(raw: bytes, *, config: _ConfigFacts) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except PhysicalDurableReplayAttestationLedgerError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PhysicalDurableReplayAttestationLedgerError(
            "DURABLE_REPLAY_LEDGER_STATE_JSON_INVALID"
        ) from exc
    if not isinstance(parsed, dict) or _canonical(
        parsed, code="DURABLE_REPLAY_LEDGER_STATE_NONCANONICAL"
    ) != raw:
        _fail("DURABLE_REPLAY_LEDGER_STATE_NONCANONICAL")
    ledger = _exact_mapping(
        parsed, fields=_LEDGER_FIELDS, code="DURABLE_REPLAY_LEDGER_STATE_INVALID"
    )
    if (
        ledger["schema"] != PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA
        or type(ledger["version"]) is not int
        or ledger["version"] != _LEDGER_VERSION
        or _sha256(
            ledger["configuration_sha256"],
            code="DURABLE_REPLAY_LEDGER_STATE_CONFIGURATION_MISMATCH",
        )
        != config.configuration_sha256
        or _sha256(
            ledger["expectation_binding_sha256"],
            code="DURABLE_REPLAY_LEDGER_STATE_CONFIGURATION_MISMATCH",
        )
        != config.expectation.expectation_binding_sha256
        or not isinstance(ledger["entries"], list)
        or len(ledger["entries"]) > config.maximum_entries
    ):
        _fail("DURABLE_REPLAY_LEDGER_STATE_CONFIGURATION_MISMATCH")
    return [dict(item) if isinstance(item, Mapping) else item for item in ledger["entries"]]


def _validate_entries(
    entries: list[dict[str, Any]], *, config: _ConfigFacts
) -> tuple[tuple[str, str], ...]:
    previous: str | None = None
    seen_ids: set[str] = set()
    seen_attestations: set[str] = set()
    observed: list[tuple[str, str]] = []
    expectation = config.expectation
    request = expectation.request
    for ordinal, raw in enumerate(entries, start=1):
        item = _exact_mapping(
            raw, fields=_ENTRY_FIELDS, code="DURABLE_REPLAY_LEDGER_ENTRY_INVALID"
        )
        if type(item["sequence"]) is not int or item["sequence"] != ordinal:
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_SEQUENCE_INVALID")
        expected_component = _strict_gate.STRICT_DURABLE_REPLAY_COMPONENTS[ordinal - 1]
        if item["component"] != expected_component:
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_COMPONENT_ORDER_INVALID")
        record_id = _record_id(
            item["record_id"], code="DURABLE_REPLAY_LEDGER_ENTRY_ID_INVALID"
        )
        if record_id in seen_ids:
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_RECORD_REPLAYED")
        seen_ids.add(record_id)
        if (
            _phase(item["phase"], code="DURABLE_REPLAY_LEDGER_ENTRY_BINDING_MISMATCH")
            != expectation.phase
            or item["campaign_id"] != request.campaign_id
            or item["release_sha"] != request.release_sha
            or item["manifest_lock_sha256"] != request.manifest_lock_sha256
            or item["installation_binding_sha256"] != request.installation_binding_sha256
            or item["strict_request_sha256"] != request.request_sha256
            or item["route_binding_sha256"] != request.route_binding_sha256
            or item["writer_term_sha256"] != request.writer_term_sha256
            or item["strict_remote_durable_replay_identity_sha256"]
            != request.strict_remote_durable_replay_identity_sha256
            or item["writer_admission_integration_sha256"]
            != request.writer_admission_integration_sha256
            or item["expectation_binding_sha256"]
            != expectation.expectation_binding_sha256
            or item["direct_fi_to_ir_ssh"] is not False
            or item["direct_fi_to_ir_scp"] is not False
            or item["direct_fi_to_ir_postgres_control"] is not False
        ):
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_BINDING_MISMATCH")
        for name in (
            "manifest_lock_sha256",
            "installation_binding_sha256",
            "strict_request_sha256",
            "route_binding_sha256",
            "writer_term_sha256",
            "strict_remote_durable_replay_identity_sha256",
            "writer_admission_integration_sha256",
            "expectation_binding_sha256",
        ):
            _sha256(item[name], code="DURABLE_REPLAY_LEDGER_ENTRY_BINDING_MISMATCH")
        attestation_sha256 = _sha256(
            item["attestation_sha256"], code="DURABLE_REPLAY_LEDGER_ENTRY_ATTESTATION_INVALID"
        )
        if attestation_sha256 != expectation.expected_hash(expected_component):
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_ATTESTATION_MISMATCH")
        if attestation_sha256 in seen_attestations:
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_ATTESTATION_REPLAYED")
        seen_attestations.add(attestation_sha256)
        if (
            type(item["attestation_bytes"]) is not int
            or not 1 <= item["attestation_bytes"] <= MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_BYTES
        ):
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_ATTESTATION_INVALID")
        _timestamp(item["recorded_at"], code="DURABLE_REPLAY_LEDGER_ENTRY_TIME_INVALID")
        if previous is None:
            if item["previous_entry_sha256"] is not None:
                _fail("DURABLE_REPLAY_LEDGER_ENTRY_CHAIN_MISMATCH")
        else:
            if item["previous_entry_sha256"] != previous:
                _fail("DURABLE_REPLAY_LEDGER_ENTRY_CHAIN_MISMATCH")
            _sha256(
                item["previous_entry_sha256"],
                code="DURABLE_REPLAY_LEDGER_ENTRY_CHAIN_MISMATCH",
            )
        unsigned = dict(item)
        entry_sha256 = unsigned.pop("entry_sha256")
        if _sha256(
            entry_sha256, code="DURABLE_REPLAY_LEDGER_ENTRY_HASH_INVALID"
        ) != _entry_sha256(unsigned):
            _fail("DURABLE_REPLAY_LEDGER_ENTRY_CHAIN_MISMATCH")
        previous = entry_sha256
        observed.append((expected_component, entry_sha256))
    return tuple(observed)


def _load_entries(config: _ConfigFacts) -> tuple[list[dict[str, Any]], bytes | None]:
    raw = _read_raw_ledger(_ledger_path(config))
    if raw is None:
        return [], None
    entries = _parse_canonical_ledger(raw, config=config)
    _validate_entries(entries, config=config)
    return entries, raw


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        _fail("DURABLE_REPLAY_LEDGER_PLATFORM_NO_DIRECTORY_FSYNC")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        _fail("DURABLE_REPLAY_LEDGER_DIRECTORY_FSYNC_FAILED")
    try:
        os.fsync(descriptor)
    except OSError:
        _fail("DURABLE_REPLAY_LEDGER_DIRECTORY_FSYNC_FAILED")
    finally:
        os.close(descriptor)


def _ledger_mapping(
    entries: list[dict[str, Any]], *, config: _ConfigFacts
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_SCHEMA,
        "version": _LEDGER_VERSION,
        "configuration_sha256": config.configuration_sha256,
        "expectation_binding_sha256": config.expectation.expectation_binding_sha256,
        "entries": entries,
    }


def _write_atomic_ledger(entries: list[dict[str, Any]], *, config: _ConfigFacts) -> bytes:
    if len(entries) > config.maximum_entries:
        _fail("DURABLE_REPLAY_LEDGER_MAXIMUM_ENTRIES_EXCEEDED")
    payload = _canonical(
        _ledger_mapping(entries, config=config),
        code="DURABLE_REPLAY_LEDGER_STATE_WRITE_INVALID",
    )
    if not 1 <= len(payload) <= MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_LEDGER_BYTES:
        _fail("DURABLE_REPLAY_LEDGER_STATE_WRITE_INVALID")
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("DURABLE_REPLAY_LEDGER_PLATFORM_NO_NOFOLLOW")
    path = _ledger_path(config)
    temporary = config.state_root / (
        ".strict-durable-replay-attestation-" + secrets.token_hex(16) + ".tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    replaced = False
    try:
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except OSError:
            _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_OPEN_FAILED")
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_UNSAFE")
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except OSError:
                _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_WRITE_FAILED")
            if written <= 0:
                _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_WRITE_FAILED")
            view = view[written:]
        try:
            os.fsync(descriptor)
        except OSError:
            _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_FSYNC_FAILED")
        try:
            os.close(descriptor)
        except OSError:
            _fail("DURABLE_REPLAY_LEDGER_TEMPORARY_CLOSE_FAILED")
        finally:
            descriptor = -1
        try:
            os.replace(temporary, path)
        except OSError:
            _fail("DURABLE_REPLAY_LEDGER_ATOMIC_RENAME_FAILED")
        replaced = True
        _fsync_directory(config.state_root)
        metadata = os.lstat(path)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            _fail("DURABLE_REPLAY_LEDGER_STATE_UNSAFE")
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
                pass
    return payload


def _require_verified_installations(
    value: object,
    *,
    expectation: _ExpectationFacts,
    now: datetime,
) -> _strict_gate.VerifiedPhysicalPostgresStrictRuntimeInstallations:
    try:
        verified = _strict_gate.require_verified_physical_postgres_strict_runtime_installations(
            value,
            request=expectation.request,
            now=now,
        )
    except _strict_gate.PhysicalPostgresStrictRuntimeInstallationError as exc:
        raise PhysicalDurableReplayAttestationLedgerError(
            "DURABLE_REPLAY_LEDGER_STRICT_INSTALLATIONS_UNVERIFIED_OR_STALE"
        ) from exc
    if (
        verified.installation_binding_sha256
        != expectation.request.installation_binding_sha256
        or verified.request_sha256 != expectation.request.request_sha256
        or verified.attestation_sha256es != expectation.expected_attestation_sha256es
        or verified.strict_rendering_still_refused_by_scaffold is not True
        or verified.not_a_launch_authorization is not True
    ):
        _fail("DURABLE_REPLAY_LEDGER_STRICT_INSTALLATIONS_UNVERIFIED_OR_STALE")
    return verified


def append_physical_durable_replay_attestation(
    *,
    config: PhysicalDurableReplayAttestationLedgerConfig,
    verified_strict_installations: object,
    component: str,
    record_id: str,
    attestation_payload: bytes,
    now: datetime,
) -> RecordedPhysicalDurableReplayAttestation:
    """Durably record one exact gate-verified attestation hash once.

    The payload is compared against the already verified strict-gate hash but
    is never written into the ledger.  The next expected component is fixed;
    duplicate record IDs, components and payload hashes are replay failures.
    """

    facts = _normalise_config(config)
    observed_now = _utc(now, code="DURABLE_REPLAY_LEDGER_CLOCK_INVALID")
    _require_verified_installations(
        verified_strict_installations,
        expectation=facts.expectation,
        now=observed_now,
    )
    if (
        type(component) is not str
        or component not in _strict_gate.STRICT_DURABLE_REPLAY_COMPONENTS
    ):
        _fail("DURABLE_REPLAY_LEDGER_COMPONENT_INVALID")
    safe_record_id = _record_id(
        record_id, code="DURABLE_REPLAY_LEDGER_RECORD_ID_INVALID"
    )
    if (
        type(attestation_payload) is not bytes
        or not 1
        <= len(attestation_payload)
        <= MAX_PHYSICAL_DURABLE_REPLAY_ATTESTATION_BYTES
    ):
        _fail("DURABLE_REPLAY_LEDGER_ATTESTATION_PAYLOAD_INVALID")
    attestation_sha256 = hashlib.sha256(attestation_payload).hexdigest()
    if attestation_sha256 != facts.expectation.expected_hash(component):
        _fail("DURABLE_REPLAY_LEDGER_ATTESTATION_HASH_MISMATCH")
    with _locked_ledger(facts):
        entries, _raw = _load_entries(facts)
        if len(entries) >= facts.maximum_entries:
            _fail("DURABLE_REPLAY_LEDGER_COMPLETE")
        expected_component = _strict_gate.STRICT_DURABLE_REPLAY_COMPONENTS[len(entries)]
        if component != expected_component:
            _fail("DURABLE_REPLAY_LEDGER_COMPONENT_ORDER_INVALID")
        if any(item["record_id"] == safe_record_id for item in entries):
            _fail("DURABLE_REPLAY_LEDGER_RECORD_ID_REPLAYED")
        if any(item["component"] == component for item in entries):
            _fail("DURABLE_REPLAY_LEDGER_COMPONENT_REPLAYED")
        if any(item["attestation_sha256"] == attestation_sha256 for item in entries):
            _fail("DURABLE_REPLAY_LEDGER_ATTESTATION_REPLAYED")
        previous = entries[-1]["entry_sha256"] if entries else None
        entry = _entry_mapping(
            sequence=len(entries) + 1,
            record_id=safe_record_id,
            component=component,
            attestation_sha256=attestation_sha256,
            attestation_bytes=len(attestation_payload),
            recorded_at=observed_now,
            previous_entry_sha256=previous,
            expectation=facts.expectation,
        )
        _write_atomic_ledger(entries + [entry], config=facts)
    return RecordedPhysicalDurableReplayAttestation(
        ledger_path=_ledger_path(facts),
        record_id=safe_record_id,
        component=component,
        sequence=entry["sequence"],
        attestation_sha256=attestation_sha256,
        entry_sha256=entry["entry_sha256"],
        recorded_at=observed_now,
    )


def verify_physical_durable_replay_attestation_ledger(
    *,
    config: PhysicalDurableReplayAttestationLedgerConfig,
    verified_strict_installations: object,
    now: datetime,
) -> VerifiedPhysicalDurableReplayAttestationLedger:
    """Verify a complete local ledger while retaining renderer refusal.

    A ledger is accepted only if it contains the four exact, fresh
    strict-installation hashes in fixed order.  This result is an observation
    and cannot authorize a strict render, process launch, promotion, writer
    admission or Full Matrix.
    """

    facts = _normalise_config(config)
    observed_now = _utc(now, code="DURABLE_REPLAY_LEDGER_CLOCK_INVALID")
    _require_verified_installations(
        verified_strict_installations,
        expectation=facts.expectation,
        now=observed_now,
    )
    with _locked_ledger(facts):
        entries, raw = _load_entries(facts)
        if len(entries) != facts.maximum_entries or raw is None:
            _fail("DURABLE_REPLAY_LEDGER_INCOMPLETE")
        observed = _validate_entries(entries, config=facts)
    result = VerifiedPhysicalDurableReplayAttestationLedger(
        ledger_path=_ledger_path(facts),
        expectation_binding_sha256=facts.expectation.expectation_binding_sha256,
        strict_request_sha256=facts.expectation.request.request_sha256,
        ledger_sha256=hashlib.sha256(raw).hexdigest(),
        entry_sha256es=observed,
        verified_at=observed_now,
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    return result


def require_verified_physical_durable_replay_attestation_ledger(
    value: object,
    *,
    config: PhysicalDurableReplayAttestationLedgerConfig,
    verified_strict_installations: object,
    now: datetime,
) -> VerifiedPhysicalDurableReplayAttestationLedger:
    """Re-read and revalidate an opaque completed local ledger observation."""

    if (
        type(value) is not VerifiedPhysicalDurableReplayAttestationLedger
        or value._capability is not _VERIFIED_CAPABILITY
        or value.strict_rendering_still_refused_by_scaffold is not True
        or value.not_a_launch_authorization is not True
    ):
        _fail("DURABLE_REPLAY_LEDGER_VERIFIED_RESULT_INVALID")
    fresh = verify_physical_durable_replay_attestation_ledger(
        config=config,
        verified_strict_installations=verified_strict_installations,
        now=now,
    )
    if (
        value.ledger_path != fresh.ledger_path
        or value.expectation_binding_sha256 != fresh.expectation_binding_sha256
        or value.strict_request_sha256 != fresh.strict_request_sha256
        or value.ledger_sha256 != fresh.ledger_sha256
        or value.entry_sha256es != fresh.entry_sha256es
        or _utc(value.verified_at, code="DURABLE_REPLAY_LEDGER_VERIFIED_RESULT_INVALID")
        > _utc(now, code="DURABLE_REPLAY_LEDGER_CLOCK_INVALID")
    ):
        _fail("DURABLE_REPLAY_LEDGER_VERIFIED_RESULT_DIVERGED")
    return value
