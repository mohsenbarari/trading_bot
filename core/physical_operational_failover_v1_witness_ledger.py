"""Default-off Witness-owned authority ledger for operational failover V1.

This is deliberately a narrow *foundation*, not a runtime.  It has no
provider, network, database, process, traffic, or writer-start code.  A
root-owned durable append/CAS adapter is injected by a later deployment.  The
adapter is the only component allowed to persist state; this module rejects
ambiguous or stale reads rather than attempting recovery itself.

The only transition implemented here is the emergency direction:

    FI active -> FI fenced/expired -> IR grant pending -> IR grant issued
              -> IR active (only after a verified completion)

Every non-active state has no writer.  In particular, a signed grant alone is
not an active term and cannot authorize traffic, a database, or an external
effect.  A future writer-admission adapter must freshly read the exact
Witness-held active state before authorizing any such operation.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import LEASE_ID_RE
from core import physical_operational_failover_v1 as evidence


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA",
    "PhysicalOperationalFailoverV1WitnessGrantIssuer",
    "PhysicalOperationalFailoverV1WitnessGrantReservation",
    "PhysicalOperationalFailoverV1WitnessLedgerClock",
    "PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore",
    "PhysicalOperationalFailoverV1WitnessLedgerEntry",
    "PhysicalOperationalFailoverV1WitnessLedgerError",
    "PhysicalOperationalFailoverV1WitnessLedgerSnapshot",
    "PhysicalOperationalFailoverV1WitnessLedgerState",
    "RootOwnedPhysicalOperationalFailoverV1WitnessLedger",
    "RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-term-ledger-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DEFAULT_ENABLED = False

_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_EVENT_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$", re.ASCII)
_PHASE_FI_ACTIVE = "fi-active"
_PHASE_FI_FENCED = "fi-fenced"
_PHASE_FI_EXPIRED = "fi-expired"
_PHASE_IR_GRANT_PENDING = "ir-grant-pending"
_PHASE_IR_GRANT_ISSUED = "ir-grant-issued"
_PHASE_IR_ACTIVE = "ir-active"
_PHASES = frozenset(
    {
        _PHASE_FI_ACTIVE,
        _PHASE_FI_FENCED,
        _PHASE_FI_EXPIRED,
        _PHASE_IR_GRANT_PENDING,
        _PHASE_IR_GRANT_ISSUED,
        _PHASE_IR_ACTIVE,
    }
)
_MAX_CLOCK_STEP_SECONDS = 300
_MAX_REPLAY_ITEMS = 4096


class PhysicalOperationalFailoverV1WitnessLedgerError(RuntimeError):
    """The root-owned term ledger rejected an unsafe transition."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WitnessLedgerError(code)


class PhysicalOperationalFailoverV1WitnessLedgerClock(Protocol):
    """Trusted Witness time source; no caller supplies a transition time."""

    def now_utc(self) -> datetime: ...


class PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore(Protocol):
    """Narrow root-owned append/CAS persistence boundary.

    Implementations must make a successful append durable before returning
    ``True`` and must never replace or edit an existing entry.  This protocol
    deliberately contains no generic object, peer, database, or transport API.
    """

    def read_current(self) -> "PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None": ...

    def append_compare_and_swap(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        entry: "PhysicalOperationalFailoverV1WitnessLedgerEntry",
        next_state: "PhysicalOperationalFailoverV1WitnessLedgerState",
    ) -> bool: ...


class PhysicalOperationalFailoverV1WitnessGrantIssuer(Protocol):
    """Narrow root signer seam; it returns canonical signed V1 grant bytes."""

    def issue_witness_promotion_grant(
        self,
        *,
        value: evidence.PhysicalOperationalFailoverV1WitnessPromotionGrantInput,
        verification_config: evidence.PhysicalOperationalFailoverV1VerificationConfig,
        now: datetime,
        expected_request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessGrantReservation:
    """Exactly one future IR grant, durably reserved while FI is inactive."""

    grant_id: str = ""
    grant_nonce: str = ""
    grant_replay_key_sha256: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    successor_term: evidence.PhysicalOperationalFailoverV1Term | None = None
    activation_route_artifact_sha256: str = ""
    activation_receiver_permit_sha256: str = ""


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessLedgerState:
    """The committed authority projection; at most one ``active_term`` exists."""

    sequence: int
    phase: str
    clock_floor: datetime
    active_term: evidence.PhysicalOperationalFailoverV1Term | None = None
    active_term_sha256: str | None = None
    predecessor_term: evidence.PhysicalOperationalFailoverV1Term | None = None
    predecessor_term_sha256: str | None = None
    predecessor_termination_reason: str | None = None
    fi_self_fence_receipt_sha256: str | None = None
    request_sha256: str | None = None
    request_id: str | None = None
    request_nonce: str | None = None
    canonical_request: bytes | None = None
    reservation: PhysicalOperationalFailoverV1WitnessGrantReservation | None = None
    issued_grant_sha256: str | None = None
    issued_grant_id: str | None = None
    issued_grant_nonce: str | None = None
    completion_sha256: str | None = None
    consumed_replay_keys: tuple[str, ...] = ()
    consumed_nonces: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessLedgerEntry:
    """One immutable append record; ``entry_sha256`` commits its next state."""

    sequence: int
    previous_head_sha256: str
    observed_at: datetime
    event: str
    state_sha256: str
    entry_sha256: str


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    """One exact CAS version and head returned by the durable store."""

    version: int
    head_sha256: str
    entry: PhysicalOperationalFailoverV1WitnessLedgerEntry
    state: PhysicalOperationalFailoverV1WitnessLedgerState


@dataclass(frozen=True)
class RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig:
    """Default-off policy for exactly one Witness-owned emergency direction."""

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_DEFAULT_ENABLED
    verification_config: evidence.PhysicalOperationalFailoverV1VerificationConfig | None = None
    initial_fi_term: evidence.PhysicalOperationalFailoverV1Term | None = None


@dataclass(frozen=True)
class _Facts:
    verification_config: evidence.PhysicalOperationalFailoverV1VerificationConfig
    initial_fi_term: evidence.PhysicalOperationalFailoverV1Term
    initial_fi_term_sha256: str


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(code) from exc


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond:
        _fail(code)
    return result


def _render_time(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(value: object, *, code: str, allow_zero: bool = False) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    if not allow_zero and value == _ZERO_SHA256:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _writer_lease_id(value: object, *, code: str) -> str:
    """Keep persisted V1 terms on the shared writer-lease grammar."""

    if type(value) is not str or LEASE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _nonce(value: object, *, code: str) -> str:
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _term_mapping(value: object, *, code: str) -> tuple[evidence.PhysicalOperationalFailoverV1Term, dict[str, object]]:
    if type(value) is not evidence.PhysicalOperationalFailoverV1Term:
        _fail(code)
    if value.holder_site not in {"webapp_fi", "webapp_ir"}:
        _fail(code)
    if type(value.writer_epoch) is not int or isinstance(value.writer_epoch, bool) or value.writer_epoch < 1:
        _fail(code)
    issued = _utc(value.issued_at, code=code)
    expires = _utc(value.expires_at, code=code)
    if expires <= issued or expires - issued > timedelta(seconds=300):
        _fail(code)
    normalized = evidence.PhysicalOperationalFailoverV1Term(
        holder_site=value.holder_site,
        writer_epoch=value.writer_epoch,
        writer_lease_id=_writer_lease_id(value.writer_lease_id, code=code),
        witness_transition_id=_identifier(value.witness_transition_id, code=code),
        witnessed_term_proof_sha256=_sha(value.witnessed_term_proof_sha256, code=code),
        issued_at=issued,
        expires_at=expires,
    )
    return normalized, {
        "holder_site": normalized.holder_site,
        "writer_epoch": normalized.writer_epoch,
        "writer_lease_id": normalized.writer_lease_id,
        "witness_transition_id": normalized.witness_transition_id,
        "witnessed_term_proof_sha256": normalized.witnessed_term_proof_sha256,
        "issued_at": _render_time(issued, code=code),
        "expires_at": _render_time(expires, code=code),
    }


def _term_sha256(value: object, *, code: str) -> str:
    _term, mapping = _term_mapping(value, code=code)
    return hashlib.sha256(_canonical(mapping, code=code)).hexdigest()


def _reservation_mapping(value: object, *, code: str) -> tuple[PhysicalOperationalFailoverV1WitnessGrantReservation, dict[str, object]]:
    if type(value) is not PhysicalOperationalFailoverV1WitnessGrantReservation:
        _fail(code)
    issued = _utc(value.issued_at, code=code)
    expires = _utc(value.expires_at, code=code)
    if expires <= issued or expires - issued > timedelta(seconds=300):
        _fail(code)
    successor, successor_mapping = _term_mapping(value.successor_term, code=code)
    if (
        successor.holder_site != "webapp_ir"
        or successor.issued_at != issued
        or successor.expires_at != expires
    ):
        _fail(code)
    normalized = PhysicalOperationalFailoverV1WitnessGrantReservation(
        grant_id=_identifier(value.grant_id, code=code),
        grant_nonce=_nonce(value.grant_nonce, code=code),
        grant_replay_key_sha256=_sha(value.grant_replay_key_sha256, code=code),
        issued_at=issued,
        expires_at=expires,
        successor_term=successor,
        activation_route_artifact_sha256=_sha(value.activation_route_artifact_sha256, code=code),
        activation_receiver_permit_sha256=_sha(value.activation_receiver_permit_sha256, code=code),
    )
    return normalized, {
        "grant_id": normalized.grant_id,
        "grant_nonce": normalized.grant_nonce,
        "grant_replay_key_sha256": normalized.grant_replay_key_sha256,
        "issued_at": _render_time(issued, code=code),
        "expires_at": _render_time(expires, code=code),
        "successor_term": successor_mapping,
        "activation_route_artifact_sha256": normalized.activation_route_artifact_sha256,
        "activation_receiver_permit_sha256": normalized.activation_receiver_permit_sha256,
    }


def _state_mapping(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not PhysicalOperationalFailoverV1WitnessLedgerState:
        _fail(code)
    if type(value.sequence) is not int or value.sequence < 1 or value.sequence > 2**63 - 1:
        _fail(code)
    if value.phase not in _PHASES:
        _fail(code)
    floor = _utc(value.clock_floor, code=code)
    if type(value.consumed_replay_keys) is not tuple or type(value.consumed_nonces) is not tuple:
        _fail(code)
    if len(value.consumed_replay_keys) > _MAX_REPLAY_ITEMS or len(value.consumed_nonces) > _MAX_REPLAY_ITEMS:
        _fail(code)
    replay = tuple(_identifier(item, code=code) for item in value.consumed_replay_keys)
    nonces = tuple(_identifier(item, code=code) for item in value.consumed_nonces)
    if tuple(sorted(set(replay))) != replay or tuple(sorted(set(nonces))) != nonces:
        _fail(code)

    active: dict[str, object] | None = None
    predecessor: dict[str, object] | None = None
    reservation: dict[str, object] | None = None
    if value.active_term is not None:
        normalized_active, active = _term_mapping(value.active_term, code=code)
        if value.active_term_sha256 != _term_sha256(normalized_active, code=code):
            _fail(code)
    elif value.active_term_sha256 is not None:
        _fail(code)
    if value.predecessor_term is not None:
        normalized_predecessor, predecessor = _term_mapping(value.predecessor_term, code=code)
        if value.predecessor_term_sha256 != _term_sha256(normalized_predecessor, code=code):
            _fail(code)
    elif value.predecessor_term_sha256 is not None:
        _fail(code)
    if value.reservation is not None:
        _reservation, reservation = _reservation_mapping(value.reservation, code=code)

    for field in (
        value.predecessor_termination_reason,
        value.fi_self_fence_receipt_sha256,
        value.request_sha256,
        value.request_id,
        value.request_nonce,
        value.issued_grant_sha256,
        value.issued_grant_id,
        value.issued_grant_nonce,
        value.completion_sha256,
    ):
        if field is not None and type(field) is not str:
            _fail(code)
    if value.predecessor_termination_reason not in {None, "fi-self-fence-receipt", "predecessor-term-expired"}:
        _fail(code)
    if value.fi_self_fence_receipt_sha256 is not None:
        _sha(value.fi_self_fence_receipt_sha256, code=code)
    for item in (value.request_sha256, value.issued_grant_sha256, value.completion_sha256):
        if item is not None:
            _sha(item, code=code)
    for item in (value.request_id, value.issued_grant_id):
        if item is not None:
            _identifier(item, code=code)
    for item in (value.request_nonce, value.issued_grant_nonce):
        if item is not None:
            _nonce(item, code=code)
    if value.canonical_request is not None:
        if type(value.canonical_request) is not bytes or not value.canonical_request or len(value.canonical_request) > 128 * 1024:
            _fail(code)
        canonical_request = base64.b64encode(value.canonical_request).decode("ascii")
    else:
        canonical_request = None

    if value.phase == _PHASE_FI_ACTIVE:
        if (
            value.active_term is None
            or value.active_term.holder_site != "webapp_fi"
            or any(
                item is not None
                for item in (
                    value.predecessor_term,
                    value.predecessor_termination_reason,
                    value.fi_self_fence_receipt_sha256,
                    value.request_sha256,
                    value.request_id,
                    value.request_nonce,
                    value.canonical_request,
                    value.reservation,
                    value.issued_grant_sha256,
                    value.issued_grant_id,
                    value.issued_grant_nonce,
                    value.completion_sha256,
                )
            )
        ):
            _fail(code)
    elif value.phase in {_PHASE_FI_FENCED, _PHASE_FI_EXPIRED}:
        expected_reason = "fi-self-fence-receipt" if value.phase == _PHASE_FI_FENCED else "predecessor-term-expired"
        if (
            value.active_term is not None
            or value.predecessor_term is None
            or value.predecessor_term.holder_site != "webapp_fi"
            or value.predecessor_termination_reason != expected_reason
            or (expected_reason == "fi-self-fence-receipt") != (value.fi_self_fence_receipt_sha256 is not None)
            or value.request_sha256 is None
            or value.request_id is None
            or value.request_nonce is None
            or value.canonical_request is None
            or any(
                item is not None
                for item in (
                    value.reservation,
                    value.issued_grant_sha256,
                    value.issued_grant_id,
                    value.issued_grant_nonce,
                    value.completion_sha256,
                )
            )
        ):
            _fail(code)
    elif value.phase == _PHASE_IR_GRANT_PENDING:
        if (
            value.active_term is not None
            or value.predecessor_term is None
            or value.predecessor_term.holder_site != "webapp_fi"
            or value.predecessor_termination_reason not in {"fi-self-fence-receipt", "predecessor-term-expired"}
            or (value.predecessor_termination_reason == "fi-self-fence-receipt") != (value.fi_self_fence_receipt_sha256 is not None)
            or value.request_sha256 is None
            or value.request_id is None
            or value.request_nonce is None
            or value.canonical_request is None
            or value.reservation is None
            or any(item is not None for item in (value.issued_grant_sha256, value.issued_grant_id, value.issued_grant_nonce, value.completion_sha256))
        ):
            _fail(code)
    elif value.phase == _PHASE_IR_GRANT_ISSUED:
        if (
            value.active_term is not None
            or value.predecessor_term is None
            or value.predecessor_term.holder_site != "webapp_fi"
            or value.predecessor_termination_reason not in {"fi-self-fence-receipt", "predecessor-term-expired"}
            or (value.predecessor_termination_reason == "fi-self-fence-receipt") != (value.fi_self_fence_receipt_sha256 is not None)
            or value.request_sha256 is None
            or value.request_id is None
            or value.request_nonce is None
            or value.canonical_request is None
            or value.reservation is None
            or value.issued_grant_sha256 is None
            or value.issued_grant_id != value.reservation.grant_id
            or value.issued_grant_nonce != value.reservation.grant_nonce
            or value.completion_sha256 is not None
        ):
            _fail(code)
    else:  # IR active
        if value.reservation is None:
            _fail(code)
        if (
            value.active_term is None
            or value.active_term.holder_site != "webapp_ir"
            or value.predecessor_term is None
            or value.predecessor_term.holder_site != "webapp_fi"
            or value.active_term != value.reservation.successor_term
            or value.active_term.writer_epoch <= value.predecessor_term.writer_epoch
            or value.predecessor_termination_reason not in {"fi-self-fence-receipt", "predecessor-term-expired"}
            or (value.predecessor_termination_reason == "fi-self-fence-receipt") != (value.fi_self_fence_receipt_sha256 is not None)
            or value.request_sha256 is None
            or value.request_id is None
            or value.request_nonce is None
            or value.canonical_request is None
            or value.issued_grant_sha256 is None
            or value.issued_grant_id != value.reservation.grant_id
            or value.issued_grant_nonce != value.reservation.grant_nonce
            or value.completion_sha256 is None
        ):
            _fail(code)

    return {
        "sequence": value.sequence,
        "phase": value.phase,
        "clock_floor": _render_time(floor, code=code),
        "active_term": active,
        "active_term_sha256": value.active_term_sha256,
        "predecessor_term": predecessor,
        "predecessor_term_sha256": value.predecessor_term_sha256,
        "predecessor_termination_reason": value.predecessor_termination_reason,
        "fi_self_fence_receipt_sha256": value.fi_self_fence_receipt_sha256,
        "request_sha256": value.request_sha256,
        "request_id": value.request_id,
        "request_nonce": value.request_nonce,
        "canonical_request_base64": canonical_request,
        "reservation": reservation,
        "issued_grant_sha256": value.issued_grant_sha256,
        "issued_grant_id": value.issued_grant_id,
        "issued_grant_nonce": value.issued_grant_nonce,
        "completion_sha256": value.completion_sha256,
        "consumed_replay_keys": list(replay),
        "consumed_nonces": list(nonces),
    }


def _state_sha256(value: PhysicalOperationalFailoverV1WitnessLedgerState, *, code: str) -> str:
    return hashlib.sha256(_canonical(_state_mapping(value, code=code), code=code)).hexdigest()


def _entry_mapping(value: object, *, code: str) -> dict[str, object]:
    if type(value) is not PhysicalOperationalFailoverV1WitnessLedgerEntry:
        _fail(code)
    if type(value.sequence) is not int or value.sequence < 1 or value.sequence > 2**63 - 1:
        _fail(code)
    return {
        "sequence": value.sequence,
        "previous_head_sha256": _sha(value.previous_head_sha256, code=code, allow_zero=True),
        "observed_at": _render_time(_utc(value.observed_at, code=code), code=code),
        "event": _event(value.event, code=code),
        "state_sha256": _sha(value.state_sha256, code=code),
    }


def _entry_sha256(
    *,
    sequence: int,
    previous_head_sha256: str,
    observed_at: datetime,
    event: str,
    state_sha256: str,
) -> str:
    _event(event, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ENTRY_INVALID")
    return hashlib.sha256(
        _canonical(
            {
                "sequence": sequence,
                "previous_head_sha256": previous_head_sha256,
                "observed_at": _render_time(observed_at, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ENTRY_INVALID"),
                "event": event,
                "state_sha256": state_sha256,
            },
            code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ENTRY_INVALID",
        )
    ).hexdigest()


def _event(value: object, *, code: str) -> str:
    if type(value) is not str or _EVENT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _make_entry(
    *,
    sequence: int,
    previous_head_sha256: str,
    observed_at: datetime,
    event: str,
    state: PhysicalOperationalFailoverV1WitnessLedgerState,
) -> PhysicalOperationalFailoverV1WitnessLedgerEntry:
    state_sha = _state_sha256(state, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STATE_INVALID")
    entry_sha = _entry_sha256(
        sequence=sequence,
        previous_head_sha256=previous_head_sha256,
        observed_at=observed_at,
        event=event,
        state_sha256=state_sha,
    )
    return PhysicalOperationalFailoverV1WitnessLedgerEntry(
        sequence=sequence,
        previous_head_sha256=previous_head_sha256,
        observed_at=observed_at,
        event=event,
        state_sha256=state_sha,
        entry_sha256=entry_sha,
    )


def _require_root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _facts(value: object) -> _Facts:
    if type(value) is not RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SCHEMA
        or value.enabled is not True
        or type(value.verification_config) is not evidence.PhysicalOperationalFailoverV1VerificationConfig
        or value.verification_config.enabled is not True
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    verification = value.verification_config
    try:
        for item in (
            verification.fi_self_fence_signer_public_key,
            verification.ir_promotion_request_signer_public_key,
            verification.witness_term_signer_public_key,
            verification.ir_promotion_completion_signer_public_key,
        ):
            Ed25519PublicKey.from_public_bytes(item)
    except ValueError as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID"
        ) from exc
    term, _mapping = _term_mapping(value.initial_fi_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    if term.holder_site != "webapp_fi" or term.writer_epoch < 1:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    if value.verification_config.pins is None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    if (
        type(verification.maximum_evidence_age_seconds) is not int
        or isinstance(verification.maximum_evidence_age_seconds, bool)
        or not 1 <= verification.maximum_evidence_age_seconds <= 300
        or type(verification.fi_self_fence_signer_public_key) is not bytes
        or type(verification.ir_promotion_request_signer_public_key) is not bytes
        or type(verification.witness_term_signer_public_key) is not bytes
        or type(verification.ir_promotion_completion_signer_public_key) is not bytes
        or any(
            len(item) != 32
            for item in (
                verification.fi_self_fence_signer_public_key,
                verification.ir_promotion_request_signer_public_key,
                verification.witness_term_signer_public_key,
                verification.ir_promotion_completion_signer_public_key,
            )
        )
        or len(
            {
                verification.fi_self_fence_signer_public_key,
                verification.ir_promotion_request_signer_public_key,
                verification.witness_term_signer_public_key,
                verification.ir_promotion_completion_signer_public_key,
            }
        )
        != 4
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID")
    return _Facts(
        verification_config=value.verification_config,
        initial_fi_term=term,
        initial_fi_term_sha256=_term_sha256(term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CONFIG_INVALID"),
    )


def _trusted_now(
    clock: object,
    *,
    floor: datetime | None,
) -> datetime:
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_MISSING")
    try:
        result = _utc(callback(), code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_INVALID")
    except PhysicalOperationalFailoverV1WitnessLedgerError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_FAILED"
        ) from exc
    if floor is not None and result < floor:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_REGRESSION")
    if floor is not None and result - floor > timedelta(seconds=_MAX_CLOCK_STEP_SECONDS):
        # A large forward jump makes evidence freshness unknowable for this
        # foundation.  A future trusted monotonic runtime may rotate safely.
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_STEP_UNSAFE")
    return result


def _snapshot(value: object) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    if type(value) is not PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    if type(value.version) is not int or value.version < 1 or value.version > 2**63 - 1:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    if _sha(value.head_sha256, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID") != value.head_sha256:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    entry_mapping = _entry_mapping(value.entry, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    expected_entry_sha = _entry_sha256(
        sequence=value.entry.sequence,
        previous_head_sha256=value.entry.previous_head_sha256,
        observed_at=value.entry.observed_at,
        event=value.entry.event,
        state_sha256=value.entry.state_sha256,
    )
    if (
        value.entry.entry_sha256 != expected_entry_sha
        or value.head_sha256 != expected_entry_sha
        or value.entry.sequence != value.version
        or value.state.sequence != value.version
        or value.entry.state_sha256 != _state_sha256(value.state, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_SNAPSHOT_INVALID")
    del entry_mapping
    return value


def _read_current(store: object) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None:
    callback = getattr(store, "read_current", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_MISSING")
    try:
        value = callback()
    except PhysicalOperationalFailoverV1WitnessLedgerError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_READ_FAILED"
        ) from exc
    if value is None:
        return None
    return _snapshot(value)


def _expect_head(snapshot: PhysicalOperationalFailoverV1WitnessLedgerSnapshot, *, version: object, head_sha256: object) -> None:
    if type(version) is not int or isinstance(version, bool) or type(head_sha256) is not str:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_EXPECTED_HEAD_INVALID")
    if version != snapshot.version or head_sha256 != snapshot.head_sha256:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STALE_HEAD")


def _append(
    store: object,
    *,
    current: PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None,
    entry: PhysicalOperationalFailoverV1WitnessLedgerEntry,
    next_state: PhysicalOperationalFailoverV1WitnessLedgerState,
) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    expected_version = 0 if current is None else current.version
    expected_head = _ZERO_SHA256 if current is None else current.head_sha256
    if (
        entry.sequence != expected_version + 1
        or entry.previous_head_sha256 != expected_head
        or next_state.sequence != entry.sequence
        or entry.state_sha256 != _state_sha256(next_state, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STATE_INVALID")
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_APPEND_INVALID")
    callback = getattr(store, "append_compare_and_swap", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_MISSING")
    try:
        accepted = callback(
            expected_version=expected_version,
            expected_head_sha256=expected_head,
            entry=entry,
            next_state=next_state,
        )
    except PhysicalOperationalFailoverV1WitnessLedgerError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_APPEND_FAILED"
        ) from exc
    if accepted is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CAS_CONFLICT")
    readback = _read_current(store)
    if (
        readback is None
        or readback.version != entry.sequence
        or readback.head_sha256 != entry.entry_sha256
        or readback.entry != entry
        or readback.state != next_state
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CAS_READBACK_INVALID")
    return readback


def _token(kind: str, value: str) -> str:
    # The prefix makes equal hashes from different evidence grammars distinct.
    return kind + ":" + value


def _consumed(
    state: PhysicalOperationalFailoverV1WitnessLedgerState,
    *,
    replay_tokens: tuple[str, ...] = (),
    nonce_tokens: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if len(set(replay_tokens)) != len(replay_tokens) or len(set(nonce_tokens)) != len(nonce_tokens):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REPLAY_DETECTED")
    replay = tuple(sorted(set(state.consumed_replay_keys + replay_tokens)))
    nonces = tuple(sorted(set(state.consumed_nonces + nonce_tokens)))
    if len(replay) != len(state.consumed_replay_keys) + len(set(replay_tokens)) or len(nonces) != len(state.consumed_nonces) + len(set(nonce_tokens)):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REPLAY_DETECTED")
    if len(replay) > _MAX_REPLAY_ITEMS or len(nonces) > _MAX_REPLAY_ITEMS:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REPLAY_CAPACITY_EXCEEDED")
    return replay, nonces


def _fresh_request(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    try:
        request = evidence.require_verified_physical_operational_failover_v1_ir_promotion_request(
            value,
            config=facts.verification_config,
            now=now,
        )
    except evidence.PhysicalOperationalFailoverV1Error as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_INVALID"
        ) from exc
    return request


def _fresh_receipt(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
) -> evidence.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt:
    try:
        receipt = evidence.require_verified_physical_operational_failover_v1_fi_self_fence_receipt(
            value,
            config=facts.verification_config,
            now=now,
        )
    except evidence.PhysicalOperationalFailoverV1Error as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_FI_RECEIPT_INVALID"
        ) from exc
    return receipt


def _fresh_grant(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
    request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
) -> evidence.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant:
    try:
        grant = evidence.require_verified_physical_operational_failover_v1_witness_promotion_grant(
            value,
            config=facts.verification_config,
            now=now,
            expected_request=request,
        )
    except evidence.PhysicalOperationalFailoverV1Error as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_INVALID"
        ) from exc
    return grant


def _fresh_completion(
    value: object,
    *,
    facts: _Facts,
    now: datetime,
    grant: evidence.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
) -> evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion:
    try:
        completion = evidence.require_verified_physical_operational_failover_v1_ir_promotion_completion(
            value,
            config=facts.verification_config,
            now=now,
            expected_grant=grant,
        )
    except evidence.PhysicalOperationalFailoverV1Error as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_COMPLETION_INVALID"
        ) from exc
    return completion


def _exact_request_for_state(
    request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
    state: PhysicalOperationalFailoverV1WitnessLedgerState,
) -> None:
    predecessor_sha = _term_sha256(request.predecessor_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_INVALID")
    if (
        state.predecessor_term != request.predecessor_term
        or state.predecessor_term_sha256 != predecessor_sha
        or state.predecessor_termination_reason != request.predecessor_termination_reason
        or state.fi_self_fence_receipt_sha256 != request.fi_self_fence_receipt_sha256
        or state.request_sha256 != request.request_sha256
        or state.request_id != request.request_id
        or state.request_nonce != request.request_nonce
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_STATE_MISMATCH")


class RootOwnedPhysicalOperationalFailoverV1WitnessLedger:
    """Root-only, default-off transition service with no live operation hooks."""

    def __init__(
        self,
        *,
        config: RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig,
        durable_store: PhysicalOperationalFailoverV1WitnessLedgerDurableCasStore,
        clock: PhysicalOperationalFailoverV1WitnessLedgerClock,
    ) -> None:
        _require_root_runtime()
        self._facts = _facts(config)
        if not callable(getattr(durable_store, "read_current", None)) or not callable(
            getattr(durable_store, "append_compare_and_swap", None)
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_STORE_MISSING")
        if not callable(getattr(clock, "now_utc", None)):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_CLOCK_MISSING")
        self._store = durable_store
        self._clock = clock

    def bootstrap_normal_fi_term(self) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        """Create the sole normal FI term exactly once through CAS."""

        _require_root_runtime()
        now = _trusted_now(self._clock, floor=None)
        if now < self._facts.initial_fi_term.issued_at or now >= self._facts.initial_fi_term.expires_at:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_INITIAL_TERM_NOT_CURRENT")
        if _read_current(self._store) is not None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ALREADY_BOOTSTRAPPED")
        state = PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=1,
            phase=_PHASE_FI_ACTIVE,
            clock_floor=now,
            active_term=self._facts.initial_fi_term,
            active_term_sha256=self._facts.initial_fi_term_sha256,
        )
        entry = _make_entry(
            sequence=1,
            previous_head_sha256=_ZERO_SHA256,
            observed_at=now,
            event="bootstrap-fi-active",
            state=state,
        )
        return _append(self._store, current=None, entry=entry, next_state=state)

    def fence_or_expire_fi(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
        fi_self_fence_receipt: evidence.VerifiedPhysicalOperationalFailoverV1FiSelfFenceReceipt | None = None,
    ) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        """Consume one exact FI termination proof and leave no active writer."""

        _require_root_runtime()
        current = _read_current(self._store)
        if current is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_NOT_BOOTSTRAPPED")
        _expect_head(current, version=expected_version, head_sha256=expected_head_sha256)
        now = _trusted_now(self._clock, floor=current.state.clock_floor)
        if current.state.phase != _PHASE_FI_ACTIVE:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_FI_NOT_ACTIVE")
        verified_request = _fresh_request(request, facts=self._facts, now=now)
        predecessor_sha = _term_sha256(verified_request.predecessor_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_INVALID")
        if (
            verified_request.predecessor_term != current.state.active_term
            or verified_request.predecessor_term_sha256 != predecessor_sha
            or current.state.active_term_sha256 != predecessor_sha
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_PREDECESSOR_MISMATCH")

        receipt_sha: str | None
        if verified_request.predecessor_termination_reason == "fi-self-fence-receipt":
            verified_receipt = _fresh_receipt(fi_self_fence_receipt, facts=self._facts, now=now)
            if (
                verified_request.fi_self_fence_receipt_sha256 != verified_receipt.receipt_sha256
                or verified_receipt.predecessor_term != current.state.active_term
                or verified_receipt.predecessor_term_sha256 != predecessor_sha
                or verified_receipt.pins != verified_request.pins
            ):
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_FI_RECEIPT_MISMATCH")
            receipt_sha = verified_receipt.receipt_sha256
            replay_tokens = (
                _token("request-replay", verified_request.replay_key_sha256),
                _token("receipt-replay", verified_receipt.replay_key_sha256),
            )
            nonce_tokens = (
                _token("request-nonce", verified_request.request_nonce),
                _token("receipt-nonce", verified_receipt.receipt_nonce),
            )
            phase = _PHASE_FI_FENCED
            event = "fi-fenced"
        elif verified_request.predecessor_termination_reason == "predecessor-term-expired":
            if fi_self_fence_receipt is not None or now < current.state.active_term.expires_at:
                _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_EXPIRY_UNTRUSTED")
            receipt_sha = None
            replay_tokens = (_token("request-replay", verified_request.replay_key_sha256),)
            nonce_tokens = (_token("request-nonce", verified_request.request_nonce),)
            phase = _PHASE_FI_EXPIRED
            event = "fi-expired"
        else:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_INVALID")
        replay, nonces = _consumed(current.state, replay_tokens=replay_tokens, nonce_tokens=nonce_tokens)
        state = PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=current.version + 1,
            phase=phase,
            clock_floor=now,
            predecessor_term=current.state.active_term,
            predecessor_term_sha256=current.state.active_term_sha256,
            predecessor_termination_reason=verified_request.predecessor_termination_reason,
            fi_self_fence_receipt_sha256=receipt_sha,
            request_sha256=verified_request.request_sha256,
            request_id=verified_request.request_id,
            request_nonce=verified_request.request_nonce,
            canonical_request=verified_request.canonical_request,
            consumed_replay_keys=replay,
            consumed_nonces=nonces,
        )
        entry = _make_entry(
            sequence=state.sequence,
            previous_head_sha256=current.head_sha256,
            observed_at=now,
            event=event,
            state=state,
        )
        return _append(self._store, current=current, entry=entry, next_state=state)

    def reserve_ir_promotion(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
        reservation: PhysicalOperationalFailoverV1WitnessGrantReservation,
    ) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        """Durably reserve exactly one IR successor while FI remains inactive."""

        _require_root_runtime()
        current = _read_current(self._store)
        if current is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_NOT_BOOTSTRAPPED")
        _expect_head(current, version=expected_version, head_sha256=expected_head_sha256)
        now = _trusted_now(self._clock, floor=current.state.clock_floor)
        if current.state.phase not in {_PHASE_FI_FENCED, _PHASE_FI_EXPIRED}:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_FI_TERMINATION_REQUIRED")
        verified_request = _fresh_request(request, facts=self._facts, now=now)
        _exact_request_for_state(verified_request, current.state)
        normalized_reservation, _mapping = _reservation_mapping(reservation, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_RESERVATION_INVALID")
        if (
            normalized_reservation.successor_term.writer_epoch <= current.state.predecessor_term.writer_epoch
            or normalized_reservation.issued_at > now
            or now >= normalized_reservation.expires_at
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_RESERVATION_INVALID")
        replay, nonces = _consumed(
            current.state,
            replay_tokens=(_token("grant-replay", normalized_reservation.grant_replay_key_sha256),),
            nonce_tokens=(_token("grant-nonce", normalized_reservation.grant_nonce),),
        )
        state = PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=current.version + 1,
            phase=_PHASE_IR_GRANT_PENDING,
            clock_floor=now,
            predecessor_term=current.state.predecessor_term,
            predecessor_term_sha256=current.state.predecessor_term_sha256,
            predecessor_termination_reason=current.state.predecessor_termination_reason,
            fi_self_fence_receipt_sha256=current.state.fi_self_fence_receipt_sha256,
            request_sha256=verified_request.request_sha256,
            request_id=verified_request.request_id,
            request_nonce=verified_request.request_nonce,
            canonical_request=verified_request.canonical_request,
            reservation=normalized_reservation,
            consumed_replay_keys=replay,
            consumed_nonces=nonces,
        )
        entry = _make_entry(sequence=state.sequence, previous_head_sha256=current.head_sha256, observed_at=now, event="ir-grant-pending", state=state)
        return _append(self._store, current=current, entry=entry, next_state=state)

    def issue_reserved_ir_promotion_grant(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        issuer: PhysicalOperationalFailoverV1WitnessGrantIssuer,
    ) -> tuple[PhysicalOperationalFailoverV1WitnessLedgerSnapshot, evidence.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant]:
        """Sign one already-reserved grant, then CAS-mark its exact hash issued."""

        _require_root_runtime()
        current = _read_current(self._store)
        if current is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_NOT_BOOTSTRAPPED")
        _expect_head(current, version=expected_version, head_sha256=expected_head_sha256)
        now = _trusted_now(self._clock, floor=current.state.clock_floor)
        state = current.state
        if state.phase != _PHASE_IR_GRANT_PENDING or state.reservation is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_PENDING_REQUIRED")
        request = _fresh_request_from_state(state, facts=self._facts, now=now)
        reservation = state.reservation
        if now >= reservation.successor_term.expires_at:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SUCCESSOR_TERM_NOT_CURRENT")
        callback = getattr(issuer, "issue_witness_promotion_grant", None)
        if not callable(callback):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_ISSUER_MISSING")
        value = evidence.PhysicalOperationalFailoverV1WitnessPromotionGrantInput(
            grant_id=reservation.grant_id,
            grant_nonce=reservation.grant_nonce,
            issued_at=reservation.issued_at,
            expires_at=reservation.expires_at,
            replay_key_sha256=reservation.grant_replay_key_sha256,
            pins=request.pins,
            request_sha256=request.request_sha256,
            request_id=request.request_id,
            request_nonce=request.request_nonce,
            predecessor_term=request.predecessor_term,
            predecessor_termination_reason=request.predecessor_termination_reason,
            fi_self_fence_receipt_sha256=request.fi_self_fence_receipt_sha256,
            successor_term=reservation.successor_term,
            activation_route_artifact_sha256=reservation.activation_route_artifact_sha256,
            activation_receiver_permit_sha256=reservation.activation_receiver_permit_sha256,
            witness_ledger_sequence=current.version,
            witness_ledger_entry_sha256=current.head_sha256,
            witness_ledger_previous_head_sha256=current.entry.previous_head_sha256,
        )
        try:
            raw_grant = callback(
                value=value,
                verification_config=self._facts.verification_config,
                now=now,
                expected_request=request,
            )
        except PhysicalOperationalFailoverV1WitnessLedgerError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WitnessLedgerError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_ISSUER_FAILED"
            ) from exc
        try:
            grant = evidence.verify_physical_operational_failover_v1_witness_promotion_grant(
                raw_grant,
                config=self._facts.verification_config,
                now=now,
                expected_request=request,
            )
        except evidence.PhysicalOperationalFailoverV1Error as exc:
            raise PhysicalOperationalFailoverV1WitnessLedgerError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_ISSUER_INVALID"
            ) from exc
        if (
            grant.witness_ledger_sequence != current.version
            or grant.witness_ledger_entry_sha256 != current.head_sha256
            or grant.witness_ledger_previous_head_sha256 != current.entry.previous_head_sha256
            or grant.grant_id != reservation.grant_id
            or grant.grant_nonce != reservation.grant_nonce
            or grant.replay_key_sha256 != reservation.grant_replay_key_sha256
            or grant.successor_term != reservation.successor_term
            or grant.activation_route_artifact_sha256 != reservation.activation_route_artifact_sha256
            or grant.activation_receiver_permit_sha256 != reservation.activation_receiver_permit_sha256
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_RESERVATION_MISMATCH")
        next_state = PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=current.version + 1,
            phase=_PHASE_IR_GRANT_ISSUED,
            clock_floor=now,
            predecessor_term=state.predecessor_term,
            predecessor_term_sha256=state.predecessor_term_sha256,
            predecessor_termination_reason=state.predecessor_termination_reason,
            fi_self_fence_receipt_sha256=state.fi_self_fence_receipt_sha256,
            request_sha256=state.request_sha256,
            request_id=state.request_id,
            request_nonce=state.request_nonce,
            canonical_request=state.canonical_request,
            reservation=reservation,
            issued_grant_sha256=grant.grant_sha256,
            issued_grant_id=grant.grant_id,
            issued_grant_nonce=grant.grant_nonce,
            consumed_replay_keys=state.consumed_replay_keys,
            consumed_nonces=state.consumed_nonces,
        )
        entry = _make_entry(sequence=next_state.sequence, previous_head_sha256=current.head_sha256, observed_at=now, event="ir-grant-issued", state=next_state)
        return _append(self._store, current=current, entry=entry, next_state=next_state), grant

    def complete_ir_promotion(
        self,
        *,
        expected_version: int,
        expected_head_sha256: str,
        request: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest,
        grant: evidence.VerifiedPhysicalOperationalFailoverV1WitnessPromotionGrant,
        completion: evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionCompletion,
    ) -> PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        """Activate IR only after exact request, issued grant, and completion bind."""

        _require_root_runtime()
        current = _read_current(self._store)
        if current is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_NOT_BOOTSTRAPPED")
        _expect_head(current, version=expected_version, head_sha256=expected_head_sha256)
        now = _trusted_now(self._clock, floor=current.state.clock_floor)
        state = current.state
        if state.phase != _PHASE_IR_GRANT_ISSUED or state.reservation is None:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_ISSUED_GRANT_REQUIRED")
        verified_request = _fresh_request(request, facts=self._facts, now=now)
        _exact_request_for_state(verified_request, state)
        verified_grant = _fresh_grant(grant, facts=self._facts, now=now, request=verified_request)
        if (
            verified_grant.grant_sha256 != state.issued_grant_sha256
            or verified_grant.grant_id != state.issued_grant_id
            or verified_grant.grant_nonce != state.issued_grant_nonce
            or verified_grant.successor_term != state.reservation.successor_term
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_GRANT_STATE_MISMATCH")
        verified_completion = _fresh_completion(completion, facts=self._facts, now=now, grant=verified_grant)
        if now >= verified_grant.successor_term.expires_at:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_SUCCESSOR_TERM_NOT_CURRENT")
        if (
            verified_completion.grant_sha256 != verified_grant.grant_sha256
            or verified_completion.grant_id != verified_grant.grant_id
            or verified_completion.grant_nonce != verified_grant.grant_nonce
            or verified_completion.successor_term != verified_grant.successor_term
            or verified_completion.predecessor_term != state.predecessor_term
            or verified_completion.predecessor_termination_reason != state.predecessor_termination_reason
            or verified_completion.fi_self_fence_receipt_sha256 != state.fi_self_fence_receipt_sha256
        ):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_COMPLETION_MISMATCH")
        replay, nonces = _consumed(
            state,
            replay_tokens=(_token("completion-replay", verified_completion.replay_key_sha256),),
            nonce_tokens=(_token("completion-nonce", verified_completion.completion_nonce),),
        )
        next_state = PhysicalOperationalFailoverV1WitnessLedgerState(
            sequence=current.version + 1,
            phase=_PHASE_IR_ACTIVE,
            clock_floor=now,
            active_term=verified_grant.successor_term,
            active_term_sha256=_term_sha256(verified_grant.successor_term, code="OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_COMPLETION_MISMATCH"),
            predecessor_term=state.predecessor_term,
            predecessor_term_sha256=state.predecessor_term_sha256,
            predecessor_termination_reason=state.predecessor_termination_reason,
            fi_self_fence_receipt_sha256=state.fi_self_fence_receipt_sha256,
            request_sha256=state.request_sha256,
            request_id=state.request_id,
            request_nonce=state.request_nonce,
            canonical_request=state.canonical_request,
            reservation=state.reservation,
            issued_grant_sha256=verified_grant.grant_sha256,
            issued_grant_id=verified_grant.grant_id,
            issued_grant_nonce=verified_grant.grant_nonce,
            completion_sha256=verified_completion.completion_sha256,
            consumed_replay_keys=replay,
            consumed_nonces=nonces,
        )
        entry = _make_entry(sequence=next_state.sequence, previous_head_sha256=current.head_sha256, observed_at=now, event="ir-active", state=next_state)
        return _append(self._store, current=current, entry=entry, next_state=next_state)


def _fresh_request_from_state(
    state: PhysicalOperationalFailoverV1WitnessLedgerState,
    *,
    facts: _Facts,
    now: datetime,
) -> evidence.VerifiedPhysicalOperationalFailoverV1IrPromotionRequest:
    if state.canonical_request is None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_REHYDRATION_REQUIRED")
    try:
        request = evidence.verify_physical_operational_failover_v1_ir_promotion_request(
            state.canonical_request,
            config=facts.verification_config,
            now=now,
        )
    except evidence.PhysicalOperationalFailoverV1Error as exc:
        raise PhysicalOperationalFailoverV1WitnessLedgerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_LEDGER_REQUEST_REHYDRATION_INVALID"
        ) from exc
    _exact_request_for_state(request, state)
    return request
