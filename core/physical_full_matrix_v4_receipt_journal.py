"""V4-only, Witness-anchored receipt journal for Full-Matrix effects.

This is deliberately *not* an adapter for the V1/V3 receipt journals.  It
implements only the ``physical_full_matrix_execution_driver_v4`` protocol and
stores no provider, database, SSH, Object Storage, Docker, or transport
credential/material.  The local records are useful evidence, but are never an
authority by themselves: each operation reads a required, injected Witness
anchor and every irreversible ``effect-started`` and ``completed`` transition
is committed to that external append-only authority first.

The order is intentionally conservative:

``CLAIMED -> Witness effect-start commitment -> local EFFECT_STARTED``
``EFFECT_STARTED -> Witness completion commitment -> local COMPLETED``

Thus a process crash after the external Witness commit but before the local
create-only record leaves an anchor-pending state.  On restart it is always
indeterminate; this module never calls an adapter and never turns that state
into a retry permit.  Likewise, a persisted local ``EFFECT_STARTED`` record
is indeterminate until an out-of-band, explicitly reviewed reconciler acts.

The injected anchor is a narrow root-owned boundary.  Its implementation must
authenticate and attest its returned heads; this module validates the typed
binding, predecessor, sequence, and commitment hashes, but deliberately has
no network or signing implementation of its own.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping, Sequence
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
from typing import Any, Protocol
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import SHA256_RE, canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_witness_anchor_wire as _wire


__all__ = (
    "FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT",
    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_PLAN_BINDING_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA",
    "derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256",
    "derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256",
    "PhysicalFullMatrixV4ReceiptJournalClock",
    "PhysicalFullMatrixV4ReceiptJournalCampaignBinding",
    "PhysicalFullMatrixV4ReceiptJournalError",
    "PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof",
    "PhysicalFullMatrixV4WitnessJournalAnchor",
    "PhysicalFullMatrixV4WitnessJournalAnchorCommitment",
    "PhysicalFullMatrixV4WitnessJournalAnchorHead",
    "PhysicalFullMatrixV4WitnessJournalAnchorReceipt",
    "RootOwnedPhysicalFullMatrixV4ReceiptJournal",
    "RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig",
    "VerifiedPhysicalFullMatrixV4CampaignContinuity",
    "require_verified_physical_full_matrix_v4_campaign_continuity",
    "require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof",
)


PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-receipt-journal-v1"
)
PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-receipt-journal-campaign-binding-v1"
)
# The signed Witness wire owns the canonical baseline bytes/digest.  The
# journal exposes the same public helper below, but never keeps a competing
# serialization of these rehydration pins.
PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_PLAN_BINDING_SCHEMA = (
    _wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_BASELINE_PLAN_BINDING_SCHEMA
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-journal-anchor-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA = (
    _wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_COMMITMENT_SCHEMA
)
PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA = (
    _driver.PHYSICAL_FULL_MATRIX_V4_PREDECESSOR_PHASE_COMPLETION_ANCHOR_PROOF_SCHEMA
)

# The typed opaque capability is driver-owned because it must be attached to
# the private adapter request.  This journal is its sole concrete issuer: the
# aliases below make that ownership relationship explicit without duplicating
# an alternate proof grammar or a second verifier.
PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof = (
    _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof
)
require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof = (
    _driver.require_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof
)

# This is a deployment-controlled fixed root, not a constructor or CLI path.
# Tests patch the constant in-process; production must provision it root-owned.
FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT = Path(
    "/var/lib/trading-bot/physical-full-matrix-v4-receipt-journal"
)

_VERSION = 1
_MODE = "root-owned-v4-witness-anchored-create-only-journal-v1"
_RECORDS_DIRECTORY = "records"
_LOCK_FILENAME = "receipt-journal.lock"
_BINDING_FILENAME = "binding.json"
_RECORD_FILENAME_RE = re.compile(r"^([0-9]{20})-([0-9a-f]{64})\.json$", re.ASCII)
_CLAIM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)
_ZERO_SHA256 = "0" * 64
_CONTINUITY_CAPABILITY = object()
_MAX_RECORD_BYTES = 128 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_MAX_RECORDS = 8_192
_MAX_RUNS = 256
_EVENT_CLAIMED = "claimed"
_EVENT_EFFECT_STARTED = "effect-started"
_EVENT_COMPLETED = "completed"
_ANCHOR_EVENTS = frozenset({_EVENT_EFFECT_STARTED, _EVENT_COMPLETED})
_EVENTS = frozenset({_EVENT_CLAIMED, *_ANCHOR_EVENTS})

_BINDING_FIELDS = frozenset(
    {
        "schema",
        "version",
        "mode",
        "journal_binding_sha256",
        "anchor_genesis_head_sha256",
        "anchor_genesis_sequence",
        "campaign_binding",
        "execution_authorized",
        "promotion_authorized",
        "full_matrix_executed",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "version",
        "sequence",
        "previous_record_sha256",
        "event",
        "occurred_at",
        "clock_floor",
        "run_id",
        "plan_sha256",
        "phase_sequence",
        "phase_request_sha256",
        "effect_key",
        "claim_id",
        "receipt_base64",
        "receipt_sha256",
        "anchor_previous_sequence",
        "anchor_previous_head_sha256",
        "anchor_sequence",
        "anchor_head_sha256",
        "anchor_commitment_sha256",
        "anchor_attestation_sha256",
        "anchor_event_sha256",
        "local_event_sha256",
        "record_sha256",
    }
)


class PhysicalFullMatrixV4ReceiptJournalError(RuntimeError):
    """The V4 root-journal boundary rejected unsafe durable state."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4ReceiptJournalError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
    """One redacted externally-attested V4 transition commitment.

    The actual Witness implementation owns signature verification and
    immutability.  It must reject an incorrect predecessor and return a
    canonical typed head through :class:`PhysicalFullMatrixV4WitnessJournalAnchor`.
    """

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    event: str
    run_id: UUID
    plan_sha256: str
    phase_sequence: int
    phase_request_sha256: str
    effect_key: str
    claim_id: str
    previous_anchor_sequence: int
    previous_anchor_head_sha256: str
    local_previous_record_sha256: str
    local_event_sha256: str
    receipt_sha256: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessJournalAnchorHead:
    """A verified current immutable Witness head returned by the anchor."""

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    sequence: int
    head_sha256: str
    previous_head_sha256: str
    commitment_sha256: str
    attestation_sha256: str
    commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment | None


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
    """The anchor's exact accepted response to a new commitment."""

    schema: str
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    sequence: int
    previous_head_sha256: str
    head_sha256: str
    commitment_sha256: str
    attestation_sha256: str


class PhysicalFullMatrixV4WitnessJournalAnchor(Protocol):
    """Required external immutable/attested head boundary.

    ``read_head`` receives the durable local tail the Journal expects.  The
    boundary must accept only that exact signed current head or its exact
    signed immediate successor (the latter is needed to detect an externally
    committed/local-record-pending crash).  It must itself verify the
    Witness's signer/transport and preserve an append-only external history.
    A local journal implementation is not a valid substitute.
    """

    def read_head(
        self,
        *,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        expected_anchor_sequence: int,
        expected_anchor_head_sha256: str,
    ) -> PhysicalFullMatrixV4WitnessJournalAnchorHead: ...

    def append_commitment(
        self,
        *,
        commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
    ) -> PhysicalFullMatrixV4WitnessJournalAnchorReceipt: ...


class PhysicalFullMatrixV4ReceiptJournalClock(Protocol):
    """Root-owned clock used for the persisted V4 clock floor."""

    def now_utc(self) -> datetime: ...


@dataclass(frozen=True)
class RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig:
    """Default-off fixed-root configuration; no state path is configurable."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_DEFAULT_ENABLED
    journal_binding_sha256: str | None = None
    campaign_binding: "PhysicalFullMatrixV4ReceiptJournalCampaignBinding | None" = None
    anchor_genesis_head_sha256: str = _ZERO_SHA256
    anchor_genesis_sequence: int = 0
    journal_mode: str = _MODE


@dataclass(frozen=True)
class PhysicalFullMatrixV4ReceiptJournalCampaignBinding:
    """Non-secret immutable identity of the one V4 campaign journal.

    It is deliberately more than an opaque hash: the Journal recomputes its
    canonical digest on every operation and can therefore prove sequence zero
    belongs to this exact run/plan/initial writer direction and Witness
    genesis, rather than treating an empty local directory as authority.
    """

    schema: str
    run_id: UUID
    plan_sha256: str
    initial_active_binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    anchor_genesis_sequence: int
    anchor_genesis_head_sha256: str
    execution_authorized: bool = False
    promotion_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV4CampaignContinuity:
    """Process-local projection for a future V4 plan-rehydration admission.

    It is not a plan, writer permit, promotion permit, raw receipt, or effect
    permit.  Only this journal mints it after the fixed root and external
    Witness head agree; a rehydrator must separately verify it.
    """

    run_id: UUID
    plan_sha256: str
    completed_sequence: int
    active_binding: _driver.PhysicalFullMatrixV4ExecutionBinding
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_CAMPAIGN_CONTINUITY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    journal_binding_sha256: str
    campaign_binding: PhysicalFullMatrixV4ReceiptJournalCampaignBinding
    baseline_plan_binding_sha256: str
    anchor_genesis_head_sha256: str
    anchor_genesis_sequence: int


@dataclass(frozen=True)
class _Record:
    sequence: int
    previous_record_sha256: str
    event: str
    occurred_at: datetime
    clock_floor: datetime
    run_id: UUID
    plan_sha256: str
    phase_sequence: int
    phase_request_sha256: str
    effect_key: str
    claim_id: str
    receipt: bytes | None
    receipt_sha256: str | None
    anchor_previous_sequence: int
    anchor_previous_head_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    anchor_commitment_sha256: str | None
    anchor_attestation_sha256: str | None
    anchor_event_sha256: str | None
    local_event_sha256: str
    record_sha256: str


def _commitment_from_anchored_record(
    *,
    record: _Record,
    facts: _Facts,
    code: str,
) -> PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
    """Reconstruct and validate the one signed commitment for a record.

    This is intentionally not a new wire grammar.  It merely rebuilds the
    existing canonical Witness commitment from a create-only local record so
    a later durable projection can cross-check the exact historical pins.
    """

    if (
        record.event not in _ANCHOR_EVENTS
        or record.anchor_commitment_sha256 is None
        or record.anchor_attestation_sha256 is None
        or record.anchor_event_sha256 is None
        or record.anchor_sequence != record.anchor_previous_sequence + 1
    ):
        _fail(code)
    value = PhysicalFullMatrixV4WitnessJournalAnchorCommitment(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA,
        journal_binding_sha256=facts.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
        anchor_genesis_sequence=facts.anchor_genesis_sequence,
        anchor_genesis_head_sha256=facts.anchor_genesis_head_sha256,
        event=record.event,
        run_id=record.run_id,
        plan_sha256=record.plan_sha256,
        phase_sequence=record.phase_sequence,
        phase_request_sha256=record.phase_request_sha256,
        effect_key=record.effect_key,
        claim_id=record.claim_id,
        previous_anchor_sequence=record.anchor_previous_sequence,
        previous_anchor_head_sha256=record.anchor_previous_head_sha256,
        local_previous_record_sha256=record.previous_record_sha256,
        local_event_sha256=record.anchor_event_sha256,
        receipt_sha256=record.receipt_sha256,
        occurred_at=record.occurred_at,
    )
    try:
        checked = _check_commitment(value, facts=facts)
    except PhysicalFullMatrixV4ReceiptJournalError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
    if record.anchor_commitment_sha256 != _commitment_sha256(checked):
        _fail(code)
    return checked


@dataclass(frozen=True)
class _Pending:
    event: str
    record: _Record


@dataclass(frozen=True)
class _RunState:
    run_id: UUID
    plan_sha256: str
    receipts: tuple[bytes, ...]
    pending: _Pending | None


@dataclass(frozen=True)
class _State:
    records: tuple[_Record, ...]
    runs: dict[str, _RunState]
    local_head_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str
    clock_floor: datetime | None


@dataclass(frozen=True)
class _OpenStorage:
    root_fd: int
    records_fd: int


@dataclass(frozen=True)
class _AnchorStatus:
    head: PhysicalFullMatrixV4WitnessJournalAnchorHead
    pending: PhysicalFullMatrixV4WitnessJournalAnchorCommitment | None


@dataclass(frozen=True)
class _LiveEffectStartAnchor:
    """One process-local post-append/readback fact retained for an adapter.

    No local path, descriptor, raw record bytes, signer key, transport, or
    provider object crosses this boundary.  The fields are retained only until
    the matching completion append or process exit; a restart therefore can
    never recreate a proof for an indeterminate historical effect.
    """

    effect_start: _driver.PhysicalFullMatrixV4EffectStart
    record: _Record
    commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment
    durable_head: PhysicalFullMatrixV4WitnessJournalAnchorHead


@dataclass(frozen=True)
class _ContinuityState:
    run_id: UUID
    plan_sha256: str
    completed_sequence: int
    active_binding_snapshot: object
    journal_binding_sha256: str
    baseline_plan_binding_sha256: str
    anchor_sequence: int
    anchor_head_sha256: str


_CONTINUITY_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV4CampaignContinuity, _ContinuityState
] = WeakKeyDictionary()


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str, permit_zero: bool = False) -> str:
    if (
        type(value) is not str
        or SHA256_RE.fullmatch(value) is None
        or (not permit_zero and value == _ZERO_SHA256)
    ):
        _fail(code)
    return value


def _run_id(value: object, *, code: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        _fail(code)
    return value


def _phase_sequence(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= len(_driver.PHYSICAL_FULL_MATRIX_V4_PHASES):
        _fail(code)
    return value


def _claim_id(value: object, *, code: str) -> str:
    if type(value) is not str or _CLAIM_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).isoformat().replace("+00:00", "Z")


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
    result = _utc(result, code=code)
    if _render_timestamp(result, code=code) != value:
        _fail(code)
    return result


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_JSON_INVALID")


def _parse_canonical(raw: object, *, code: str) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_RECORD_BYTES:
        _fail(code)
    try:
        parsed = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, PhysicalFullMatrixV4ReceiptJournalError) as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
    if type(parsed) is not dict or _canonical(parsed, code=code) != raw:
        _fail(code)
    return parsed


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, *, permit_none: bool, code: str) -> bytes | None:
    if value is None and permit_none:
        return None
    if type(value) is not str or not value:
        _fail(code)
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, base64.binascii.Error) as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
    if not result or _b64(result) != value:
        _fail(code)
    return result


def _event_body(
    *,
    event: str,
    occurred_at: datetime,
    clock_floor: datetime,
    run_id: UUID,
    plan_sha256: str,
    phase_sequence: int,
    phase_request_sha256: str,
    effect_key: str,
    claim_id: str,
    previous_record_sha256: str,
    previous_anchor_sequence: int,
    previous_anchor_head_sha256: str,
    receipt_sha256: str | None,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA,
        "version": _VERSION,
        "event": event,
        "occurred_at": _render_timestamp(
            occurred_at,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_INVALID",
        ),
        "clock_floor": _render_timestamp(
            clock_floor,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_INVALID",
        ),
        "run_id": str(run_id),
        "plan_sha256": plan_sha256,
        "phase_sequence": phase_sequence,
        "phase_request_sha256": phase_request_sha256,
        "effect_key": effect_key,
        "claim_id": claim_id,
        "previous_record_sha256": previous_record_sha256,
        "previous_anchor_sequence": previous_anchor_sequence,
        "previous_anchor_head_sha256": previous_anchor_head_sha256,
        "receipt_sha256": receipt_sha256,
    }


def _event_sha256(**kwargs: object) -> str:
    return _sha256_bytes(
        _canonical(
            _event_body(**kwargs),  # type: ignore[arg-type]
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        )
    )


def _wire_commitment(
    value: PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
    *,
    code: str,
) -> _wire.PhysicalFullMatrixV4WitnessAnchorCommitment:
    """Map one journal event to the sole signed Witness commitment grammar.

    ``anchor_commitment_sha256`` is intentionally the wire digest itself.
    The local event hash remains a separately named field *inside* that
    commitment; it must never become a competing projected commitment hash.
    """

    try:
        # The public wire builder is deliberately the only place that derives
        # the public phase label.  Calling the dataclass constructor here
        # would leave a second journal-owned way to assemble a signed fact.
        result = _wire.build_physical_full_matrix_v4_witness_anchor_commitment(
            journal_binding_sha256=value.journal_binding_sha256,
            baseline_plan_binding_sha256=value.baseline_plan_binding_sha256,
            run_id=value.run_id,
            plan_sha256=value.plan_sha256,
            anchor_genesis_sequence=value.anchor_genesis_sequence,
            anchor_genesis_head_sha256=value.anchor_genesis_head_sha256,
            event=value.event,
            phase_sequence=value.phase_sequence,
            phase_request_sha256=value.phase_request_sha256,
            effect_key=value.effect_key,
            claim_id=value.claim_id,
            receipt_sha256=value.receipt_sha256,
            previous_anchor_sequence=value.previous_anchor_sequence,
            previous_anchor_head_sha256=value.previous_anchor_head_sha256,
            local_previous_record_sha256=value.local_previous_record_sha256,
            local_event_sha256=value.local_event_sha256,
            occurred_at=value.occurred_at,
        )
        # Materialize the exact canonical wire representation now.  This
        # checks all fields (including the deterministic phase label) before
        # a value can be accepted from an injected adapter.
        _wire.canonical_physical_full_matrix_v4_witness_anchor_commitment_bytes(
            result
        )
        return result
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc


def _commitment_sha256(value: PhysicalFullMatrixV4WitnessJournalAnchorCommitment) -> str:
    try:
        return _wire.derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(
            _wire_commitment(
                value,
                code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
            )
        )
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID"
        ) from exc


def _check_commitment(
    value: object,
    *,
    facts: _Facts,
) -> PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
    if type(value) is not PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA
        or value.journal_binding_sha256 != facts.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != facts.baseline_plan_binding_sha256
        or value.anchor_genesis_sequence != facts.anchor_genesis_sequence
        or value.anchor_genesis_head_sha256 != facts.anchor_genesis_head_sha256
        or value.run_id != facts.campaign_binding.run_id
        or value.plan_sha256 != facts.campaign_binding.plan_sha256
        or value.event not in _ANCHOR_EVENTS
        or type(value.previous_anchor_sequence) is not int
        or value.previous_anchor_sequence < facts.anchor_genesis_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _run_id(value.run_id, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(value.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _phase_sequence(value.phase_sequence, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(value.phase_request_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(value.effect_key, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _claim_id(value.claim_id, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    if type(value.anchor_genesis_sequence) is not int or value.anchor_genesis_sequence < 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(
        value.anchor_genesis_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(
        value.previous_anchor_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(
        value.local_previous_record_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(value.local_event_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    if value.event == _EVENT_EFFECT_STARTED:
        if value.receipt_sha256 is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    else:
        _sha256(value.receipt_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _utc(value.occurred_at, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _wire_commitment(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
    )
    return value


def _check_anchor_head(
    value: object,
    *,
    facts: _Facts,
) -> PhysicalFullMatrixV4WitnessJournalAnchorHead:
    if type(value) is not PhysicalFullMatrixV4WitnessJournalAnchorHead:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA
        or value.journal_binding_sha256 != facts.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != facts.baseline_plan_binding_sha256
        or type(value.sequence) is not int
        or value.sequence < facts.anchor_genesis_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(
        value.head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(
        value.previous_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(
        value.commitment_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    _sha256(
        value.attestation_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    if value.sequence == facts.anchor_genesis_sequence:
        if (
            value.head_sha256 != facts.anchor_genesis_head_sha256
            or value.previous_head_sha256 != _ZERO_SHA256
            or value.commitment_sha256 != _ZERO_SHA256
            or value.attestation_sha256 != _ZERO_SHA256
            or value.commitment is not None
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
        return value
    commitment = _check_commitment(value.commitment, facts=facts)
    if (
        value.head_sha256 == value.previous_head_sha256
        or value.commitment_sha256 != _commitment_sha256(commitment)
        or value.previous_head_sha256 != commitment.previous_anchor_head_sha256
        or value.sequence != commitment.previous_anchor_sequence + 1
        or value.attestation_sha256 == _ZERO_SHA256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    return value


def _check_anchor_receipt(
    value: object,
    *,
    facts: _Facts,
    before: PhysicalFullMatrixV4WitnessJournalAnchorHead,
    commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
) -> PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
    if type(value) is not PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA
        or value.journal_binding_sha256 != facts.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != facts.baseline_plan_binding_sha256
        or type(value.sequence) is not int
        or value.sequence != before.sequence + 1
        or value.previous_head_sha256 != before.head_sha256
        or value.head_sha256 == before.head_sha256
        or value.commitment_sha256 != _commitment_sha256(commitment)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_INVALID")
    _sha256(value.head_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_INVALID")
    _sha256(value.attestation_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_INVALID")
    return value


def _anchor_head(
    *,
    anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
    facts: _Facts,
    expected_anchor_sequence: int,
    expected_anchor_head_sha256: str,
) -> PhysicalFullMatrixV4WitnessJournalAnchorHead:
    if (
        type(expected_anchor_sequence) is not int
        or expected_anchor_sequence < facts.anchor_genesis_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID")
    _sha256(
        expected_anchor_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        permit_zero=True,
    )
    callback = getattr(anchor, "read_head", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_REQUIRED")
    try:
        value = callback(
            journal_binding_sha256=facts.journal_binding_sha256,
            baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
            expected_anchor_sequence=expected_anchor_sequence,
            expected_anchor_head_sha256=expected_anchor_head_sha256,
        )
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_READ_FAILED"
        ) from exc
    return _check_anchor_head(value, facts=facts)


def _anchor_append(
    *,
    anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
    facts: _Facts,
    before: PhysicalFullMatrixV4WitnessJournalAnchorHead,
    commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
) -> PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
    callback = getattr(anchor, "append_commitment", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_REQUIRED")
    try:
        value = callback(commitment=commitment)
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_FAILED"
        ) from exc
    return _check_anchor_receipt(
        value,
        facts=facts,
        before=before,
        commitment=commitment,
    )


def _require_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _require_fd_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "fdatasync"):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PLATFORM_UNSUPPORTED")


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
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
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
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE"
        ) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_secure_root() -> int:
    root = FIXED_PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT
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
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
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
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4ReceiptJournalError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_ROOT_UNSAFE"
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
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
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


def _ensure_records_directory(root_fd: int) -> int:
    created = False
    descriptor = -1
    try:
        try:
            os.mkdir(_RECORDS_DIRECTORY, 0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        descriptor = os.open(
            _RECORDS_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=root_fd,
        )
        if created:
            # mkdir(2) is subject to umask; pin the required private mode on
            # the fd before any metadata is trusted.
            os.fchmod(descriptor, 0o700)
            os.fsync(descriptor)
            os.fsync(root_fd)
        before = _safe_child_metadata(
            root_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_DIRECTORY_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _RECORDS_DIRECTORY,
            directory=True,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_DIRECTORY_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            os.close(descriptor)
            descriptor = -1
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_DIRECTORY_UNSAFE")
        return descriptor
    except PhysicalFullMatrixV4ReceiptJournalError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_DIRECTORY_UNSAFE"
        ) from exc


def _open_lock(root_fd: int) -> int:
    descriptor = -1
    created = False
    try:
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
        before = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_LOCK_UNSAFE",
        )
        opened = os.fstat(descriptor)
        after = _safe_child_metadata(
            root_fd,
            _LOCK_FILENAME,
            directory=False,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_LOCK_UNSAFE",
        )
        if _metadata_tuple(before) != _metadata_tuple(opened) or _metadata_tuple(after) != _metadata_tuple(before):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_LOCK_UNSAFE")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except PhysicalFullMatrixV4ReceiptJournalError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_LOCK_OPEN_FAILED"
        ) from exc


@contextmanager
def _locked_storage() -> Iterator[_OpenStorage]:
    root_fd = _open_secure_root()
    records_fd = -1
    lock_fd = -1
    try:
        records_fd = _ensure_records_directory(root_fd)
        lock_fd = _open_lock(root_fd)
        yield _OpenStorage(root_fd=root_fd, records_fd=records_fd)
    finally:
        try:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            for descriptor in (lock_fd, records_fd, root_fd):
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
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
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
            raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
        if type(written) is not int or written <= 0:
            _fail(code)
        view = view[written:]


def _write_create_only_at(parent_fd: int, name: str, payload: bytes, *, code: str) -> None:
    if (
        type(name) is not str
        or type(payload) is not bytes
        or not 1 <= len(payload) <= _MAX_RECORD_BYTES
        or "/" in name
        or "\\" in name
        or name in {".", ".."}
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
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_STATE_UNSAFE")
        _write_all(descriptor, payload, code=code)
        os.fdatasync(descriptor)
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_DIRECTORY_FSYNC_FAILED"
        ) from exc


def _binding_payload(facts: _Facts) -> bytes:
    return _canonical(
        {
            "schema": PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA,
            "version": _VERSION,
            "mode": _MODE,
            "journal_binding_sha256": facts.journal_binding_sha256,
            "anchor_genesis_head_sha256": facts.anchor_genesis_head_sha256,
            "anchor_genesis_sequence": facts.anchor_genesis_sequence,
            "campaign_binding": _campaign_binding_body(facts.campaign_binding),
            "execution_authorized": False,
            "promotion_authorized": False,
            "full_matrix_executed": False,
        },
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_INVALID",
    )


def _init_binding(storage: _OpenStorage, *, facts: _Facts) -> None:
    expected = _binding_payload(facts)
    try:
        actual = _read_file_at(
            storage.root_fd,
            _BINDING_FILENAME,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_MISSING_OR_UNSAFE",
        )
    except PhysicalFullMatrixV4ReceiptJournalError as exc:
        if exc.code != "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_MISSING_OR_UNSAFE":
            raise
        _write_create_only_at(
            storage.root_fd,
            _BINDING_FILENAME,
            expected,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_WRITE_FAILED",
        )
        actual = _read_file_at(
            storage.root_fd,
            _BINDING_FILENAME,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_MISSING_OR_UNSAFE",
        )
    if actual != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_FOREIGN_BINDING")
    parsed = _parse_canonical(actual, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_INVALID")
    if set(parsed) != _BINDING_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BINDING_INVALID")


def _record_without_digest(value: _Record) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA,
        "version": _VERSION,
        "sequence": value.sequence,
        "previous_record_sha256": value.previous_record_sha256,
        "event": value.event,
        "occurred_at": _render_timestamp(
            value.occurred_at,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        "clock_floor": _render_timestamp(
            value.clock_floor,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        "run_id": str(value.run_id),
        "plan_sha256": value.plan_sha256,
        "phase_sequence": value.phase_sequence,
        "phase_request_sha256": value.phase_request_sha256,
        "effect_key": value.effect_key,
        "claim_id": value.claim_id,
        "receipt_base64": None if value.receipt is None else _b64(value.receipt),
        "receipt_sha256": value.receipt_sha256,
        "anchor_previous_sequence": value.anchor_previous_sequence,
        "anchor_previous_head_sha256": value.anchor_previous_head_sha256,
        "anchor_sequence": value.anchor_sequence,
        "anchor_head_sha256": value.anchor_head_sha256,
        "anchor_commitment_sha256": value.anchor_commitment_sha256,
        "anchor_attestation_sha256": value.anchor_attestation_sha256,
        "anchor_event_sha256": value.anchor_event_sha256,
        "local_event_sha256": value.local_event_sha256,
    }


def _record_digest(value: _Record) -> str:
    return _sha256_bytes(
        _canonical(
            _record_without_digest(value),
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        )
    )


def _record_mapping(value: _Record) -> dict[str, object]:
    result = _record_without_digest(value)
    result["record_sha256"] = value.record_sha256
    return result


def _record_from_mapping(value: object) -> _Record:
    if type(value) is not dict or set(value) != _RECORD_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_FIELDS_INVALID")
    if (
        value["schema"] != PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA
        or value["version"] != _VERSION
        or type(value["sequence"]) is not int
        or value["sequence"] < 1
        or value["event"] not in _EVENTS
        or type(value["anchor_previous_sequence"]) is not int
        or value["anchor_previous_sequence"] < 0
        or type(value["anchor_sequence"]) is not int
        or value["anchor_sequence"] < value["anchor_previous_sequence"]
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
    try:
        run_id = UUID(value["run_id"])
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID"
        ) from exc
    if str(run_id) != value["run_id"]:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
    _run_id(run_id, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
    receipt = _unb64(
        value["receipt_base64"],
        permit_none=True,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID",
    )
    receipt_sha = value["receipt_sha256"]
    if receipt is None:
        if receipt_sha is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID")
    else:
        if len(receipt) > _MAX_RECEIPT_BYTES:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID")
        _sha256(
            receipt_sha,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID",
        )
        if _sha256_bytes(receipt) != receipt_sha:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID")
    commitment_sha = value["anchor_commitment_sha256"]
    attestation_sha = value["anchor_attestation_sha256"]
    anchor_event_sha = value["anchor_event_sha256"]
    if value["event"] == _EVENT_CLAIMED:
        if (
            commitment_sha is not None
            or attestation_sha is not None
            or anchor_event_sha is not None
            or receipt is not None
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
    else:
        _sha256(commitment_sha, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
        _sha256(attestation_sha, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
        _sha256(anchor_event_sha, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
        if value["event"] == _EVENT_EFFECT_STARTED and receipt is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
        if value["event"] == _EVENT_COMPLETED and receipt is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
    result = _Record(
        sequence=value["sequence"],
        previous_record_sha256=_sha256(
            value["previous_record_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
            permit_zero=True,
        ),
        event=value["event"],
        occurred_at=_timestamp(
            value["occurred_at"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        clock_floor=_timestamp(
            value["clock_floor"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        run_id=run_id,
        plan_sha256=_sha256(value["plan_sha256"], code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID"),
        phase_sequence=_phase_sequence(
            value["phase_sequence"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        phase_request_sha256=_sha256(
            value["phase_request_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        effect_key=_sha256(value["effect_key"], code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID"),
        claim_id=_claim_id(value["claim_id"], code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID"),
        receipt=receipt,
        receipt_sha256=receipt_sha,
        anchor_previous_sequence=value["anchor_previous_sequence"],
        anchor_previous_head_sha256=_sha256(
            value["anchor_previous_head_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
            permit_zero=True,
        ),
        anchor_sequence=value["anchor_sequence"],
        anchor_head_sha256=_sha256(
            value["anchor_head_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
            permit_zero=True,
        ),
        anchor_commitment_sha256=commitment_sha,
        anchor_attestation_sha256=attestation_sha,
        anchor_event_sha256=anchor_event_sha,
        local_event_sha256=_sha256(
            value["local_event_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
        record_sha256=_sha256(
            value["record_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
        ),
    )
    if result.record_sha256 != _record_digest(result):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_DIGEST_INVALID")
    return result


def _load_records(storage: _OpenStorage) -> tuple[_Record, ...]:
    try:
        names = os.listdir(storage.records_fd)
    except OSError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_READ_FAILED"
        ) from exc
    if len(names) > _MAX_RECORDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_LIMIT")
    matches: list[tuple[int, str, str]] = []
    for name in names:
        match = _RECORD_FILENAME_RE.fullmatch(name)
        if match is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORDS_DIRECTORY_UNSAFE")
        matches.append((int(match.group(1)), match.group(2), name))
    matches.sort()
    records: list[_Record] = []
    for expected_sequence, (sequence, filename_digest, name) in enumerate(matches, start=1):
        if sequence != expected_sequence:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_SEQUENCE_INVALID")
        raw = _read_file_at(
            storage.records_fd,
            name,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_UNSAFE",
        )
        record = _record_from_mapping(
            _parse_canonical(raw, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID")
        )
        if record.sequence != sequence or record.record_sha256 != filename_digest:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_FILENAME_MISMATCH")
        records.append(record)
    return tuple(records)


def _same_phase(
    record: _Record,
    *,
    run_id: UUID,
    plan_sha256: str,
    phase_sequence: int,
    phase_request_sha256: str,
    effect_key: str,
    claim_id: str | None = None,
) -> bool:
    return (
        record.run_id == run_id
        and record.plan_sha256 == plan_sha256
        and record.phase_sequence == phase_sequence
        and record.phase_request_sha256 == phase_request_sha256
        and record.effect_key == effect_key
        and (claim_id is None or record.claim_id == claim_id)
    )


def _apply_record(
    *,
    state: _State,
    record: _Record,
) -> _State:
    if record.sequence != len(state.records) + 1 or record.previous_record_sha256 != state.local_head_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_CHAIN_INVALID")
    if record.clock_floor < record.occurred_at or (
        state.clock_floor is not None and record.clock_floor < state.clock_floor
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_REGRESSION")
    if (
        record.anchor_previous_sequence != state.anchor_sequence
        or record.anchor_previous_head_sha256 != state.anchor_head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_CHAIN_INVALID")
    if record.event == _EVENT_CLAIMED:
        if (
            record.anchor_sequence != state.anchor_sequence
            or record.anchor_head_sha256 != state.anchor_head_sha256
            or record.anchor_commitment_sha256 is not None
            or record.anchor_attestation_sha256 is not None
            or record.anchor_event_sha256 is not None
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_CHAIN_INVALID")
    elif (
        record.anchor_sequence != state.anchor_sequence + 1
        or record.anchor_head_sha256 == state.anchor_head_sha256
        or record.anchor_commitment_sha256 is None
        or record.anchor_attestation_sha256 is None
        or record.anchor_event_sha256 is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_CHAIN_INVALID")
    expected_event_sha = _event_sha256(
        event=record.event,
        occurred_at=record.occurred_at,
        clock_floor=record.clock_floor,
        run_id=record.run_id,
        plan_sha256=record.plan_sha256,
        phase_sequence=record.phase_sequence,
        phase_request_sha256=record.phase_request_sha256,
        effect_key=record.effect_key,
        claim_id=record.claim_id,
        previous_record_sha256=record.previous_record_sha256,
        previous_anchor_sequence=record.anchor_previous_sequence,
        previous_anchor_head_sha256=record.anchor_previous_head_sha256,
        receipt_sha256=record.receipt_sha256,
    )
    if record.local_event_sha256 != expected_event_sha:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_EVENT_MISMATCH")
    if record.event in _ANCHOR_EVENTS:
        expected_anchor_event_sha = _event_sha256(
            event=record.event,
            occurred_at=record.occurred_at,
            clock_floor=record.occurred_at,
            run_id=record.run_id,
            plan_sha256=record.plan_sha256,
            phase_sequence=record.phase_sequence,
            phase_request_sha256=record.phase_request_sha256,
            effect_key=record.effect_key,
            claim_id=record.claim_id,
            previous_record_sha256=record.previous_record_sha256,
            previous_anchor_sequence=record.anchor_previous_sequence,
            previous_anchor_head_sha256=record.anchor_previous_head_sha256,
            receipt_sha256=record.receipt_sha256,
        )
        if record.anchor_event_sha256 != expected_anchor_event_sha:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_EVENT_MISMATCH")
    runs = dict(state.runs)
    key = str(record.run_id)
    run = runs.get(key)
    if run is None:
        if record.event != _EVENT_CLAIMED or len(runs) >= _MAX_RUNS:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
        run = _RunState(
            run_id=record.run_id,
            plan_sha256=record.plan_sha256,
            receipts=(),
            pending=None,
        )
    elif run.plan_sha256 != record.plan_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PLAN_CONFLICT")
    expected_phase = len(run.receipts) + 1
    if record.event == _EVENT_CLAIMED:
        if run.pending is not None or record.phase_sequence != expected_phase:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
        next_run = _RunState(
            run_id=run.run_id,
            plan_sha256=run.plan_sha256,
            receipts=run.receipts,
            pending=_Pending(event=_EVENT_CLAIMED, record=record),
        )
    elif record.event == _EVENT_EFFECT_STARTED:
        if (
            run.pending is None
            or run.pending.event != _EVENT_CLAIMED
            or not _same_phase(
                run.pending.record,
                run_id=record.run_id,
                plan_sha256=record.plan_sha256,
                phase_sequence=record.phase_sequence,
                phase_request_sha256=record.phase_request_sha256,
                effect_key=record.effect_key,
                claim_id=record.claim_id,
            )
            or record.receipt is not None
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
        next_run = _RunState(
            run_id=run.run_id,
            plan_sha256=run.plan_sha256,
            receipts=run.receipts,
            pending=_Pending(event=_EVENT_EFFECT_STARTED, record=record),
        )
    else:
        if (
            run.pending is None
            or run.pending.event != _EVENT_EFFECT_STARTED
            or not _same_phase(
                run.pending.record,
                run_id=record.run_id,
                plan_sha256=record.plan_sha256,
                phase_sequence=record.phase_sequence,
                phase_request_sha256=record.phase_request_sha256,
                effect_key=record.effect_key,
                claim_id=record.claim_id,
            )
            or record.receipt is None
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
        try:
            receipt = _driver.parse_physical_full_matrix_v4_run_receipt(record.receipt)
        except Exception as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(
                "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID"
            ) from exc
        previous = _ZERO_SHA256
        if run.receipts:
            try:
                previous = _driver.parse_physical_full_matrix_v4_run_receipt(
                    run.receipts[-1]
                ).receipt_sha256
            except Exception as exc:
                raise PhysicalFullMatrixV4ReceiptJournalError(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_INVALID"
                ) from exc
        if (
            receipt.run_id != record.run_id
            or receipt.plan_sha256 != record.plan_sha256
            or receipt.sequence != record.phase_sequence
            or receipt.phase_request_sha256 != record.phase_request_sha256
            or receipt.effect_key != record.effect_key
            or receipt.previous_receipt_sha256 != previous
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_RECEIPT_MISMATCH")
        next_run = _RunState(
            run_id=run.run_id,
            plan_sha256=run.plan_sha256,
            receipts=run.receipts + (record.receipt,),
            pending=None,
        )
    runs[key] = next_run
    return _State(
        records=state.records + (record,),
        runs=runs,
        local_head_sha256=record.record_sha256,
        anchor_sequence=record.anchor_sequence,
        anchor_head_sha256=record.anchor_head_sha256,
        clock_floor=record.clock_floor,
    )


def _read_state(storage: _OpenStorage, *, facts: _Facts) -> _State:
    _init_binding(storage, facts=facts)
    state = _State(
        records=(),
        runs={},
        local_head_sha256=_ZERO_SHA256,
        anchor_sequence=facts.anchor_genesis_sequence,
        anchor_head_sha256=facts.anchor_genesis_head_sha256,
        clock_floor=None,
    )
    for record in _load_records(storage):
        # The fixed root belongs to exactly one typed V4 campaign.  Ignoring
        # a foreign record would let a seeded or rolled-back local root look
        # like this campaign's empty sequence-zero state.
        if (
            record.run_id != facts.campaign_binding.run_id
            or record.plan_sha256 != facts.campaign_binding.plan_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_FOREIGN_CAMPAIGN_RECORD")
        state = _apply_record(state=state, record=record)
    return state


def _record_payload(value: _Record) -> bytes:
    return _canonical(
        _record_mapping(value),
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_INVALID",
    )


def _append_record(storage: _OpenStorage, *, state: _State, record: _Record, facts: _Facts) -> _State:
    expected = _apply_record(state=state, record=record)
    filename = f"{record.sequence:020d}-{record.record_sha256}.json"
    _write_create_only_at(
        storage.records_fd,
        filename,
        _record_payload(record),
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_WRITE_FAILED",
    )
    reread = _read_state(storage, facts=facts)
    if (
        reread.local_head_sha256 != expected.local_head_sha256
        or reread.anchor_sequence != expected.anchor_sequence
        or reread.anchor_head_sha256 != expected.anchor_head_sha256
        or reread.records != expected.records
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECORD_NOT_DURABLE")
    return reread


def _clock_now(
    *,
    clock: PhysicalFullMatrixV4ReceiptJournalClock,
    floor: datetime | None,
) -> datetime:
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_REQUIRED")
    try:
        result = _utc(
            callback(),
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_INVALID",
        )
    except PhysicalFullMatrixV4ReceiptJournalError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_FAILED"
        ) from exc
    if floor is not None and result < floor:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLOCK_ROLLBACK")
    return result


def _campaign_initial_snapshot(
    value: object,
) -> object:
    """Validate the full initial V4 writer binding without importing V1/V3."""

    if type(value) is not _driver.PhysicalFullMatrixV4ExecutionBinding:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    try:
        # The campaign baseline must be the normal FI-writer direction.  V4
        # successors are derived later from anchored completion records.
        return _driver._snapshot_binding(
            value,
            direction=("webapp_fi", "webapp_ir"),
        )
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID"
        ) from exc


def _binding_from_snapshot(value: object) -> _driver.PhysicalFullMatrixV4ExecutionBinding:
    if type(value) is not _driver._BindingSnapshot:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    return _driver.PhysicalFullMatrixV4ExecutionBinding(**value.__dict__)


def derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
    *,
    run_id: UUID,
    plan_sha256: str,
    initial_active_binding: _driver.PhysicalFullMatrixV4ExecutionBinding,
) -> str:
    """Derive the public, typed V4 baseline pin for a rehydration admission.

    This deliberately accepts the complete normal-direction initial binding,
    rather than an already-calculated or caller-supplied opaque digest.  It is
    safe for configuration/re-hydration code to use and does not open state,
    contact an anchor, mint a continuity capability, or authorize an effect.
    """

    checked_run = _run_id(
        run_id,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_BINDING_INVALID",
    )
    checked_plan = _sha256(
        plan_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_BINDING_INVALID",
    )
    try:
        snapshot = _campaign_initial_snapshot(initial_active_binding)
    except PhysicalFullMatrixV4ReceiptJournalError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_BINDING_INVALID"
        ) from exc
    try:
        return _wire.derive_physical_full_matrix_v4_witness_anchor_baseline_plan_binding_sha256(
            run_id=checked_run,
            plan_sha256=checked_plan,
            initial_active_binding=dict(snapshot.__dict__),
        )
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_BASELINE_BINDING_INVALID"
        ) from exc


def _baseline_plan_binding_sha256(
    value: PhysicalFullMatrixV4ReceiptJournalCampaignBinding,
) -> str:
    return derive_physical_full_matrix_v4_receipt_journal_baseline_plan_binding_sha256(
        run_id=value.run_id,
        plan_sha256=value.plan_sha256,
        initial_active_binding=value.initial_active_binding,
    )


def _campaign_binding_body(
    value: PhysicalFullMatrixV4ReceiptJournalCampaignBinding,
) -> dict[str, object]:
    snapshot = _campaign_initial_snapshot(value.initial_active_binding)
    return {
        "schema": value.schema,
        "run_id": str(value.run_id),
        "plan_sha256": value.plan_sha256,
        "initial_active_binding": dict(snapshot.__dict__),
        "baseline_plan_binding_sha256": _baseline_plan_binding_sha256(value),
        "anchor_genesis_sequence": value.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": value.anchor_genesis_head_sha256,
        "execution_authorized": value.execution_authorized,
        "promotion_authorized": value.promotion_authorized,
        "full_matrix_executed": value.full_matrix_executed,
    }


def _campaign_binding_sha256(
    value: PhysicalFullMatrixV4ReceiptJournalCampaignBinding,
) -> str:
    return _sha256_bytes(
        _canonical(
            _campaign_binding_body(value),
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID",
        )
    )


def derive_physical_full_matrix_v4_receipt_journal_campaign_binding_sha256(
    *,
    campaign_binding: PhysicalFullMatrixV4ReceiptJournalCampaignBinding,
) -> str:
    """Return the public typed campaign pin required by journal config.

    Like the baseline helper, this is deterministic and non-authorizing.  It
    exists so production configuration and rehydrators never need a private
    journal helper to construct the exact config/anchor binding.
    """

    return _campaign_binding_sha256(_campaign_binding(campaign_binding))


def _campaign_binding(
    value: object,
) -> PhysicalFullMatrixV4ReceiptJournalCampaignBinding:
    if type(value) is not PhysicalFullMatrixV4ReceiptJournalCampaignBinding:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_SCHEMA
        or value.execution_authorized is not False
        or value.promotion_authorized is not False
        or value.full_matrix_executed is not False
        or type(value.anchor_genesis_sequence) is not int
        or value.anchor_genesis_sequence < 0
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    _run_id(value.run_id, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    _sha256(value.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID")
    _sha256(
        value.anchor_genesis_head_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_INVALID",
        permit_zero=True,
    )
    _campaign_initial_snapshot(value.initial_active_binding)
    # Force a canonical representation now so a later opaque mutable object
    # cannot substitute only a digest for the actual baseline pins.
    _campaign_binding_body(value)
    return value


def _record_for(
    *,
    state: _State,
    event: str,
    occurred_at: datetime,
    clock_floor: datetime,
    run_id: UUID,
    plan_sha256: str,
    phase_sequence: int,
    phase_request_sha256: str,
    effect_key: str,
    claim_id: str,
    receipt: bytes | None,
    anchor_sequence: int,
    anchor_head_sha256: str,
    anchor_commitment_sha256: str | None,
    anchor_attestation_sha256: str | None,
    anchor_event_sha256: str | None,
) -> _Record:
    receipt_sha = None if receipt is None else _sha256_bytes(receipt)
    local_event_sha = _event_sha256(
        event=event,
        occurred_at=occurred_at,
        clock_floor=clock_floor,
        run_id=run_id,
        plan_sha256=plan_sha256,
        phase_sequence=phase_sequence,
        phase_request_sha256=phase_request_sha256,
        effect_key=effect_key,
        claim_id=claim_id,
        previous_record_sha256=state.local_head_sha256,
        previous_anchor_sequence=state.anchor_sequence,
        previous_anchor_head_sha256=state.anchor_head_sha256,
        receipt_sha256=receipt_sha,
    )
    provisional = _Record(
        sequence=len(state.records) + 1,
        previous_record_sha256=state.local_head_sha256,
        event=event,
        occurred_at=occurred_at,
        clock_floor=clock_floor,
        run_id=run_id,
        plan_sha256=plan_sha256,
        phase_sequence=phase_sequence,
        phase_request_sha256=phase_request_sha256,
        effect_key=effect_key,
        claim_id=claim_id,
        receipt=receipt,
        receipt_sha256=receipt_sha,
        anchor_previous_sequence=state.anchor_sequence,
        anchor_previous_head_sha256=state.anchor_head_sha256,
        anchor_sequence=anchor_sequence,
        anchor_head_sha256=anchor_head_sha256,
        anchor_commitment_sha256=anchor_commitment_sha256,
        anchor_attestation_sha256=anchor_attestation_sha256,
        anchor_event_sha256=anchor_event_sha256,
        local_event_sha256=local_event_sha,
        record_sha256="",
    )
    return _Record(
        **{
            **provisional.__dict__,
            "record_sha256": _record_digest(provisional),
        }
    )


def _commitment_for(
    *,
    facts: _Facts,
    state: _State,
    event: str,
    now: datetime,
    run_id: UUID,
    plan_sha256: str,
    phase_sequence: int,
    phase_request_sha256: str,
    effect_key: str,
    claim_id: str,
    receipt: bytes | None,
) -> PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
    receipt_sha = None if receipt is None else _sha256_bytes(receipt)
    local_event_sha = _event_sha256(
        event=event,
        occurred_at=now,
        clock_floor=now,
        run_id=run_id,
        plan_sha256=plan_sha256,
        phase_sequence=phase_sequence,
        phase_request_sha256=phase_request_sha256,
        effect_key=effect_key,
        claim_id=claim_id,
        previous_record_sha256=state.local_head_sha256,
        previous_anchor_sequence=state.anchor_sequence,
        previous_anchor_head_sha256=state.anchor_head_sha256,
        receipt_sha256=receipt_sha,
    )
    return PhysicalFullMatrixV4WitnessJournalAnchorCommitment(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA,
        journal_binding_sha256=facts.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
        anchor_genesis_sequence=facts.anchor_genesis_sequence,
        anchor_genesis_head_sha256=facts.anchor_genesis_head_sha256,
        event=event,
        run_id=run_id,
        plan_sha256=plan_sha256,
        phase_sequence=phase_sequence,
        phase_request_sha256=phase_request_sha256,
        effect_key=effect_key,
        claim_id=claim_id,
        previous_anchor_sequence=state.anchor_sequence,
        previous_anchor_head_sha256=state.anchor_head_sha256,
        local_previous_record_sha256=state.local_head_sha256,
        local_event_sha256=local_event_sha,
        receipt_sha256=receipt_sha,
        occurred_at=now,
    )


def _pending_matches(
    *,
    state: _State,
    head: PhysicalFullMatrixV4WitnessJournalAnchorHead,
    facts: _Facts,
) -> PhysicalFullMatrixV4WitnessJournalAnchorCommitment | None:
    if (
        head.sequence != state.anchor_sequence + 1
        or head.previous_head_sha256 != state.anchor_head_sha256
        or head.commitment is None
    ):
        return None
    commitment = _check_commitment(head.commitment, facts=facts)
    if (
        commitment.previous_anchor_sequence != state.anchor_sequence
        or commitment.previous_anchor_head_sha256 != state.anchor_head_sha256
        or commitment.local_previous_record_sha256 != state.local_head_sha256
        or head.commitment_sha256 != _commitment_sha256(commitment)
    ):
        return None
    run = state.runs.get(str(commitment.run_id))
    if run is None or run.plan_sha256 != commitment.plan_sha256 or run.pending is None:
        return None
    expected_pending = _EVENT_CLAIMED if commitment.event == _EVENT_EFFECT_STARTED else _EVENT_EFFECT_STARTED
    pending = run.pending.record
    if (
        run.pending.event != expected_pending
        or not _same_phase(
            pending,
            run_id=commitment.run_id,
            plan_sha256=commitment.plan_sha256,
            phase_sequence=commitment.phase_sequence,
            phase_request_sha256=commitment.phase_request_sha256,
            effect_key=commitment.effect_key,
            claim_id=commitment.claim_id,
        )
    ):
        return None
    expected = _event_sha256(
        event=commitment.event,
        occurred_at=_utc(
            commitment.occurred_at,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        ),
        clock_floor=_utc(
            commitment.occurred_at,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_INVALID",
        ),
        run_id=commitment.run_id,
        plan_sha256=commitment.plan_sha256,
        phase_sequence=commitment.phase_sequence,
        phase_request_sha256=commitment.phase_request_sha256,
        effect_key=commitment.effect_key,
        claim_id=commitment.claim_id,
        previous_record_sha256=state.local_head_sha256,
        previous_anchor_sequence=state.anchor_sequence,
        previous_anchor_head_sha256=state.anchor_head_sha256,
        receipt_sha256=commitment.receipt_sha256,
    )
    return commitment if commitment.local_event_sha256 == expected else None


def _require_anchor_head_matches_local_tail(
    *,
    state: _State,
    head: PhysicalFullMatrixV4WitnessJournalAnchorHead,
    facts: _Facts,
) -> None:
    """Bind the current external head to the last local anchored event.

    The anchor boundary is responsible for authenticating its own head.  This
    additional check prevents the local journal from accepting a typed,
    self-consistent but *different* current commitment under the same remote
    head identity.  A later local CLAIMED record intentionally does not alter
    the external tail, so search backwards for the last anchored record.
    """

    anchored = next(
        (record for record in reversed(state.records) if record.event in _ANCHOR_EVENTS),
        None,
    )
    if anchored is None:
        if (
            state.anchor_sequence != facts.anchor_genesis_sequence
            or state.anchor_head_sha256 != facts.anchor_genesis_head_sha256
            or head.sequence != facts.anchor_genesis_sequence
            or head.head_sha256 != facts.anchor_genesis_head_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_LOCAL_MISMATCH")
        return
    commitment = _check_commitment(head.commitment, facts=facts)
    if (
        head.sequence != anchored.anchor_sequence
        or head.head_sha256 != anchored.anchor_head_sha256
        or head.previous_head_sha256 != anchored.anchor_previous_head_sha256
        or head.commitment_sha256 != anchored.anchor_commitment_sha256
        or head.attestation_sha256 != anchored.anchor_attestation_sha256
        or commitment.event != anchored.event
        or commitment.run_id != anchored.run_id
        or commitment.plan_sha256 != anchored.plan_sha256
        or commitment.phase_sequence != anchored.phase_sequence
        or commitment.phase_request_sha256 != anchored.phase_request_sha256
        or commitment.effect_key != anchored.effect_key
        or commitment.claim_id != anchored.claim_id
        or commitment.previous_anchor_sequence != anchored.anchor_previous_sequence
        or commitment.previous_anchor_head_sha256 != anchored.anchor_previous_head_sha256
        or commitment.local_previous_record_sha256 != anchored.previous_record_sha256
        or commitment.local_event_sha256 != anchored.anchor_event_sha256
        or commitment.receipt_sha256 != anchored.receipt_sha256
        or commitment.occurred_at != anchored.occurred_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_LOCAL_MISMATCH")


def _anchor_status(
    *,
    state: _State,
    anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
    facts: _Facts,
) -> _AnchorStatus:
    head = _anchor_head(
        anchor=anchor,
        facts=facts,
        expected_anchor_sequence=state.anchor_sequence,
        expected_anchor_head_sha256=state.anchor_head_sha256,
    )
    if head.sequence == state.anchor_sequence and head.head_sha256 == state.anchor_head_sha256:
        _require_anchor_head_matches_local_tail(state=state, head=head, facts=facts)
        return _AnchorStatus(head=head, pending=None)
    pending = _pending_matches(state=state, head=head, facts=facts)
    if pending is not None:
        return _AnchorStatus(head=head, pending=pending)
    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_ROLLBACK_OR_DIVERGENCE")


def _observe_anchor(
    *,
    state: _State,
    anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
    facts: _Facts,
    clock: PhysicalFullMatrixV4ReceiptJournalClock,
    floor: datetime | None,
) -> tuple[_AnchorStatus, datetime]:
    """Read the external head and take a fresh trusted sample afterwards."""

    status = _anchor_status(state=state, anchor=anchor, facts=facts)
    return status, _clock_now(clock=clock, floor=floor)


def _verify_anchor_append_durable(
    *,
    anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
    facts: _Facts,
    receipt: PhysicalFullMatrixV4WitnessJournalAnchorReceipt,
    commitment: PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
    clock: PhysicalFullMatrixV4ReceiptJournalClock,
    floor: datetime,
) -> tuple[datetime, PhysicalFullMatrixV4WitnessJournalAnchorHead]:
    """Never trust a callback return alone as the durable external head.

    The verified head is returned only to retain a narrow in-process start
    projection after the caller has also written the create-only local record.
    It is not persisted as a raw transport artifact and this helper performs
    no provider/host action beyond the injected anchor's already-required
    readback.
    """

    after_append = _clock_now(clock=clock, floor=floor)
    head = _anchor_head(
        anchor=anchor,
        facts=facts,
        expected_anchor_sequence=receipt.sequence,
        expected_anchor_head_sha256=receipt.head_sha256,
    )
    after_read = _clock_now(clock=clock, floor=after_append)
    if (
        head.sequence != receipt.sequence
        or head.previous_head_sha256 != receipt.previous_head_sha256
        or head.head_sha256 != receipt.head_sha256
        or head.commitment_sha256 != receipt.commitment_sha256
        or head.attestation_sha256 != receipt.attestation_sha256
        or head.commitment != commitment
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_APPEND_NOT_DURABLE")
    return after_read, head


def _facts(value: object) -> _Facts:
    if type(value) is not RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_SCHEMA
        or value.enabled is not True
        or value.journal_mode != _MODE
        or type(value.anchor_genesis_sequence) is not int
        or value.anchor_genesis_sequence < 0
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID")
    _require_root()
    campaign = _campaign_binding(value.campaign_binding)
    configured_binding_sha256 = _sha256(
        value.journal_binding_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID",
    )
    if (
        configured_binding_sha256 != _campaign_binding_sha256(campaign)
        or value.anchor_genesis_head_sha256 != campaign.anchor_genesis_head_sha256
        or value.anchor_genesis_sequence != campaign.anchor_genesis_sequence
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID")
    return _Facts(
        journal_binding_sha256=configured_binding_sha256,
        campaign_binding=campaign,
        baseline_plan_binding_sha256=_baseline_plan_binding_sha256(campaign),
        anchor_genesis_head_sha256=_sha256(
            value.anchor_genesis_head_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONFIG_INVALID",
            permit_zero=True,
        ),
        anchor_genesis_sequence=value.anchor_genesis_sequence,
    )


def _make_claim_id(existing: set[str]) -> str:
    for _ in range(8):
        try:
            candidate = "pfm-v4-witness-journal-" + secrets.token_urlsafe(24)
        except Exception as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(
                "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_ID_FAILED"
            ) from exc
        if _CLAIM_ID_RE.fullmatch(candidate) is not None and candidate not in existing:
            return candidate
    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_ID_FAILED")


def _claim_input(
    *,
    run_id: object,
    plan_sha256: object,
    sequence: object,
    phase_request_sha256: object,
    effect_key: object,
) -> tuple[UUID, str, int, str, str]:
    return (
        _run_id(run_id, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID"),
        _sha256(plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID"),
        _phase_sequence(sequence, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID"),
        _sha256(
            phase_request_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID",
        ),
        _sha256(effect_key, code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_INPUT_INVALID"),
    )


def _require_campaign_run(
    *,
    facts: _Facts,
    run_id: UUID,
    plan_sha256: str | None = None,
) -> None:
    campaign = facts.campaign_binding
    if run_id != campaign.run_id or (plan_sha256 is not None and plan_sha256 != campaign.plan_sha256):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CAMPAIGN_BINDING_MISMATCH")


def _anchored_active_binding(
    *,
    facts: _Facts,
    run: _RunState | None,
    now: datetime,
) -> tuple[int, object]:
    """Derive the active V4 binding only from anchor-backed local records.

    Receipt bytes are parsed here solely after the create-only record chain and
    the *current* external Witness head have already been verified by the
    caller.  No caller-supplied raw receipt can enter this calculation.
    """

    initial = _campaign_initial_snapshot(facts.campaign_binding.initial_active_binding)
    receipts = () if run is None else run.receipts
    try:
        snapshot = _driver._PlanSnapshot(
            canonical_plan=b"",
            plan_sha256=facts.campaign_binding.plan_sha256,
            run_id=facts.campaign_binding.run_id,
            binding=initial,
            phases=_driver._phase_snapshots(),
            maximum_oracle_age_seconds=1,
        )
        verified, active = _driver._validate_receipt_chain(
            snapshot=snapshot,
            raw_receipts=receipts,
            now=now,
        )
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_CHAIN_INVALID"
        ) from exc
    if len(verified) != len(receipts):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_CHAIN_INVALID")
    return len(verified), active


def _mint_continuity(
    *,
    facts: _Facts,
    completed_sequence: int,
    active_snapshot: object,
    anchor_sequence: int,
    anchor_head_sha256: str,
) -> VerifiedPhysicalFullMatrixV4CampaignContinuity:
    active = _binding_from_snapshot(active_snapshot)
    result = VerifiedPhysicalFullMatrixV4CampaignContinuity(
        run_id=facts.campaign_binding.run_id,
        plan_sha256=facts.campaign_binding.plan_sha256,
        completed_sequence=completed_sequence,
        active_binding=active,
        journal_binding_sha256=facts.journal_binding_sha256,
        baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
        anchor_sequence=anchor_sequence,
        anchor_head_sha256=anchor_head_sha256,
    )
    object.__setattr__(result, "_capability", _CONTINUITY_CAPABILITY)
    _CONTINUITY_STATES[result] = _ContinuityState(
        run_id=result.run_id,
        plan_sha256=result.plan_sha256,
        completed_sequence=result.completed_sequence,
        active_binding_snapshot=active_snapshot,
        journal_binding_sha256=result.journal_binding_sha256,
        baseline_plan_binding_sha256=result.baseline_plan_binding_sha256,
        anchor_sequence=result.anchor_sequence,
        anchor_head_sha256=result.anchor_head_sha256,
    )
    return result


def require_verified_physical_full_matrix_v4_campaign_continuity(
    value: object,
) -> VerifiedPhysicalFullMatrixV4CampaignContinuity:
    """Require the nonserializable projection minted by this V4 journal."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV4CampaignContinuity
        or value._capability is not _CONTINUITY_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_PROVENANCE_INVALID")
    state = _CONTINUITY_STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_PROVENANCE_INVALID")
    try:
        active = _driver._snapshot_binding(value.active_binding, direction=None)
    except Exception as exc:
        raise PhysicalFullMatrixV4ReceiptJournalError(
            "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_PROVENANCE_INVALID"
        ) from exc
    if (
        value.run_id != state.run_id
        or value.plan_sha256 != state.plan_sha256
        or value.completed_sequence != state.completed_sequence
        or active != state.active_binding_snapshot
        or value.journal_binding_sha256 != state.journal_binding_sha256
        or value.baseline_plan_binding_sha256 != state.baseline_plan_binding_sha256
        or value.anchor_sequence != state.anchor_sequence
        or value.anchor_head_sha256 != state.anchor_head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_TAMPERED")
    return value

class RootOwnedPhysicalFullMatrixV4ReceiptJournal:
    """Concrete V4-only driver journal with a mandatory Witness anchor.

    The construction itself is inert.  Every method rechecks default-off,
    root, fixed-root, clock, and Witness state.  The in-memory identities only
    gate the hand-off from one live driver call to the next; durable records
    remain the restart evidence and never authorize an adapter retry.
    """

    def __init__(
        self,
        config: RootOwnedPhysicalFullMatrixV4ReceiptJournalConfig,
        *,
        witness_anchor: PhysicalFullMatrixV4WitnessJournalAnchor,
        trusted_clock: PhysicalFullMatrixV4ReceiptJournalClock,
    ) -> None:
        self._config = config
        self._witness_anchor = witness_anchor
        self._trusted_clock = trusted_clock
        self._live_claims: dict[str, _driver.PhysicalFullMatrixV4PhaseClaim] = {}
        self._live_effect_starts: dict[str, _driver.PhysicalFullMatrixV4EffectStart] = {}
        self._live_effect_start_anchors: dict[str, _LiveEffectStartAnchor] = {}

    def read_receipts(self, *, run_id: UUID) -> Sequence[bytes]:
        facts = _facts(self._config)
        checked_run_id = _run_id(
            run_id,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_ID_INVALID",
        )
        _require_campaign_run(facts=facts, run_id=checked_run_id)
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(clock=self._trusted_clock, floor=state.clock_floor)
            status, _after_anchor = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_PENDING")
            run = state.runs.get(str(checked_run_id))
            # A CLAIMED record has not crossed an irreversible boundary and
            # may be re-issued by claim_phase after a process restart.  Once
            # EFFECT_STARTED is durable, however, returning the older receipt
            # prefix to a caller would obscure an indeterminate real-world
            # effect.  Stop before any caller can treat that prefix as a
            # restart permit.
            if run is not None and run.pending is not None:
                if run.pending.event == _EVENT_EFFECT_STARTED:
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_INDETERMINATE")
                if run.pending.event != _EVENT_CLAIMED:
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
            return () if run is None else tuple(run.receipts)

    def verify_campaign_continuity(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        completed_sequence: int,
        active_binding: _driver.PhysicalFullMatrixV4ExecutionBinding,
    ) -> VerifiedPhysicalFullMatrixV4CampaignContinuity:
        """Prove a V4 campaign point from the external head plus local chain.

        This is intentionally stronger than parsing the receipt list.  It
        requires the typed campaign baseline and immutable Witness genesis at
        sequence zero, and requires every later local completion chain to end
        at the externally-attested current head with no pending transition.
        """

        facts = _facts(self._config)
        checked_run = _run_id(
            run_id,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_INPUT_INVALID",
        )
        checked_plan = _sha256(
            plan_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_INPUT_INVALID",
        )
        if (
            type(completed_sequence) is not int
            or not 0 <= completed_sequence <= len(_driver.PHYSICAL_FULL_MATRIX_V4_PHASES)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_INPUT_INVALID")
        _require_campaign_run(
            facts=facts,
            run_id=checked_run,
            plan_sha256=checked_plan,
        )
        try:
            observed_active = _driver._snapshot_binding(active_binding, direction=None)
        except Exception as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(
                "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_INPUT_INVALID"
            ) from exc
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(clock=self._trusted_clock, floor=state.clock_floor)
            status, now = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_ANCHOR_PENDING")
            run = state.runs.get(str(checked_run))
            if run is not None and run.plan_sha256 != checked_plan:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_PLAN_CONFLICT")
            if run is not None and run.pending is not None:
                # A durable CLAIMED state has no external effect commitment,
                # so it is safe for the same campaign to re-claim the next
                # phase.  EFFECT_STARTED is categorically different: it may
                # already have produced an irreversible effect and can only
                # be reconciled out of band.
                if run.pending.event == _EVENT_EFFECT_STARTED:
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_INCOMPLETE")
                if run.pending.event != _EVENT_CLAIMED:
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RUN_STATE_INVALID")
            derived_sequence, derived_active = _anchored_active_binding(
                facts=facts,
                run=run,
                now=now,
            )
            if (
                derived_sequence != completed_sequence
                or derived_active != observed_active
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CONTINUITY_MISMATCH")
            return _mint_continuity(
                facts=facts,
                completed_sequence=derived_sequence,
                active_snapshot=derived_active,
                anchor_sequence=status.head.sequence,
                anchor_head_sha256=status.head.head_sha256,
            )

    def claim_phase(
        self,
        *,
        run_id: UUID,
        plan_sha256: str,
        sequence: int,
        phase_request_sha256: str,
        effect_key: str,
    ) -> _driver.PhysicalFullMatrixV4PhaseClaim:
        facts = _facts(self._config)
        checked_run, checked_plan, checked_sequence, checked_request, checked_effect = _claim_input(
            run_id=run_id,
            plan_sha256=plan_sha256,
            sequence=sequence,
            phase_request_sha256=phase_request_sha256,
            effect_key=effect_key,
        )
        _require_campaign_run(
            facts=facts,
            run_id=checked_run,
            plan_sha256=checked_plan,
        )
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(clock=self._trusted_clock, floor=state.clock_floor)
            status, now = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                pending = status.pending
                if (
                    pending.run_id == checked_run
                    and pending.plan_sha256 == checked_plan
                    and pending.phase_sequence == checked_sequence
                    and pending.phase_request_sha256 == checked_request
                    and pending.effect_key == checked_effect
                ):
                    return _driver.PhysicalFullMatrixV4PhaseClaim(
                        run_id=checked_run,
                        plan_sha256=checked_plan,
                        sequence=checked_sequence,
                        phase_request_sha256=checked_request,
                        effect_key=checked_effect,
                        indeterminate=True,
                    )
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_PENDING")
            run = state.runs.get(str(checked_run))
            if run is None:
                run = _RunState(
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    receipts=(),
                    pending=None,
                )
            elif run.plan_sha256 != checked_plan:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PLAN_CONFLICT")
            if checked_sequence <= len(run.receipts):
                stored = run.receipts[checked_sequence - 1]
                try:
                    receipt = _driver.parse_physical_full_matrix_v4_run_receipt(stored)
                except Exception as exc:
                    raise PhysicalFullMatrixV4ReceiptJournalError(
                        "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECEIPT_INVALID"
                    ) from exc
                if (
                    receipt.phase_request_sha256 != checked_request
                    or receipt.effect_key != checked_effect
                ):
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_CONFLICT")
                return _driver.PhysicalFullMatrixV4PhaseClaim(
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                    existing_receipt=stored,
                )
            if checked_sequence != len(run.receipts) + 1:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_SEQUENCE_INVALID")
            if run.pending is not None:
                pending = run.pending
                if not _same_phase(
                    pending.record,
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    phase_sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                ):
                    _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PENDING_CLAIM_CONFLICT")
                if pending.event == _EVENT_EFFECT_STARTED:
                    return _driver.PhysicalFullMatrixV4PhaseClaim(
                        run_id=checked_run,
                        plan_sha256=checked_plan,
                        sequence=checked_sequence,
                        phase_request_sha256=checked_request,
                        effect_key=checked_effect,
                        indeterminate=True,
                    )
                claim = _driver.PhysicalFullMatrixV4PhaseClaim(
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                    claim_id=pending.record.claim_id,
                )
                self._live_claims[claim.claim_id] = claim  # type: ignore[index]
                return claim
            existing_claim_ids = {
                item.pending.record.claim_id
                for item in state.runs.values()
                if item.pending is not None
            }
            claim_id = _make_claim_id(existing_claim_ids)
            record = _record_for(
                state=state,
                event=_EVENT_CLAIMED,
                occurred_at=now,
                clock_floor=now,
                run_id=checked_run,
                plan_sha256=checked_plan,
                phase_sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=claim_id,
                receipt=None,
                anchor_sequence=state.anchor_sequence,
                anchor_head_sha256=state.anchor_head_sha256,
                anchor_commitment_sha256=None,
                anchor_attestation_sha256=None,
                anchor_event_sha256=None,
            )
            _append_record(storage, state=state, record=record, facts=facts)
            claim = _driver.PhysicalFullMatrixV4PhaseClaim(
                run_id=checked_run,
                plan_sha256=checked_plan,
                sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=claim_id,
            )
            self._live_claims[claim_id] = claim
            return claim

    def mark_effect_started(
        self,
        *,
        claim: _driver.PhysicalFullMatrixV4PhaseClaim,
        effect_key: str,
    ) -> _driver.PhysicalFullMatrixV4EffectStart:
        facts = _facts(self._config)
        if (
            type(claim) is not _driver.PhysicalFullMatrixV4PhaseClaim
            or claim.claim_id is None
            or claim.existing_receipt is not None
            or claim.indeterminate is not False
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
        checked_run, checked_plan, checked_sequence, checked_request, checked_effect = _claim_input(
            run_id=claim.run_id,
            plan_sha256=claim.plan_sha256,
            sequence=claim.sequence,
            phase_request_sha256=claim.phase_request_sha256,
            effect_key=effect_key,
        )
        _require_campaign_run(
            facts=facts,
            run_id=checked_run,
            plan_sha256=checked_plan,
        )
        checked_claim = _claim_id(
            claim.claim_id,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_NOT_LIVE",
        )
        if claim.effect_key != checked_effect or self._live_claims.get(checked_claim) is not claim:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(clock=self._trusted_clock, floor=state.clock_floor)
            status, before_append = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_PENDING")
            run = state.runs.get(str(checked_run))
            if (
                run is None
                or run.pending is None
                or run.pending.event != _EVENT_CLAIMED
                or not _same_phase(
                    run.pending.record,
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    phase_sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                    claim_id=checked_claim,
                )
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_NOT_PENDING")
            commitment = _commitment_for(
                facts=facts,
                state=state,
                event=_EVENT_EFFECT_STARTED,
                now=before_append,
                run_id=checked_run,
                plan_sha256=checked_plan,
                phase_sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=checked_claim,
                receipt=None,
            )
            receipt = _anchor_append(
                anchor=self._witness_anchor,
                facts=facts,
                before=status.head,
                commitment=commitment,
            )
            after_anchor, durable_head = _verify_anchor_append_durable(
                anchor=self._witness_anchor,
                facts=facts,
                receipt=receipt,
                commitment=commitment,
                clock=self._trusted_clock,
                floor=before_append,
            )
            # The Witness commitment is intentionally before the local record.
            # A crash here becomes anchor-pending and cannot be retried.
            record = _record_for(
                state=state,
                event=_EVENT_EFFECT_STARTED,
                occurred_at=commitment.occurred_at,
                clock_floor=after_anchor,
                run_id=checked_run,
                plan_sha256=checked_plan,
                phase_sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=checked_claim,
                receipt=None,
                anchor_sequence=receipt.sequence,
                anchor_head_sha256=receipt.head_sha256,
                anchor_commitment_sha256=receipt.commitment_sha256,
                anchor_attestation_sha256=receipt.attestation_sha256,
                anchor_event_sha256=commitment.local_event_sha256,
            )
            _append_record(storage, state=state, record=record, facts=facts)
            start = _driver.PhysicalFullMatrixV4EffectStart(
                run_id=checked_run,
                plan_sha256=checked_plan,
                sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=checked_claim,
            )
            self._live_effect_starts[checked_claim] = start
            self._live_effect_start_anchors[checked_claim] = _LiveEffectStartAnchor(
                effect_start=start,
                record=record,
                commitment=commitment,
                durable_head=durable_head,
            )
            try:
                del self._live_claims[checked_claim]
            except KeyError:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_CLAIM_NOT_LIVE")
            return start

    def project_effect_start_anchor_proof(
        self,
        *,
        effect_start: _driver.PhysicalFullMatrixV4EffectStart,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    ) -> _driver.PhysicalFullMatrixV4EffectStartAnchorProof:
        """Project one already-verified effect-start into an adapter request.

        This method is deliberately process-local and inert: it performs no
        file open, anchor read, transport, provider, host, or phase action.
        It merely cross-checks the exact post-append/readback facts retained
        by :meth:`mark_effect_started` and mints a driver-owned opaque proof.
        Therefore a restart, a pre-effect request, an uncommitted claim, or a
        post-completion effect start cannot recreate a proof.
        """

        facts = _facts(self._config)
        if type(effect_start) is not _driver.PhysicalFullMatrixV4EffectStart:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_ANCHOR_PROOF_NOT_LIVE")
        try:
            checked_claim = _claim_id(
                effect_start.claim_id,
                code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_ANCHOR_PROOF_NOT_LIVE",
            )
        except PhysicalFullMatrixV4ReceiptJournalError:
            raise
        live = self._live_effect_start_anchors.get(checked_claim)
        if (
            live is None
            or live.effect_start is not effect_start
            or self._live_effect_starts.get(checked_claim) is not effect_start
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_ANCHOR_PROOF_NOT_LIVE")
        record = live.record
        commitment = _check_commitment(live.commitment, facts=facts)
        durable_head = _check_anchor_head(live.durable_head, facts=facts)
        if (
            record.event != _EVENT_EFFECT_STARTED
            or record.receipt is not None
            or record.receipt_sha256 is not None
            or record.run_id != effect_start.run_id
            or record.plan_sha256 != effect_start.plan_sha256
            or record.phase_sequence != effect_start.sequence
            or record.phase_request_sha256 != effect_start.phase_request_sha256
            or record.effect_key != effect_start.effect_key
            or record.claim_id != effect_start.claim_id
            or commitment.event != _EVENT_EFFECT_STARTED
            or commitment.receipt_sha256 is not None
            or commitment.run_id != record.run_id
            or commitment.plan_sha256 != record.plan_sha256
            or commitment.phase_sequence != record.phase_sequence
            or commitment.phase_request_sha256 != record.phase_request_sha256
            or commitment.effect_key != record.effect_key
            or commitment.claim_id != record.claim_id
            or commitment.previous_anchor_sequence != record.anchor_previous_sequence
            or commitment.previous_anchor_head_sha256 != record.anchor_previous_head_sha256
            or commitment.local_previous_record_sha256 != record.previous_record_sha256
            or commitment.local_event_sha256 != record.anchor_event_sha256
            or commitment.occurred_at != record.occurred_at
            or durable_head.sequence != record.anchor_sequence
            or durable_head.head_sha256 != record.anchor_head_sha256
            or durable_head.previous_head_sha256 != record.anchor_previous_head_sha256
            or durable_head.commitment_sha256 != record.anchor_commitment_sha256
            or durable_head.attestation_sha256 != record.anchor_attestation_sha256
            or durable_head.commitment != commitment
            or record.anchor_commitment_sha256 != _commitment_sha256(commitment)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_ANCHOR_PROOF_MISMATCH")
        try:
            return _driver._mint_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request,
                effect_start=effect_start,
                journal_binding_sha256=facts.journal_binding_sha256,
                baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
                anchor_genesis_sequence=commitment.anchor_genesis_sequence,
                anchor_genesis_head_sha256=commitment.anchor_genesis_head_sha256,
                anchor_previous_sequence=commitment.previous_anchor_sequence,
                anchor_previous_head_sha256=commitment.previous_anchor_head_sha256,
                anchor_sequence=durable_head.sequence,
                anchor_head_sha256=durable_head.head_sha256,
                anchor_commitment_sha256=durable_head.commitment_sha256,
                anchor_attestation_sha256=durable_head.attestation_sha256,
                anchor_local_previous_record_sha256=(
                    commitment.local_previous_record_sha256
                ),
                anchor_local_event_sha256=commitment.local_event_sha256,
                anchor_occurred_at=commitment.occurred_at,
            )
        except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(
                "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_ANCHOR_PROOF_REQUEST_INVALID"
            ) from exc

    def project_predecessor_phase_completion_anchor_proof(
        self,
        *,
        effect_start: _driver.PhysicalFullMatrixV4EffectStart,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    ) -> _driver.PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof:
        """Project a durable predecessor completion for this successor start.

        Unlike the live start projection, this proof is deliberately derived
        from the durable record chain.  A process restart may therefore mint
        a fresh projection *only after* a new successor effect start has been
        journaled and the Witness's current external head has been re-read.
        The method never makes an adapter call and never treats a local cache
        or raw receipt as authority.
        """

        facts = _facts(self._config)
        code = "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_INVALID"
        if type(effect_start) is not _driver.PhysicalFullMatrixV4EffectStart:
            _fail(code)
        try:
            authority = _driver.require_physical_full_matrix_v4_effect_start_authority(
                request=request
            )
            successor_anchor = _driver.require_physical_full_matrix_v4_effect_start_anchor_proof(
                request=request
            )
        except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
        if (
            authority.phase.sequence <= 1
            or effect_start.run_id != authority.run_id
            or effect_start.plan_sha256 != authority.plan_sha256
            or effect_start.sequence != authority.phase.sequence
            or effect_start.phase_request_sha256 != authority.phase_request_sha256
            or effect_start.effect_key != authority.effect_key
            or effect_start.claim_id != authority.claim_id
        ):
            _fail(code)
        checked_run, checked_plan, checked_sequence, checked_request, checked_effect = _claim_input(
            run_id=effect_start.run_id,
            plan_sha256=effect_start.plan_sha256,
            sequence=effect_start.sequence,
            phase_request_sha256=effect_start.phase_request_sha256,
            effect_key=effect_start.effect_key,
        )
        checked_claim = _claim_id(effect_start.claim_id, code=code)
        _require_campaign_run(
            facts=facts,
            run_id=checked_run,
            plan_sha256=checked_plan,
        )
        predecessor_sequence = checked_sequence - 1
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(
                clock=self._trusted_clock,
                floor=state.clock_floor,
            )
            status, _after_anchor = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_NOT_CURRENT"
                )
            if not state.records:
                _fail(code)
            current_record = state.records[-1]
            if (
                current_record.event != _EVENT_EFFECT_STARTED
                or current_record.receipt is not None
                or current_record.receipt_sha256 is not None
                or not _same_phase(
                    current_record,
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    phase_sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                    claim_id=checked_claim,
                )
            ):
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_NOT_CURRENT"
                )
            current_commitment = _commitment_from_anchored_record(
                record=current_record,
                facts=facts,
                code=code,
            )
            if (
                successor_anchor.journal_binding_sha256
                != facts.journal_binding_sha256
                or successor_anchor.baseline_plan_binding_sha256
                != facts.baseline_plan_binding_sha256
                or successor_anchor.anchor_genesis_sequence
                != facts.anchor_genesis_sequence
                or successor_anchor.anchor_genesis_head_sha256
                != facts.anchor_genesis_head_sha256
                or successor_anchor.anchor_previous_sequence
                != current_record.anchor_previous_sequence
                or successor_anchor.anchor_previous_head_sha256
                != current_record.anchor_previous_head_sha256
                or successor_anchor.anchor_sequence != current_record.anchor_sequence
                or successor_anchor.anchor_head_sha256 != current_record.anchor_head_sha256
                or successor_anchor.anchor_commitment_sha256
                != current_record.anchor_commitment_sha256
                or successor_anchor.anchor_attestation_sha256
                != current_record.anchor_attestation_sha256
                or successor_anchor.anchor_local_previous_record_sha256
                != current_record.previous_record_sha256
                or successor_anchor.anchor_local_event_sha256
                != current_record.anchor_event_sha256
                or successor_anchor.anchor_occurred_at != current_record.occurred_at
                or status.head.sequence != current_record.anchor_sequence
                or status.head.head_sha256 != current_record.anchor_head_sha256
                or status.head.previous_head_sha256
                != current_record.anchor_previous_head_sha256
                or status.head.commitment_sha256
                != current_record.anchor_commitment_sha256
                or status.head.attestation_sha256
                != current_record.anchor_attestation_sha256
                or status.head.commitment != current_commitment
            ):
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_NOT_CURRENT"
                )
            predecessor_completion = next(
                (
                    record
                    for record in reversed(state.records[:-1])
                    if (
                        record.event == _EVENT_COMPLETED
                        and _same_phase(
                            record,
                            run_id=checked_run,
                            plan_sha256=checked_plan,
                            phase_sequence=predecessor_sequence,
                            phase_request_sha256=record.phase_request_sha256,
                            effect_key=record.effect_key,
                        )
                    )
                ),
                None,
            )
            if predecessor_completion is None:
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_UNAVAILABLE"
                )
            predecessor_start = next(
                (
                    record
                    for record in state.records
                    if record.record_sha256
                    == predecessor_completion.previous_record_sha256
                ),
                None,
            )
            if (
                predecessor_start is None
                or predecessor_start.event != _EVENT_EFFECT_STARTED
                or predecessor_start.receipt is not None
                or predecessor_start.receipt_sha256 is not None
                or predecessor_completion.receipt is None
                or predecessor_completion.receipt_sha256 is None
                or not _same_phase(
                    predecessor_start,
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    phase_sequence=predecessor_sequence,
                    phase_request_sha256=predecessor_completion.phase_request_sha256,
                    effect_key=predecessor_completion.effect_key,
                    claim_id=predecessor_completion.claim_id,
                )
                or predecessor_completion.anchor_previous_sequence
                != predecessor_start.anchor_sequence
                or predecessor_completion.anchor_previous_head_sha256
                != predecessor_start.anchor_head_sha256
                or predecessor_completion.anchor_sequence
                != predecessor_start.anchor_sequence + 1
                or current_record.anchor_previous_sequence
                != predecessor_completion.anchor_sequence
                or current_record.anchor_previous_head_sha256
                != predecessor_completion.anchor_head_sha256
            ):
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_DURABLE_MISMATCH"
                )
            predecessor_start_commitment = _commitment_from_anchored_record(
                record=predecessor_start,
                facts=facts,
                code=code,
            )
            predecessor_completion_commitment = _commitment_from_anchored_record(
                record=predecessor_completion,
                facts=facts,
                code=code,
            )
            try:
                parsed_receipt = _driver.parse_physical_full_matrix_v4_run_receipt(
                    predecessor_completion.receipt
                )
            except Exception as exc:
                raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc
            if (
                parsed_receipt.receipt_sha256 != predecessor_completion.receipt_sha256
                or parsed_receipt.run_id != predecessor_completion.run_id
                or parsed_receipt.plan_sha256 != predecessor_completion.plan_sha256
                or parsed_receipt.sequence != predecessor_completion.phase_sequence
                or parsed_receipt.phase_request_sha256
                != predecessor_completion.phase_request_sha256
                or parsed_receipt.effect_key != predecessor_completion.effect_key
                or predecessor_completion_commitment.receipt_sha256
                != predecessor_completion.receipt_sha256
                or predecessor_start_commitment.receipt_sha256 is not None
            ):
                _fail(
                    "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_PREDECESSOR_COMPLETION_ANCHOR_PROOF_DURABLE_MISMATCH"
                )
            predecessor_effect_start = _driver.PhysicalFullMatrixV4EffectStart(
                run_id=predecessor_start.run_id,
                plan_sha256=predecessor_start.plan_sha256,
                sequence=predecessor_start.phase_sequence,
                phase_request_sha256=predecessor_start.phase_request_sha256,
                effect_key=predecessor_start.effect_key,
                claim_id=predecessor_start.claim_id,
            )
            try:
                return _driver._mint_physical_full_matrix_v4_predecessor_phase_completion_anchor_proof(
                    request=request,
                    predecessor_effect_start=predecessor_effect_start,
                    journal_binding_sha256=facts.journal_binding_sha256,
                    baseline_plan_binding_sha256=facts.baseline_plan_binding_sha256,
                    anchor_genesis_sequence=facts.anchor_genesis_sequence,
                    anchor_genesis_head_sha256=facts.anchor_genesis_head_sha256,
                    predecessor_effect_start_anchor_previous_sequence=(
                        predecessor_start.anchor_previous_sequence
                    ),
                    predecessor_effect_start_anchor_previous_head_sha256=(
                        predecessor_start.anchor_previous_head_sha256
                    ),
                    predecessor_effect_start_anchor_sequence=(
                        predecessor_start.anchor_sequence
                    ),
                    predecessor_effect_start_anchor_head_sha256=(
                        predecessor_start.anchor_head_sha256
                    ),
                    predecessor_effect_start_anchor_commitment_sha256=(
                        predecessor_start.anchor_commitment_sha256
                    ),
                    predecessor_effect_start_anchor_attestation_sha256=(
                        predecessor_start.anchor_attestation_sha256
                    ),
                    predecessor_effect_start_anchor_local_previous_record_sha256=(
                        predecessor_start.previous_record_sha256
                    ),
                    predecessor_effect_start_anchor_local_event_sha256=(
                        predecessor_start.anchor_event_sha256
                    ),
                    predecessor_effect_started_at=predecessor_start.occurred_at,
                    predecessor_completion_receipt_sha256=(
                        predecessor_completion.receipt_sha256
                    ),
                    predecessor_completion_anchor_previous_sequence=(
                        predecessor_completion.anchor_previous_sequence
                    ),
                    predecessor_completion_anchor_previous_head_sha256=(
                        predecessor_completion.anchor_previous_head_sha256
                    ),
                    predecessor_completion_anchor_sequence=(
                        predecessor_completion.anchor_sequence
                    ),
                    predecessor_completion_anchor_head_sha256=(
                        predecessor_completion.anchor_head_sha256
                    ),
                    predecessor_completion_anchor_commitment_sha256=(
                        predecessor_completion.anchor_commitment_sha256
                    ),
                    predecessor_completion_anchor_attestation_sha256=(
                        predecessor_completion.anchor_attestation_sha256
                    ),
                    predecessor_completion_anchor_local_previous_record_sha256=(
                        predecessor_completion.previous_record_sha256
                    ),
                    predecessor_completion_anchor_local_event_sha256=(
                        predecessor_completion.anchor_event_sha256
                    ),
                    predecessor_completed_at=predecessor_completion.occurred_at,
                )
            except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
                raise PhysicalFullMatrixV4ReceiptJournalError(code) from exc

    def append_started(
        self,
        *,
        effect_start: _driver.PhysicalFullMatrixV4EffectStart,
        canonical_receipt: bytes,
    ) -> bytes:
        facts = _facts(self._config)
        if type(effect_start) is not _driver.PhysicalFullMatrixV4EffectStart:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_NOT_LIVE")
        checked_run, checked_plan, checked_sequence, checked_request, checked_effect = _claim_input(
            run_id=effect_start.run_id,
            plan_sha256=effect_start.plan_sha256,
            sequence=effect_start.sequence,
            phase_request_sha256=effect_start.phase_request_sha256,
            effect_key=effect_start.effect_key,
        )
        _require_campaign_run(
            facts=facts,
            run_id=checked_run,
            plan_sha256=checked_plan,
        )
        checked_claim = _claim_id(
            effect_start.claim_id,
            code="PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_NOT_LIVE",
        )
        if self._live_effect_starts.get(checked_claim) is not effect_start:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_NOT_LIVE")
        if type(canonical_receipt) is not bytes or not 1 <= len(canonical_receipt) <= _MAX_RECEIPT_BYTES:
            _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECEIPT_INVALID")
        try:
            parsed = _driver.parse_physical_full_matrix_v4_run_receipt(canonical_receipt)
        except Exception as exc:
            raise PhysicalFullMatrixV4ReceiptJournalError(
                "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECEIPT_INVALID"
            ) from exc
        with _locked_storage() as storage:
            state = _read_state(storage, facts=facts)
            before_anchor = _clock_now(clock=self._trusted_clock, floor=state.clock_floor)
            status, before_append = _observe_anchor(
                state=state,
                anchor=self._witness_anchor,
                facts=facts,
                clock=self._trusted_clock,
                floor=before_anchor,
            )
            if status.pending is not None:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_ANCHOR_PENDING")
            run = state.runs.get(str(checked_run))
            if (
                run is None
                or run.pending is None
                or run.pending.event != _EVENT_EFFECT_STARTED
                or not _same_phase(
                    run.pending.record,
                    run_id=checked_run,
                    plan_sha256=checked_plan,
                    phase_sequence=checked_sequence,
                    phase_request_sha256=checked_request,
                    effect_key=checked_effect,
                    claim_id=checked_claim,
                )
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_NOT_PENDING")
            previous = _ZERO_SHA256
            if run.receipts:
                try:
                    previous = _driver.parse_physical_full_matrix_v4_run_receipt(
                        run.receipts[-1]
                    ).receipt_sha256
                except Exception as exc:
                    raise PhysicalFullMatrixV4ReceiptJournalError(
                        "PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECEIPT_INVALID"
                    ) from exc
            if (
                parsed.run_id != checked_run
                or parsed.plan_sha256 != checked_plan
                or parsed.sequence != checked_sequence
                or parsed.phase_request_sha256 != checked_request
                or parsed.effect_key != checked_effect
                or parsed.previous_receipt_sha256 != previous
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_RECEIPT_EFFECT_MISMATCH")
            commitment = _commitment_for(
                facts=facts,
                state=state,
                event=_EVENT_COMPLETED,
                now=before_append,
                run_id=checked_run,
                plan_sha256=checked_plan,
                phase_sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=checked_claim,
                receipt=canonical_receipt,
            )
            anchor_receipt = _anchor_append(
                anchor=self._witness_anchor,
                facts=facts,
                before=status.head,
                commitment=commitment,
            )
            after_anchor, _durable_head = _verify_anchor_append_durable(
                anchor=self._witness_anchor,
                facts=facts,
                receipt=anchor_receipt,
                commitment=commitment,
                clock=self._trusted_clock,
                floor=before_append,
            )
            # If this local create-only write cannot complete, the external
            # completion commitment remains and a restart is indeterminate.
            record = _record_for(
                state=state,
                event=_EVENT_COMPLETED,
                occurred_at=commitment.occurred_at,
                clock_floor=after_anchor,
                run_id=checked_run,
                plan_sha256=checked_plan,
                phase_sequence=checked_sequence,
                phase_request_sha256=checked_request,
                effect_key=checked_effect,
                claim_id=checked_claim,
                receipt=canonical_receipt,
                anchor_sequence=anchor_receipt.sequence,
                anchor_head_sha256=anchor_receipt.head_sha256,
                anchor_commitment_sha256=anchor_receipt.commitment_sha256,
                anchor_attestation_sha256=anchor_receipt.attestation_sha256,
                anchor_event_sha256=commitment.local_event_sha256,
            )
            _append_record(storage, state=state, record=record, facts=facts)
            try:
                del self._live_effect_starts[checked_claim]
                del self._live_effect_start_anchors[checked_claim]
            except KeyError:
                _fail("PHYSICAL_FULL_MATRIX_V4_RECEIPT_JOURNAL_EFFECT_START_NOT_LIVE")
            return canonical_receipt
