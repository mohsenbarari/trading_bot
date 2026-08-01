"""Durable, fail-closed pre-operation reservation foundation for V4 P2/P4/P7.

This module is deliberately narrower than the existing Witness anchor journal.
The anchor journal records an effect that may already have started; it must not
be reinterpreted as an abortable reservation.  This module instead creates one
separate, root-owned durable RESERVED record before a future phase owner may
cross its executor boundary.

It is a foundation only:

* it is default-off and root-gated;
* it has no network, provider, host, Docker, database, or phase-executor code;
* it accepts only P2, P4, and P7 with their closed term directions;
* every durable record is bound to the exact pre-start driver claim, request,
  predecessor term, and successor intent.  Its in-process activation is then
  bound to the exact driver effect-start authority and immutable anchor;
* before it calls the future bridge, it requires the V4 driver's fresh,
  process-local pre-effect readiness provenance for that exact predecessor
  binding.  The durable record retains the readiness binding digest, never a
  serializable replacement for the opaque readiness capability;
* RESERVED is intentionally non-reusable.  There is no release, retry, or
  automatic expiry cleanup.  A restart, a crash after persistence, or expiry
  is indeterminate until a later, separately reviewed reconciliation protocol
  is added; and
* the initial returned receipt is not usable at an executor.  Only a later
  opaque, in-process activation capability may be required by a future owner,
  and it is never a writer, promotion, traffic, execution, or Full-Matrix
  permit.

The required ``PreEffectLinearizer`` is deliberately only a contract here,
not an implementation of the current receipt journal.  It must be supplied
by a separately reviewed, same-root journal/driver atomic bridge which both
reserves the exact live claim before effect-start and makes the journal reject
an unassociated later effect-start.  Until such a bridge exists, this module
must remain non-integrated and rejected for runtime use; a timestamp, an
opaque receipt, or the returned digest cannot honestly provide that
linearization by themselves.

The current P2/P4/P7 runtimes are intentionally not imported or integrated.
Future root-owned executor boundaries must require this capability immediately
before their first external action, then add a distinct durable consumed or
reconciled state machine.  Treating a RESERVED record as success would be
unsafe.

The fixed state root presently permits at most one RESERVED record.  It does
not implement a durable completion/reconciliation transition, so it cannot
advance one live campaign from P2 to P4 to P7.  The closed operation map means
that each named operation is individually recognized, not that this foundation
is a usable three-operation pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
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
import threading
from typing import Any, Protocol
from uuid import UUID
from weakref import WeakKeyDictionary

from core import physical_full_matrix_execution_driver_v4 as _driver


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_MAX_LIFETIME_SECONDS",
    "FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT",
    "PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATUS",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationCheckpoint",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationClock",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationError",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationPreEffectLinearizer",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry",
    "PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt",
    "PhysicalFullMatrixV4P2P4P7SuccessorIntent",
    "build_physical_full_matrix_v4_p2_p4_p7_successor_intent",
    "require_physical_full_matrix_v4_p2_p4_p7_pre_operation_reservation",
)


PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-p2-p4-p7-pre-operation-reservation-v1"
)
PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATUS = (
    "reserved-activated-before-future-executor-not-authorized"
)
_RECEIPT_STATUS = "reserved-awaiting-effect-start-activation-not-authorized"
DEFAULT_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_MAX_LIFETIME_SECONDS = 60

# Deployment owns this exact root.  The caller cannot select a path.  Tests
# patch this module constant to a root-owned temporary directory.
FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-full-matrix-v4-p2-p4-p7-pre-operation-reservations"
)

_VERSION = 1
_MODE = "root-witness-owned-v4-p2-p4-p7-pre-operation-reservation-v1"
_PINS_PURPOSE = "v4-p2-p4-p7-exact-pre-operation-pins-v1"
_RESERVED = "RESERVED"
_LOCK_FILENAME = "reservation.lock"
_BINDING_FILENAME = "binding.json"
_CURRENT_FILENAME = "current.json"
_RECORDS_DIRECTORY = "records"
_MAX_RECORD_BYTES = 128 * 1024
_MAX_RECORDS = 64
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_RECORD_NAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)

_BINDING_FIELDS = frozenset(
    {
        "campaign_id",
        "release_sha",
        "readiness_binding_sha256",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "source_site",
        "destination_site",
        "roundtrip_attestation_sha256",
        "roundtrip_configuration_sha256",
        "witness_transition_id",
        "witness_sequence",
    }
)
_ANCHOR_FIELDS = frozenset(
    {
        "journal_binding_sha256",
        "baseline_plan_binding_sha256",
        "anchor_genesis_sequence",
        "anchor_genesis_head_sha256",
        "anchor_previous_sequence",
        "anchor_previous_head_sha256",
        "anchor_sequence",
        "anchor_head_sha256",
        "anchor_commitment_sha256",
        "anchor_attestation_sha256",
        "anchor_local_previous_record_sha256",
        "anchor_local_event_sha256",
        "anchor_occurred_at",
    }
)
_SUCCESSOR_INTENT_FIELDS = frozenset(
    {
        "schema",
        "operation_phase_sequence",
        "operation_phase",
        "successor_phase_sequence",
        "successor_phase",
        "successor_binding",
    }
)
_PINS_FIELDS = frozenset(
    {
        "schema",
        "purpose",
        "witness_reservation_scope_sha256",
        "run_id",
        "plan_sha256",
        "phase_sequence",
        "phase",
        "oracle",
        "transport_profile",
        "phase_request_sha256",
        "effect_key",
        "claim_id",
        "predecessor_term",
        "successor_intent",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "state",
        "sequence",
        "previous_record_sha256",
        "reservation_identity_sha256",
        "reservation_id",
        "pre_effect_claim_linearization_sha256",
        "reserved_at",
        "expires_at",
        "pins",
        "record_sha256",
    }
)
_CURRENT_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "sequence",
        "reservation_identity_sha256",
        "record_sha256",
    }
)
_STATE_BINDING_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "witness_reservation_scope_sha256",
    }
)


class PhysicalFullMatrixV4P2P4P7PreOperationReservationError(RuntimeError):
    """The narrow reservation foundation failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code)


class PhysicalFullMatrixV4P2P4P7PreOperationReservationClock(Protocol):
    """Root-owned UTC clock.  The registry never uses a host time fallback."""

    def now_utc(self) -> datetime: ...


class PhysicalFullMatrixV4P2P4P7PreOperationReservationCheckpoint(Protocol):
    """Independent root/Witness-owned monotonic checkpoint for this state."""

    def attest_v4_p2_p4_p7_pre_operation_reservation_state(
        self,
        *,
        witness_reservation_scope_sha256: str,
        sequence: int,
        previous_record_sha256: str,
        record_sha256: str,
    ) -> None: ...


class PhysicalFullMatrixV4P2P4P7PreOperationReservationPreEffectLinearizer(Protocol):
    """Required future journal/driver atomic bridge.

    A production implementation must atomically and durably mutually exclude
    the supplied fixed reservation scope while reserving the exact live
    receipt-journal claim before effect-start.  It must make the journal reject
    a later effect-start that is not associated with that reservation, and
    retain the indeterminate scope fence if local record persistence is
    uncertain.  This module deliberately supplies no fallback implementation.
    """

    def linearize_v4_p2_p4_p7_before_effect_start(
        self,
        *,
        witness_reservation_scope_sha256: str,
        run_id: UUID,
        plan_sha256: str,
        phase_sequence: int,
        phase: str,
        phase_request_sha256: str,
        effect_key: str,
        claim_id: str,
        reservation_identity_sha256: str,
    ) -> str: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig:
    """Default-off fixed-scope policy.  It contains no host or provider data."""

    enabled: bool = PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_DEFAULT_ENABLED
    witness_reservation_scope_sha256: str | None = None
    maximum_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_MAX_LIFETIME_SECONDS
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV4P2P4P7SuccessorIntent:
    """Closed successor intent pinned before a future P2/P4/P7 executor.

    P2 intentionally carries no successor writer term: its next phase is
    recovery, not a writer transition.  P4/P7 carry the exact anticipated
    successor binding, but this still does not authorize that successor.
    """

    schema: str
    operation_phase_sequence: int
    operation_phase: str
    successor_phase_sequence: int
    successor_phase: str
    successor_binding: _driver.PhysicalFullMatrixV4ExecutionBinding | None


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability:
    """Opaque in-process proof of one freshly read-back RESERVED record.

    It intentionally contains no network client, host handle, writer permit,
    promotion authority, or generic execution permission.  A future concrete
    root owner may require it immediately before one executor callback only.
    """

    schema: str
    status: str
    reservation_identity_sha256: str
    reservation_id: str
    reservation_record_sha256: str
    phase_sequence: int
    phase: str
    effect_key: str
    journaled_effect_start_identity_sha256: str
    effect_start_anchor_head_sha256: str
    expires_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        reservation_identity_sha256: str,
        reservation_id: str,
        reservation_record_sha256: str,
        phase_sequence: int,
        phase: str,
        effect_key: str,
        journaled_effect_start_identity_sha256: str,
        effect_start_anchor_head_sha256: str,
        expires_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY_CONSTRUCTION_TOKEN:
            raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA),
            ("status", PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATUS),
            ("reservation_identity_sha256", reservation_identity_sha256),
            ("reservation_id", reservation_id),
            ("reservation_record_sha256", reservation_record_sha256),
            ("phase_sequence", phase_sequence),
            ("phase", phase),
            ("effect_key", effect_key),
            (
                "journaled_effect_start_identity_sha256",
                journaled_effect_start_identity_sha256,
            ),
            ("effect_start_anchor_head_sha256", effect_start_anchor_head_sha256),
            ("expires_at", expires_at),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("traffic_switch_authorized", False),
            ("external_effect_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt:
    """Opaque in-process handle to a read-back RESERVED record.

    The receipt is deliberately not an executor capability.  It can be
    activated only by the same live registry after the driver has made a real
    effect-start anchor.  A restart loses this in-process handle and leaves
    the durable reservation indeterminate.
    """

    schema: str
    status: str
    reservation_identity_sha256: str
    reservation_id: str
    reservation_record_sha256: str
    phase_sequence: int
    phase: str
    effect_key: str
    expires_at: datetime
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        reservation_identity_sha256: str,
        reservation_id: str,
        reservation_record_sha256: str,
        phase_sequence: int,
        phase: str,
        effect_key: str,
        expires_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY_CONSTRUCTION_TOKEN:
            raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA),
            ("status", _RECEIPT_STATUS),
            ("reservation_identity_sha256", reservation_identity_sha256),
            ("reservation_id", reservation_id),
            ("reservation_record_sha256", reservation_record_sha256),
            ("phase_sequence", phase_sequence),
            ("phase", phase),
            ("effect_key", effect_key),
            ("expires_at", expires_at),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("traffic_switch_authorized", False),
            ("external_effect_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    scope_sha256: str
    maximum_lifetime_seconds: int


@dataclass(frozen=True)
class _Operation:
    sequence: int
    phase: str
    predecessor_direction: tuple[str, str]
    successor_sequence: int
    successor_phase: str
    successor_direction: tuple[str, str] | None


_OPERATIONS: dict[str, _Operation] = {
    "fence-fi-writer-v2": _Operation(
        sequence=2,
        phase="fence-fi-writer-v2",
        predecessor_direction=("webapp_fi", "webapp_ir"),
        successor_sequence=3,
        successor_phase="recover-ir-through-object-storage-v2",
        successor_direction=None,
    ),
    "witness-promote-ir-v2": _Operation(
        sequence=4,
        phase="witness-promote-ir-v2",
        predecessor_direction=("webapp_fi", "webapp_ir"),
        successor_sequence=5,
        successor_phase="ir-writer-v2-witness-roundtrip-strict-ack-matrix",
        successor_direction=("webapp_ir", "webapp_fi"),
    ),
    "witness-restore-fi-writer-v2": _Operation(
        sequence=7,
        phase="witness-restore-fi-writer-v2",
        predecessor_direction=("webapp_ir", "webapp_fi"),
        successor_sequence=8,
        successor_phase="final-three-site-v2-convergence-oracle",
        successor_direction=("webapp_fi", "webapp_ir"),
    ),
}


@dataclass(frozen=True)
class _Pins:
    mapping: dict[str, object]
    identity_sha256: str
    phase_sequence: int
    phase: str
    effect_key: str


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    reservation_identity_sha256: str
    reservation_id: str
    pre_effect_claim_linearization_sha256: str
    reserved_at: datetime
    expires_at: datetime
    pins: dict[str, object]
    record_sha256: str


@dataclass(frozen=True)
class _Current:
    sequence: int
    reservation_identity_sha256: str
    record_sha256: str


@dataclass
class _Storage:
    root_fd: int
    records_fd: int


@dataclass
class _LiveReceiptState:
    registry: "PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry"
    scope_sha256: str
    pins: _Pins
    record: _Record
    activated: bool = False


@dataclass
class _LiveCapabilityState:
    registry: "PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry"
    receipt: PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt
    scope_sha256: str
    pins: _Pins
    record: _Record
    effect_start_identity_sha256: str
    anchor: dict[str, object]
    consumed: bool = False


_CAPABILITY_CONSTRUCTION_TOKEN = object()
_RECEIPT_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt, _LiveReceiptState
] = WeakKeyDictionary()
_CAPABILITY_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability, _LiveCapabilityState
] = WeakKeyDictionary()


def _require_root() -> None:
    if os.geteuid() != 0:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_ROOT_REQUIRED")


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not permit_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _positive(value: object, *, code: str, permit_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if permit_zero else 1) or value > 2**63 - 1:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: object, *, code: str) -> str:
    result = _utc(value, code=code)
    if result.microsecond:
        return result.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return result.isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError:
        _fail(code)
    if _render_timestamp(result, code=code) != value:
        _fail(code)
    return result


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
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc


def _strict_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _decode_canonical(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_RECORD_BYTES:
        _fail(code)
    try:
        decoded = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _item: _fail(code),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        PhysicalFullMatrixV4P2P4P7PreOperationReservationError,
    ):
        _fail(code)
    if type(decoded) is not dict or _canonical(decoded, code=code) != value:
        _fail(code)
    return decoded


def _facts(value: object) -> _Facts:
    if type(value) is not PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_DISABLED")
    scope = _sha256(
        value.witness_reservation_scope_sha256,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CONFIG_INVALID",
    )
    maximum = value.maximum_lifetime_seconds
    if type(maximum) is not int or not 1 <= maximum <= 300:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CONFIG_INVALID")
    return _Facts(scope_sha256=scope, maximum_lifetime_seconds=maximum)


def _now(clock: object) -> datetime:
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CLOCK_INVALID")
    try:
        return _utc(
            callback(),
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CLOCK_INVALID",
        )
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CLOCK_INVALID"
        ) from exc


def _binding_mapping(
    value: object,
    *,
    direction: tuple[str, str],
    code: str,
) -> dict[str, object]:
    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionBinding:
        _fail(code)
    try:
        _driver._binding(value, direction=direction)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    result = {name: getattr(value, name) for name in _BINDING_FIELDS}
    if _binding_from_mapping(result, direction=direction, code=code) != value:
        _fail(code)
    return result


def _binding_from_mapping(
    value: object,
    *,
    direction: tuple[str, str],
    code: str,
) -> _driver.PhysicalFullMatrixV4ExecutionBinding:
    if type(value) is not dict or set(value) != _BINDING_FIELDS:
        _fail(code)
    try:
        binding = _driver.PhysicalFullMatrixV4ExecutionBinding(**value)  # type: ignore[arg-type]
        _driver._binding(binding, direction=direction)
    except (TypeError, _driver.PhysicalFullMatrixV4ExecutionDriverError) as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    return binding


def _anchor_mapping(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not _driver.PhysicalFullMatrixV4EffectStartAnchorProof:
        _fail(code)
    mapping = {
        "journal_binding_sha256": value.journal_binding_sha256,
        "baseline_plan_binding_sha256": value.baseline_plan_binding_sha256,
        "anchor_genesis_sequence": value.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": value.anchor_genesis_head_sha256,
        "anchor_previous_sequence": value.anchor_previous_sequence,
        "anchor_previous_head_sha256": value.anchor_previous_head_sha256,
        "anchor_sequence": value.anchor_sequence,
        "anchor_head_sha256": value.anchor_head_sha256,
        "anchor_commitment_sha256": value.anchor_commitment_sha256,
        "anchor_attestation_sha256": value.anchor_attestation_sha256,
        "anchor_local_previous_record_sha256": value.anchor_local_previous_record_sha256,
        "anchor_local_event_sha256": value.anchor_local_event_sha256,
        "anchor_occurred_at": _render_timestamp(value.anchor_occurred_at, code=code),
    }
    _anchor_from_mapping(mapping, code=code)
    return mapping


def _anchor_from_mapping(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != _ANCHOR_FIELDS:
        _fail(code)
    journal = _sha256(value["journal_binding_sha256"], code=code)
    baseline = _sha256(value["baseline_plan_binding_sha256"], code=code)
    genesis_sequence = _positive(value["anchor_genesis_sequence"], code=code, permit_zero=True)
    genesis_head = _sha256(value["anchor_genesis_head_sha256"], code=code, permit_zero=True)
    previous_sequence = _positive(value["anchor_previous_sequence"], code=code, permit_zero=True)
    previous_head = _sha256(value["anchor_previous_head_sha256"], code=code, permit_zero=True)
    sequence = _positive(value["anchor_sequence"], code=code)
    if previous_sequence < genesis_sequence or sequence != previous_sequence + 1:
        _fail(code)
    if previous_sequence == genesis_sequence and previous_head != genesis_head:
        _fail(code)
    result = {
        "journal_binding_sha256": journal,
        "baseline_plan_binding_sha256": baseline,
        "anchor_genesis_sequence": genesis_sequence,
        "anchor_genesis_head_sha256": genesis_head,
        "anchor_previous_sequence": previous_sequence,
        "anchor_previous_head_sha256": previous_head,
        "anchor_sequence": sequence,
        "anchor_head_sha256": _sha256(value["anchor_head_sha256"], code=code),
        "anchor_commitment_sha256": _sha256(value["anchor_commitment_sha256"], code=code),
        "anchor_attestation_sha256": _sha256(value["anchor_attestation_sha256"], code=code),
        "anchor_local_previous_record_sha256": _sha256(
            value["anchor_local_previous_record_sha256"], code=code, permit_zero=True
        ),
        "anchor_local_event_sha256": _sha256(value["anchor_local_event_sha256"], code=code),
        "anchor_occurred_at": _render_timestamp(
            _timestamp(value["anchor_occurred_at"], code=code), code=code
        ),
    }
    if result != value:
        _fail(code)
    return result


def _operation_for(
    *,
    sequence: object,
    phase: object,
    code: str,
) -> _Operation:
    checked_sequence = _positive(sequence, code=code)
    if type(phase) is not str:
        _fail(code)
    operation = _OPERATIONS.get(phase)
    if operation is None or operation.sequence != checked_sequence:
        _fail(code)
    return operation


def build_physical_full_matrix_v4_p2_p4_p7_successor_intent(
    *,
    operation_phase_sequence: int,
    operation_phase: str,
    successor_binding: _driver.PhysicalFullMatrixV4ExecutionBinding | None,
) -> PhysicalFullMatrixV4P2P4P7SuccessorIntent:
    """Build a closed successor intent without starting any operation."""

    operation = _operation_for(
        sequence=operation_phase_sequence,
        phase=operation_phase,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SUCCESSOR_INTENT_INVALID",
    )
    if operation.successor_direction is None:
        if successor_binding is not None:
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SUCCESSOR_INTENT_INVALID")
    else:
        _binding_mapping(
            successor_binding,
            direction=operation.successor_direction,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SUCCESSOR_INTENT_INVALID",
        )
    return PhysicalFullMatrixV4P2P4P7SuccessorIntent(
        schema=PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
        operation_phase_sequence=operation.sequence,
        operation_phase=operation.phase,
        successor_phase_sequence=operation.successor_sequence,
        successor_phase=operation.successor_phase,
        successor_binding=successor_binding,
    )


def _successor_intent_mapping(
    value: object,
    *,
    operation: _Operation,
    predecessor: _driver.PhysicalFullMatrixV4ExecutionBinding,
    code: str,
) -> dict[str, object]:
    if type(value) is not PhysicalFullMatrixV4P2P4P7SuccessorIntent:
        _fail(code)
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        or value.operation_phase_sequence != operation.sequence
        or value.operation_phase != operation.phase
        or value.successor_phase_sequence != operation.successor_sequence
        or value.successor_phase != operation.successor_phase
    ):
        _fail(code)
    successor_mapping: dict[str, object] | None
    if operation.successor_direction is None:
        if value.successor_binding is not None:
            _fail(code)
        successor_mapping = None
    else:
        successor = _binding_from_mapping(
            _binding_mapping(
                value.successor_binding,
                direction=operation.successor_direction,
                code=code,
            ),
            direction=operation.successor_direction,
            code=code,
        )
        if (
            successor.campaign_id != predecessor.campaign_id
            or successor.release_sha != predecessor.release_sha
            or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
            or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
            or successor.roundtrip_attestation_sha256
            == predecessor.roundtrip_attestation_sha256
            or successor.roundtrip_configuration_sha256
            != predecessor.roundtrip_configuration_sha256
            or successor.writer_epoch <= predecessor.writer_epoch
            or successor.witness_sequence <= predecessor.witness_sequence
            or successor.writer_lease_id == predecessor.writer_lease_id
            or successor.witness_transition_id == predecessor.witness_transition_id
            or successor.witnessed_term_proof_sha256
            == predecessor.witnessed_term_proof_sha256
            or successor.readiness_binding_sha256 == predecessor.readiness_binding_sha256
        ):
            _fail(code)
        successor_mapping = _binding_mapping(
            successor,
            direction=operation.successor_direction,
            code=code,
        )
    result = {
        "schema": value.schema,
        "operation_phase_sequence": value.operation_phase_sequence,
        "operation_phase": value.operation_phase,
        "successor_phase_sequence": value.successor_phase_sequence,
        "successor_phase": value.successor_phase,
        "successor_binding": successor_mapping,
    }
    _successor_intent_from_mapping(
        result,
        operation=operation,
        predecessor=predecessor,
        code=code,
    )
    return result


def _successor_intent_from_mapping(
    value: object,
    *,
    operation: _Operation,
    predecessor: _driver.PhysicalFullMatrixV4ExecutionBinding,
    code: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != _SUCCESSOR_INTENT_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        or value["operation_phase_sequence"] != operation.sequence
        or value["operation_phase"] != operation.phase
        or value["successor_phase_sequence"] != operation.successor_sequence
        or value["successor_phase"] != operation.successor_phase
    ):
        _fail(code)
    if operation.successor_direction is None:
        if value["successor_binding"] is not None:
            _fail(code)
        return dict(value)
    successor = _binding_from_mapping(
        value["successor_binding"],
        direction=operation.successor_direction,
        code=code,
    )
    if (
        successor.campaign_id != predecessor.campaign_id
        or successor.release_sha != predecessor.release_sha
        or successor.route_commitment_sha256 == predecessor.route_commitment_sha256
        or successor.four_role_binding_sha256 != predecessor.four_role_binding_sha256
        or successor.roundtrip_attestation_sha256
        == predecessor.roundtrip_attestation_sha256
        or successor.roundtrip_configuration_sha256
        != predecessor.roundtrip_configuration_sha256
        or successor.writer_epoch <= predecessor.writer_epoch
        or successor.witness_sequence <= predecessor.witness_sequence
        or successor.writer_lease_id == predecessor.writer_lease_id
        or successor.witness_transition_id == predecessor.witness_transition_id
        or successor.witnessed_term_proof_sha256
        == predecessor.witnessed_term_proof_sha256
        or successor.readiness_binding_sha256 == predecessor.readiness_binding_sha256
    ):
        _fail(code)
    canonical = dict(value)
    canonical["successor_binding"] = _binding_mapping(
        successor,
        direction=operation.successor_direction,
        code=code,
    )
    if canonical != value:
        _fail(code)
    return canonical


def _pre_start_pins(
    *,
    claim: object,
    request: object,
    successor_intent: object,
    facts: _Facts,
    now: datetime,
    code: str,
) -> _Pins:
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail(code)
    if (
        type(claim) is not _driver.PhysicalFullMatrixV4PhaseClaim
        or claim.claim_id is None
        or claim.existing_receipt is not None
        or claim.indeterminate is not False
        or request._effect_start_authority is not None
        or request._effect_start_anchor_proof is not None
    ):
        _fail(code)
    operation = _operation_for(
        sequence=request.phase.sequence,
        phase=request.phase.name,
        code=code,
    )
    predecessor_mapping = _binding_mapping(
        request.binding,
        direction=operation.predecessor_direction,
        code=code,
    )
    predecessor = _binding_from_mapping(
        predecessor_mapping,
        direction=operation.predecessor_direction,
        code=code,
    )
    _require_pre_effect_readiness(
        request=request,
        operation=operation,
        predecessor_mapping=predecessor_mapping,
        now=now,
        code=code,
    )
    if (
        claim.run_id != request.run_id
        or claim.plan_sha256 != request.plan_sha256
        or claim.sequence != request.phase.sequence
        or claim.phase_request_sha256 != request.phase_request_sha256
        or claim.effect_key != request.effect_key
    ):
        _fail(code)
    intent_mapping = _successor_intent_mapping(
        successor_intent,
        operation=operation,
        predecessor=predecessor,
        code=code,
    )
    if (
        type(request.run_id) is not UUID
        or request.phase.sequence != operation.sequence
        or request.phase.name != operation.phase
    ):
        _fail(code)
    mapping: dict[str, object] = {
        "schema": PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
        "purpose": _PINS_PURPOSE,
        "witness_reservation_scope_sha256": facts.scope_sha256,
        "run_id": str(request.run_id),
        "plan_sha256": _sha256(request.plan_sha256, code=code),
        "phase_sequence": operation.sequence,
        "phase": operation.phase,
        "oracle": request.phase.oracle,
        "transport_profile": request.phase.transport_profile,
        "phase_request_sha256": _sha256(request.phase_request_sha256, code=code),
        "effect_key": _sha256(request.effect_key, code=code),
        "claim_id": _identifier(claim.claim_id, code=code),
        "predecessor_term": predecessor_mapping,
        "successor_intent": intent_mapping,
    }
    canonical = _pins_from_mapping(mapping, facts=facts, code=code)
    return _Pins(
        mapping=canonical,
        identity_sha256=hashlib.sha256(_canonical(canonical, code=code)).hexdigest(),
        phase_sequence=operation.sequence,
        phase=operation.phase,
        effect_key=request.effect_key,
    )


def _require_pre_effect_readiness(
    *,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    operation: _Operation,
    predecessor_mapping: dict[str, object],
    now: datetime,
    code: str,
) -> None:
    """Revalidate V4's opaque pre-start readiness at this reservation seam.

    The receipt record can safely retain the public readiness binding digest
    embedded in ``predecessor_mapping``; it must never treat a serialized
    lookalike as the V4 process-local readiness capability.  The V4 driver's
    own verifier remains the only authority for that provenance.
    """

    evidence = request.pre_effect_readiness_evidence
    if type(evidence) is not _driver.PhysicalFullMatrixV4ReadinessEvidence:
        _fail(code)
    try:
        observed = _binding_mapping(
            evidence.binding,
            direction=operation.predecessor_direction,
            code=code,
        )
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    if observed != predecessor_mapping:
        _fail(code)
    try:
        _driver._validate_readiness_evidence(
            evidence,
            binding=_driver._snapshot_binding(
                request.binding,
                direction=operation.predecessor_direction,
            ),
            now=_utc(now, code=code),
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    except Exception as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc


def _activation_pins(
    *,
    request: object,
    reservation_pins: _Pins,
    facts: _Facts,
    code: str,
) -> tuple[str, dict[str, object]]:
    """Bind a live receipt to the exact effect-start that followed it."""

    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest:
        _fail(code)
    try:
        authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
            request=request
        )
        anchor = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
            request=request
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    expected = reservation_pins.mapping
    operation = _operation_for(
        sequence=expected["phase_sequence"],
        phase=expected["phase"],
        code=code,
    )
    predecessor = _binding_from_mapping(
        expected["predecessor_term"],
        direction=operation.predecessor_direction,
        code=code,
    )
    if (
        authority.run_id != UUID(expected["run_id"])  # type: ignore[arg-type]
        or authority.plan_sha256 != expected["plan_sha256"]
        or authority.phase.sequence != operation.sequence
        or authority.phase.name != operation.phase
        or authority.effect_key != expected["effect_key"]
        or authority.phase_request_sha256 != expected["phase_request_sha256"]
        or authority.binding != predecessor
        or authority.claim_id != expected["claim_id"]
        or anchor.run_id != authority.run_id
        or anchor.plan_sha256 != authority.plan_sha256
        or anchor.phase != authority.phase
        or anchor.effect_key != authority.effect_key
        or anchor.phase_request_sha256 != authority.phase_request_sha256
        or anchor.binding != authority.binding
        or anchor.claim_id != authority.claim_id
        or anchor.journaled_effect_start_identity_sha256
        != authority.journaled_effect_start_identity_sha256
    ):
        _fail(code)
    identity = _sha256(authority.journaled_effect_start_identity_sha256, code=code)
    return identity, _anchor_mapping(anchor, code=code)


def _pins_from_mapping(
    value: object,
    *,
    facts: _Facts,
    code: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != _PINS_FIELDS:
        _fail(code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        or value["purpose"] != _PINS_PURPOSE
        or _sha256(value["witness_reservation_scope_sha256"], code=code)
        != facts.scope_sha256
    ):
        _fail(code)
    try:
        run_id = UUID(str(value["run_id"]))
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if str(run_id) != value["run_id"]:
        _fail(code)
    operation = _operation_for(
        sequence=value["phase_sequence"],
        phase=value["phase"],
        code=code,
    )
    phase = next(
        (
            item
            for item in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES
            if item.sequence == operation.sequence and item.name == operation.phase
        ),
        None,
    )
    if (
        phase is None
        or value["oracle"] != phase.oracle
        or value["transport_profile"] != phase.transport_profile
    ):
        _fail(code)
    predecessor = _binding_from_mapping(
        value["predecessor_term"],
        direction=operation.predecessor_direction,
        code=code,
    )
    intent = _successor_intent_from_mapping(
        value["successor_intent"],
        operation=operation,
        predecessor=predecessor,
        code=code,
    )
    canonical = {
        "schema": PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
        "purpose": _PINS_PURPOSE,
        "witness_reservation_scope_sha256": facts.scope_sha256,
        "run_id": str(run_id),
        "plan_sha256": _sha256(value["plan_sha256"], code=code),
        "phase_sequence": operation.sequence,
        "phase": operation.phase,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        "phase_request_sha256": _sha256(value["phase_request_sha256"], code=code),
        "effect_key": _sha256(value["effect_key"], code=code),
        "claim_id": _identifier(value["claim_id"], code=code),
        "predecessor_term": _binding_mapping(
            predecessor,
            direction=operation.predecessor_direction,
            code=code,
        ),
        "successor_intent": intent,
    }
    if canonical != value:
        _fail(code)
    return canonical


def _state_binding_payload(*, facts: _Facts) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "witness_reservation_scope_sha256": facts.scope_sha256,
        },
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_BINDING_INVALID",
    )


def _record_payload(
    *,
    facts: _Facts,
    sequence: int,
    previous_record_sha256: str,
    reservation_identity_sha256: str,
    reservation_id: str,
    pre_effect_claim_linearization_sha256: str,
    reserved_at: datetime,
    expires_at: datetime,
    pins: dict[str, object],
) -> tuple[bytes, str]:
    canonical_pins = _pins_from_mapping(
        pins,
        facts=facts,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
    )
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
        "version": _VERSION,
        "mode": _MODE,
        "state": _RESERVED,
        "sequence": _positive(sequence, code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID"),
        "previous_record_sha256": _sha256(
            previous_record_sha256,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
            permit_zero=sequence == 1,
        ),
        "reservation_identity_sha256": _sha256(
            reservation_identity_sha256,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        "reservation_id": _sha256(
            reservation_id,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        "pre_effect_claim_linearization_sha256": _sha256(
            pre_effect_claim_linearization_sha256,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        "reserved_at": _render_timestamp(
            reserved_at,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        "expires_at": _render_timestamp(
            expires_at,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        "pins": canonical_pins,
    }
    if (
        body["expires_at"] <= body["reserved_at"]
        or hashlib.sha256(_canonical(canonical_pins, code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID")).hexdigest()
        != body["reservation_identity_sha256"]
    ):
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID")
    digest = hashlib.sha256(
        _canonical(body, code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID")
    ).hexdigest()
    return (
        _canonical(
            {**body, "record_sha256": digest},
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        ),
        digest,
    )


def _record_from_payload(
    payload: bytes,
    *,
    facts: _Facts,
    expected_sequence: int,
    expected_previous_record_sha256: str,
) -> _Record:
    code = "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID"
    value = _decode_canonical(payload, code=code)
    if set(value) != _RECORD_FIELDS:
        _fail(code)
    sequence = _positive(value["sequence"], code=code)
    previous = _sha256(value["previous_record_sha256"], code=code, permit_zero=sequence == 1)
    identity = _sha256(value["reservation_identity_sha256"], code=code)
    reservation_id = _sha256(value["reservation_id"], code=code)
    linearization = _sha256(value["pre_effect_claim_linearization_sha256"], code=code)
    reserved_at = _timestamp(value["reserved_at"], code=code)
    expires_at = _timestamp(value["expires_at"], code=code)
    pins = _pins_from_mapping(value["pins"], facts=facts, code=code)
    record_sha256 = _sha256(value["record_sha256"], code=code)
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        or value["version"] != _VERSION
        or value["mode"] != _MODE
        or value["state"] != _RESERVED
        or sequence != expected_sequence
        or previous != expected_previous_record_sha256
        or expires_at <= reserved_at
        or identity != hashlib.sha256(_canonical(pins, code=code)).hexdigest()
    ):
        _fail(code)
    expected_payload, expected_sha256 = _record_payload(
        facts=facts,
        sequence=sequence,
        previous_record_sha256=previous,
        reservation_identity_sha256=identity,
        reservation_id=reservation_id,
        pre_effect_claim_linearization_sha256=linearization,
        reserved_at=reserved_at,
        expires_at=expires_at,
        pins=pins,
    )
    if payload != expected_payload or record_sha256 != expected_sha256:
        _fail(code)
    return _Record(
        sequence=sequence,
        previous_record_sha256=previous,
        reservation_identity_sha256=identity,
        reservation_id=reservation_id,
        pre_effect_claim_linearization_sha256=linearization,
        reserved_at=reserved_at,
        expires_at=expires_at,
        pins=pins,
        record_sha256=record_sha256,
    )


def _current_payload(*, current: _Current) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "sequence": current.sequence,
            "reservation_identity_sha256": current.reservation_identity_sha256,
            "record_sha256": current.record_sha256,
        },
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_INVALID",
    )


def _current_from_payload(payload: bytes) -> _Current:
    code = "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_INVALID"
    value = _decode_canonical(payload, code=code)
    if (
        set(value) != _CURRENT_FIELDS
        or value["schema"] != PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        or value["version"] != _VERSION
        or value["mode"] != _MODE
    ):
        _fail(code)
    return _Current(
        sequence=_positive(value["sequence"], code=code),
        reservation_identity_sha256=_sha256(value["reservation_identity_sha256"], code=code),
        record_sha256=_sha256(value["record_sha256"], code=code),
    )


def _safe_metadata(
    fd: int,
    *,
    directory: bool,
    mode: int,
    code: str,
) -> None:
    try:
        metadata = os.fstat(fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    if (
        bool(stat.S_ISDIR(metadata.st_mode)) != directory
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
        or (not directory and metadata.st_nlink != 1)
    ):
        _fail(code)


def _ensure_directory(path: Path, *, mode: int, code: str) -> int:
    if not path.is_absolute():
        _fail(code)
    try:
        try:
            os.mkdir(path, mode=mode)
        except FileExistsError:
            pass
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    try:
        _safe_metadata(descriptor, directory=True, mode=mode, code=code)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _ensure_child_directory(
    parent_fd: int,
    *,
    name: str,
    mode: int,
    code: str,
) -> int:
    try:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent_fd)
        except FileExistsError:
            pass
        descriptor = os.open(
            name,
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    try:
        _safe_metadata(descriptor, directory=True, mode=mode, code=code)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_file_at(parent_fd: int, name: str, *, code: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        _safe_metadata(descriptor, directory=False, mode=0o600, code=code)
        size = os.fstat(descriptor).st_size
        if not 1 <= size <= _MAX_RECORD_BYTES:
            _fail(code)
        data = bytearray()
        while len(data) < size:
            chunk = os.read(descriptor, size - len(data))
            if not chunk:
                _fail(code)
            data.extend(chunk)
        if os.read(descriptor, 1):
            _fail(code)
        return bytes(data)
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_all(fd: int, payload: bytes, *, code: str) -> None:
    view = memoryview(payload)
    try:
        while view:
            count = os.write(fd, view)
            if count <= 0:
                _fail(code)
            view = view[count:]
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail(code)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        _safe_metadata(descriptor, directory=False, mode=0o600, code=code)
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent_fd)
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_current_atomic(root_fd: int, payload: bytes) -> None:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_RECORD_BYTES:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_INVALID")
    temporary = ".current-" + secrets.token_bytes(32).hex() + ".tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        _safe_metadata(
            descriptor,
            directory=False,
            mode=0o600,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_WRITE_FAILED",
        )
        _write_all(
            descriptor,
            payload,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_WRITE_FAILED",
        )
        os.fdatasync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            _CURRENT_FILENAME,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
        os.fsync(root_fd)
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_WRITE_FAILED"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _exists_at(parent_fd: int, name: str, *, code: str) -> bool:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(code) from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        _fail(code)
    return True


def _record_names(records_fd: int) -> list[tuple[int, str, str]]:
    try:
        names = os.listdir(records_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_INVALID"
        ) from exc
    result: list[tuple[int, str, str]] = []
    for name in names:
        match = _RECORD_NAME_RE.fullmatch(name)
        if match is None:
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_INVALID")
        result.append((int(match.group(1)), match.group(2), name))
    if len(result) > _MAX_RECORDS:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_INVALID")
    return sorted(result)


def _ensure_binding(storage: _Storage, *, facts: _Facts) -> None:
    expected = _state_binding_payload(facts=facts)
    if _exists_at(
        storage.root_fd,
        _BINDING_FILENAME,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_BINDING_INVALID",
    ):
        if _read_file_at(
            storage.root_fd,
            _BINDING_FILENAME,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_BINDING_INVALID",
        ) != expected:
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_BINDING_MISMATCH")
        return
    _write_create_only_at(
        storage.root_fd,
        _BINDING_FILENAME,
        expected,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_BINDING_WRITE_FAILED",
    )


@contextmanager
def _locked_storage(*, facts: _Facts) -> Iterator[_Storage]:
    root_fd = -1
    records_fd = -1
    lock_fd = -1
    try:
        root_fd = _ensure_directory(
            FIXED_PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT,
            mode=0o700,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_ROOT_UNSAFE",
        )
        lock_fd = os.open(
            _LOCK_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        _safe_metadata(
            lock_fd,
            directory=False,
            mode=0o600,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_LOCK_UNSAFE",
        )
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        records_fd = _ensure_child_directory(
            root_fd,
            name=_RECORDS_DIRECTORY,
            mode=0o700,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORDS_UNSAFE",
        )
        storage = _Storage(root_fd=root_fd, records_fd=records_fd)
        _ensure_binding(storage, facts=facts)
        yield storage
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATE_LOCK_FAILED"
        ) from exc
    finally:
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if records_fd >= 0:
            try:
                os.close(records_fd)
            except OSError:
                pass
        if root_fd >= 0:
            try:
                os.close(root_fd)
            except OSError:
                pass


def _load_state(storage: _Storage, *, facts: _Facts) -> tuple[_Record, ...]:
    _ensure_binding(storage, facts=facts)
    records: list[_Record] = []
    expected_sequence = 1
    previous = _ZERO_SHA256
    for sequence, identity, name in _record_names(storage.records_fd):
        payload = _read_file_at(
            storage.records_fd,
            name,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_INVALID",
        )
        record = _record_from_payload(
            payload,
            facts=facts,
            expected_sequence=expected_sequence,
            expected_previous_record_sha256=previous,
        )
        if (
            sequence != record.sequence
            or identity != record.reservation_identity_sha256
        ):
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_CHAIN_INVALID")
        records.append(record)
        expected_sequence += 1
        previous = record.record_sha256
    current_exists = _exists_at(
        storage.root_fd,
        _CURRENT_FILENAME,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_INVALID",
    )
    if not records:
        if current_exists:
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_ROLLBACK")
        return ()
    # Unlike the anchor ledger, a missing pointer is not recoverable here.
    # The record is a pre-operation reservation, so recreating an inferred
    # pointer could convert a crash boundary into a fresh usable reservation.
    if not current_exists:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_INDETERMINATE")
    current = _current_from_payload(
        _read_file_at(
            storage.root_fd,
            _CURRENT_FILENAME,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_INVALID",
        )
    )
    final = records[-1]
    if (
        current.sequence != final.sequence
        or current.reservation_identity_sha256 != final.reservation_identity_sha256
        or current.record_sha256 != final.record_sha256
    ):
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CURRENT_ROLLBACK")
    return tuple(records)


def _checkpoint(
    checkpoint: object,
    *,
    facts: _Facts,
    record: _Record | None,
) -> None:
    callback = getattr(
        checkpoint,
        "attest_v4_p2_p4_p7_pre_operation_reservation_state",
        None,
    )
    if not callable(callback):
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CHECKPOINT_MISSING")
    sequence = 0 if record is None else record.sequence
    previous = _ZERO_SHA256 if record is None else record.previous_record_sha256
    digest = _ZERO_SHA256 if record is None else record.record_sha256
    try:
        callback(
            witness_reservation_scope_sha256=facts.scope_sha256,
            sequence=sequence,
            previous_record_sha256=previous,
            record_sha256=digest,
        )
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CHECKPOINT_FAILED"
        ) from exc


def _linearize_pre_effect_claim(
    linearizer: object,
    *,
    pins: _Pins,
) -> str:
    """Require the future atomic journal/driver bridge; never emulate it.

    The bridge is the authoritative cross-store safety seam.  It must durably
    make the entire fixed reservation scope indeterminate before returning,
    including if the local RESERVED record later fails to persist.  It may
    additionally record the exact claim correlation, but must not permit a
    different claim or successor intent to evade the scope fence.  The local
    registry records only the returned immutable correlation digest; it cannot
    prove or reconstruct the bridge's journal-side transaction on its own.
    """

    callback = getattr(
        linearizer,
        "linearize_v4_p2_p4_p7_before_effect_start",
        None,
    )
    if not callable(callback):
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PRE_EFFECT_LINEARIZER_MISSING")
    mapping = pins.mapping
    try:
        result = callback(
            witness_reservation_scope_sha256=mapping["witness_reservation_scope_sha256"],
            run_id=UUID(mapping["run_id"]),  # type: ignore[arg-type]
            plan_sha256=mapping["plan_sha256"],
            phase_sequence=mapping["phase_sequence"],
            phase=mapping["phase"],
            phase_request_sha256=mapping["phase_request_sha256"],
            effect_key=mapping["effect_key"],
            claim_id=mapping["claim_id"],
            reservation_identity_sha256=pins.identity_sha256,
        )
    except PhysicalFullMatrixV4P2P4P7PreOperationReservationError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
            "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PRE_EFFECT_LINEARIZATION_FAILED"
        ) from exc
    return _sha256(
        result,
        code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PRE_EFFECT_LINEARIZATION_INVALID",
    )


def _make_receipt(
    *,
    registry: "PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry",
    facts: _Facts,
    pins: _Pins,
    record: _Record,
) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt:
    value = PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt(
        reservation_identity_sha256=record.reservation_identity_sha256,
        reservation_id=record.reservation_id,
        reservation_record_sha256=record.record_sha256,
        phase_sequence=pins.phase_sequence,
        phase=pins.phase,
        effect_key=pins.effect_key,
        expires_at=record.expires_at,
        capability=_CAPABILITY_CONSTRUCTION_TOKEN,
    )
    _RECEIPT_STATES[value] = _LiveReceiptState(
        registry=registry,
        scope_sha256=facts.scope_sha256,
        pins=pins,
        record=record,
    )
    return value


def _receipt_matches(
    value: PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt,
    *,
    state: _LiveReceiptState,
) -> bool:
    record = state.record
    pins = state.pins
    return (
        value.schema == PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        and value.status == _RECEIPT_STATUS
        and value.reservation_identity_sha256 == record.reservation_identity_sha256
        and value.reservation_id == record.reservation_id
        and value.reservation_record_sha256 == record.record_sha256
        and value.phase_sequence == pins.phase_sequence
        and value.phase == pins.phase
        and value.effect_key == pins.effect_key
        and value.expires_at == record.expires_at
        and value.writer_authorized is False
        and value.promotion_authorized is False
        and value.traffic_switch_authorized is False
        and value.external_effect_authorized is False
        and value.execution_authorized is False
        and value.full_matrix_authorized is False
        and value._capability is _CAPABILITY_CONSTRUCTION_TOKEN
    )


def _make_capability(
    *,
    registry: "PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry",
    receipt: PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt,
    receipt_state: _LiveReceiptState,
    effect_start_identity_sha256: str,
    anchor: dict[str, object],
) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability:
    record = receipt_state.record
    pins = receipt_state.pins
    value = PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability(
        reservation_identity_sha256=record.reservation_identity_sha256,
        reservation_id=record.reservation_id,
        reservation_record_sha256=record.record_sha256,
        phase_sequence=pins.phase_sequence,
        phase=pins.phase,
        effect_key=pins.effect_key,
        journaled_effect_start_identity_sha256=effect_start_identity_sha256,
        effect_start_anchor_head_sha256=anchor["anchor_head_sha256"],  # type: ignore[arg-type]
        expires_at=record.expires_at,
        capability=_CAPABILITY_CONSTRUCTION_TOKEN,
    )
    _CAPABILITY_STATES[value] = _LiveCapabilityState(
        registry=registry,
        receipt=receipt,
        scope_sha256=receipt_state.scope_sha256,
        pins=pins,
        record=record,
        effect_start_identity_sha256=effect_start_identity_sha256,
        anchor=anchor,
    )
    return value


def _capability_matches(
    value: PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability,
    *,
    state: _LiveCapabilityState,
) -> bool:
    record = state.record
    pins = state.pins
    return (
        value.schema == PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_SCHEMA
        and value.status == PHYSICAL_FULL_MATRIX_V4_P2_P4_P7_PRE_OPERATION_RESERVATION_STATUS
        and value.reservation_identity_sha256 == record.reservation_identity_sha256
        and value.reservation_id == record.reservation_id
        and value.reservation_record_sha256 == record.record_sha256
        and value.phase_sequence == pins.phase_sequence
        and value.phase == pins.phase
        and value.effect_key == pins.effect_key
        and value.journaled_effect_start_identity_sha256
        == state.effect_start_identity_sha256
        and value.effect_start_anchor_head_sha256 == state.anchor["anchor_head_sha256"]
        and value.expires_at == record.expires_at
        and value.writer_authorized is False
        and value.promotion_authorized is False
        and value.traffic_switch_authorized is False
        and value.external_effect_authorized is False
        and value.execution_authorized is False
        and value.full_matrix_authorized is False
        and value._capability is _CAPABILITY_CONSTRUCTION_TOKEN
    )


class PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry:
    """Root/Witness-owned durable reservation state for one future executor.

    It intentionally has no consume, release, retry, transport, provider, or
    site-control operation.  A record that survives a process boundary is
    therefore indeterminate, rather than silently being turned back into a
    usable capability.
    """

    def __init__(
        self,
        config: PhysicalFullMatrixV4P2P4P7PreOperationReservationConfig,
        *,
        clock: PhysicalFullMatrixV4P2P4P7PreOperationReservationClock | None,
        rollback_checkpoint: PhysicalFullMatrixV4P2P4P7PreOperationReservationCheckpoint
        | None,
        pre_effect_linearizer: (
            PhysicalFullMatrixV4P2P4P7PreOperationReservationPreEffectLinearizer
            | None
        ),
    ) -> None:
        self._config = config
        self._clock = clock
        self._rollback_checkpoint = rollback_checkpoint
        self._pre_effect_linearizer = pre_effect_linearizer
        self._activation_lock = threading.RLock()
        self._clock_lock = threading.RLock()
        # If a bridge call may have crossed its durable boundary but local
        # persistence subsequently fails, this entire fixed reservation scope
        # is unknown.  Do not let a different claim or successor intent evade
        # that ambiguity in this process.  Across restart the required bridge
        # remains the authoritative fail-closed source; no local fallback
        # exists.
        self._linearization_lock = threading.RLock()
        self._locally_indeterminate_pre_effect_linearization = False
        self._clock_floor: datetime | None = None

    def _trusted_now(self) -> datetime:
        with self._clock_lock:
            value = _now(self._clock)
            if self._clock_floor is not None and value < self._clock_floor:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CLOCK_ROLLBACK")
            self._clock_floor = value
            return value

    def reserve_before_future_executor(
        self,
        *,
        claim: _driver.PhysicalFullMatrixV4PhaseClaim,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
        successor_intent: PhysicalFullMatrixV4P2P4P7SuccessorIntent,
        expires_at: datetime,
    ) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt:
        """Create, fsync, checkpoint, and read back one non-reusable RESERVED.

        This must be called after the driver has made a local claim but before
        its generic effect-start transition.  The returned receipt cannot
        cross an executor boundary.  If any persistence boundary is ambiguous,
        the durable residue blocks reissue after restart.
        """

        _require_root()
        facts = _facts(self._config)
        now = self._trusted_now()
        checked_expires = _utc(
            expires_at,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRY_INVALID",
        )
        if (
            checked_expires <= now
            or checked_expires - now
            > timedelta(seconds=facts.maximum_lifetime_seconds)
        ):
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRY_INVALID")
        pins = _pre_start_pins(
            claim=claim,
            request=request,
            successor_intent=successor_intent,
            facts=facts,
            now=now,
            code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PINS_INVALID",
        )
        with _locked_storage(facts=facts) as storage:
            records = _load_state(storage, facts=facts)
            _checkpoint(
                self._rollback_checkpoint,
                facts=facts,
                record=None if not records else records[-1],
            )
            if records:
                # No auto release/retry, even after expiry.  The future
                # reconciliation protocol must account for this record first.
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_OUTSTANDING_INDETERMINATE")
            # Re-read/revalidate the opaque V4 readiness immediately before
            # crossing the future bridge.  A reconstructed request or a stale
            # readiness capability cannot be carried from an earlier check.
            fresh_pins = _pre_start_pins(
                claim=claim,
                request=request,
                successor_intent=successor_intent,
                facts=facts,
                now=self._trusted_now(),
                code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PINS_INVALID",
            )
            if fresh_pins != pins:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_PINS_CHANGED")
            pins = fresh_pins
            with self._linearization_lock:
                if self._locally_indeterminate_pre_effect_linearization:
                    _fail(
                        "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_"
                        "PRE_EFFECT_LINEARIZATION_INDETERMINATE"
                    )
                # Set this before invoking the external bridge: an exception
                # after it durably linearizes is itself ambiguous, so a
                # generic local retry would be unsafe.
                self._locally_indeterminate_pre_effect_linearization = True
                linearization_sha256 = _linearize_pre_effect_claim(
                    self._pre_effect_linearizer,
                    pins=pins,
                )
            reserved_at = self._trusted_now()
            if reserved_at >= checked_expires:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRY_INVALID")
            try:
                reservation_id = secrets.token_bytes(32).hex()
            except Exception as exc:
                raise PhysicalFullMatrixV4P2P4P7PreOperationReservationError(
                    "V4_P2_P4_P7_PRE_OPERATION_RESERVATION_ID_GENERATION_FAILED"
                ) from exc
            payload, record_sha256 = _record_payload(
                facts=facts,
                sequence=1,
                previous_record_sha256=_ZERO_SHA256,
                reservation_identity_sha256=pins.identity_sha256,
                reservation_id=reservation_id,
                pre_effect_claim_linearization_sha256=linearization_sha256,
                reserved_at=reserved_at,
                expires_at=checked_expires,
                pins=pins.mapping,
            )
            name = f"{1:020d}-{pins.identity_sha256}.json"
            _write_create_only_at(
                storage.records_fd,
                name,
                payload,
                code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECORD_WRITE_FAILED",
            )
            record = _Record(
                sequence=1,
                previous_record_sha256=_ZERO_SHA256,
                reservation_identity_sha256=pins.identity_sha256,
                reservation_id=reservation_id,
                pre_effect_claim_linearization_sha256=linearization_sha256,
                reserved_at=reserved_at,
                expires_at=checked_expires,
                pins=pins.mapping,
                record_sha256=record_sha256,
            )
            _write_current_atomic(
                storage.root_fd,
                _current_payload(
                    current=_Current(
                        sequence=record.sequence,
                        reservation_identity_sha256=record.reservation_identity_sha256,
                        record_sha256=record.record_sha256,
                    )
                ),
            )
            # Readback must parse the exact on-disk bytes before any
            # in-process receipt exists.  It is intentionally still within
            # the lock.
            reread = _load_state(storage, facts=facts)
            if len(reread) != 1 or reread[0] != record:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_READBACK_INVALID")
            _checkpoint(self._rollback_checkpoint, facts=facts, record=record)
            if self._trusted_now() >= record.expires_at:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRED_INDETERMINATE")
            return _make_receipt(
                registry=self,
                facts=facts,
                pins=pins,
                record=record,
            )

    def activate_after_effect_start(
        self,
        *,
        reservation: PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    ) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability:
        """Activate one live receipt only after its exact start anchor exists.

        Activation performs no durable transition.  That is deliberate:
        RESERVED remains the sole durable state until a later completion or
        reconciliation design exists.  A process restart therefore cannot
        recreate a usable executor capability from the record alone.
        """

        _require_root()
        facts = _facts(self._config)
        self._trusted_now()
        if (
            type(reservation)
            is not PhysicalFullMatrixV4P2P4P7PreOperationReservationReceipt
            or reservation._capability is not _CAPABILITY_CONSTRUCTION_TOKEN
        ):
            _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_REQUIRED")
        with self._activation_lock:
            receipt_state = _RECEIPT_STATES.get(reservation)
            if receipt_state is None or receipt_state.registry is not self:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_INDETERMINATE")
            if receipt_state.activated:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_ALREADY_ACTIVATED")
            if not _receipt_matches(reservation, state=receipt_state):
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_TAMPERED")
            if receipt_state.scope_sha256 != facts.scope_sha256:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_PINS_MISMATCH")
            effect_identity, anchor = _activation_pins(
                request=request,
                reservation_pins=receipt_state.pins,
                facts=facts,
                code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_ACTIVATION_PINS_INVALID",
            )
            with _locked_storage(facts=facts) as storage:
                records = _load_state(storage, facts=facts)
                if len(records) != 1 or records[0] != receipt_state.record:
                    _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_RECEIPT_INDETERMINATE")
                _checkpoint(self._rollback_checkpoint, facts=facts, record=records[0])
                if self._trusted_now() >= records[0].expires_at:
                    _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRED_INDETERMINATE")
            # From this point forward a second post-start capability is
            # forbidden even if a caller presents a different anchor object.
            receipt_state.activated = True
            return _make_capability(
                registry=self,
                receipt=reservation,
                receipt_state=receipt_state,
                effect_start_identity_sha256=effect_identity,
                anchor=anchor,
            )

    def require_live_capability(
        self,
        *,
        capability: PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    ) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability:
        """Consume one in-process capability at a future executor boundary.

        The method is intentionally one-shot.  A future owner must call it
        immediately before its one executor callback; an exception after this
        method returns leaves the capability consumed rather than allowing a
        second ambiguous external attempt.
        """

        _require_root()
        facts = _facts(self._config)
        self._trusted_now()
        with self._activation_lock:
            state = _CAPABILITY_STATES.get(capability) if type(
                capability
            ) is PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability else None
            if state is None:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_INDETERMINATE")
            if state.consumed:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_ALREADY_CONSUMED")
            effect_identity, anchor = _activation_pins(
                request=request,
                reservation_pins=state.pins,
                facts=facts,
                code="V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_PINS_MISMATCH",
            )
            if (
                type(capability)
                is not PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability
                or capability._capability is not _CAPABILITY_CONSTRUCTION_TOKEN
            ):
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_REQUIRED")
            if state.registry is not self:
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_INDETERMINATE")
            if not _capability_matches(capability, state=state):
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_TAMPERED")
            if (
                state.scope_sha256 != facts.scope_sha256
                or effect_identity != state.effect_start_identity_sha256
                or anchor != state.anchor
            ):
                _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_PINS_MISMATCH")
            with _locked_storage(facts=facts) as storage:
                records = _load_state(storage, facts=facts)
                if len(records) != 1 or records[0] != state.record:
                    _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_CAPABILITY_INDETERMINATE")
                _checkpoint(self._rollback_checkpoint, facts=facts, record=records[0])
                if self._trusted_now() >= records[0].expires_at:
                    _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_EXPIRED_INDETERMINATE")
            state.consumed = True
            return capability


def require_physical_full_matrix_v4_p2_p4_p7_pre_operation_reservation(
    *,
    registry: object,
    capability: object,
    request: object,
) -> PhysicalFullMatrixV4P2P4P7PreOperationReservationCapability:
    """Consume a fresh in-process RESERVED correlation at a future owner seam."""

    if type(registry) is not PhysicalFullMatrixV4P2P4P7PreOperationReservationRegistry:
        _fail("V4_P2_P4_P7_PRE_OPERATION_RESERVATION_REGISTRY_INVALID")
    return registry.require_live_capability(
        capability=capability,  # type: ignore[arg-type]
        request=request,  # type: ignore[arg-type]
    )
