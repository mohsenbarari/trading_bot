"""Fail-closed live-root diagnostic for the quarantined V4 Phase-1 checkpoint.

Gen2's existing pending-commit handoff deliberately contains no
``AsyncSession`` or root-transaction identity.  A wrapper that receives a
raw pending object after Gen2 returns can observe a live session, but cannot
prove that this was the session/root that flushed that pending row.  In-memory
association is not a database causal fence.

This module therefore does *not* prepare or project a checkpoint.  It records
only a process-local, non-authorizing diagnostic that an exact post-effect
capture and a live PostgreSQL root were observed but the required Gen2
pending-session provenance and same-root database fence are unavailable.  It
never calls Gen2, starts/ends a transaction, persists a row, or exposes a raw
pending handoff.  The experimental migration remains quarantined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from uuid import uuid4
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession

from core import physical_full_matrix_execution_driver_v4 as _driver
from core import (
    physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint as _checkpoint,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_STATUS",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError",
    "PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable",
    "record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable",
    "require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable",
)


PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase1-post-effect-strict-ack-same-root-envelope-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_STATUS: Final = (
    "live-root-observed-db-causal-fence-required-unavailable"
)

_PHASE: Final = _driver.PHYSICAL_FULL_MATRIX_V4_PHASES[0]
_CAPABILITY = object()


class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(ValueError):
    """The non-candidate P1 diagnostic refused an unsafe live-root context."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig:
    """Default-off diagnostic policy, with no Gen2 adapter/session factory."""

    checkpoint_config: (
        _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig
        | None
    ) = None
    enabled: bool = (
        PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_DEFAULT_ENABLED
    )
    checkpoint_durable: bool = False
    gen2_pending_session_provenance_available: bool = False
    db_causal_fence_established: bool = False
    checkpoint_prepare_available: bool = False
    checkpoint_projection_available: bool = False
    phase_completion_evidenced: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True, eq=False, init=False)
class PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable:
    """Opaque diagnostic, never a prepared checkpoint or phase result."""

    schema: str
    status: str
    diagnostic_id: str
    run_id: object
    plan_sha256: str
    phase_name: str
    phase_sequence: int
    effect_key: str
    phase_request_sha256: str
    claim_id: str
    journaled_effect_start_identity_sha256: str
    capture_id: str
    live_root_transaction_observed: bool = True
    checkpoint_durable: bool = False
    gen2_pending_session_provenance_available: bool = False
    db_causal_fence_established: bool = False
    checkpoint_prepare_available: bool = False
    checkpoint_projection_available: bool = False
    phase_completion_evidenced: bool = False
    writer_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        diagnostic_id: str,
        request: _driver.PhysicalFullMatrixV4ExecutionRequest,
        capture: _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
        claim_id: str,
        journaled_effect_start_identity_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError(
                "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CONSTRUCTION_FORBIDDEN"
            )
        for name, value in (
            (
                "schema",
                PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SCHEMA,
            ),
            (
                "status",
                PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_STATUS,
            ),
            ("diagnostic_id", diagnostic_id),
            ("run_id", request.run_id),
            ("plan_sha256", request.plan_sha256),
            ("phase_name", request.phase.name),
            ("phase_sequence", request.phase.sequence),
            ("effect_key", request.effect_key),
            ("phase_request_sha256", request.phase_request_sha256),
            ("claim_id", claim_id),
            (
                "journaled_effect_start_identity_sha256",
                journaled_effect_start_identity_sha256,
            ),
            ("capture_id", capture.capture_id),
            ("live_root_transaction_observed", True),
            ("checkpoint_durable", False),
            ("gen2_pending_session_provenance_available", False),
            ("db_causal_fence_established", False),
            ("checkpoint_prepare_available", False),
            ("checkpoint_projection_available", False),
            ("phase_completion_evidenced", False),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("full_matrix_executed", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_COPY_FORBIDDEN"
        )

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_COPY_FORBIDDEN"
        )


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig
    session: AsyncSession
    root_transaction: object
    request: _driver.PhysicalFullMatrixV4ExecutionRequest
    capture: _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture


_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable, _State
] = WeakKeyDictionary()


def _config(
    value: object,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig:
    if (
        type(value)
        is not PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig
        or value.enabled is not True
        or type(value.checkpoint_config)
        is not _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointConfig
        or value.checkpoint_config.enabled is not True
        or value.checkpoint_durable is not False
        or value.gen2_pending_session_provenance_available is not False
        or value.db_causal_fence_established is not False
        or value.checkpoint_prepare_available is not False
        or value.checkpoint_projection_available is not False
        or value.phase_completion_evidenced is not False
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CONFIG_INVALID")
    try:
        _checkpoint._config(value.checkpoint_config)
    except _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CHECKPOINT_CONFIG_INVALID"
        ) from exc
    return value


def _live_root_transaction(
    session: object,
    *,
    existing_diagnostic: bool,
) -> object:
    """Read an exact root transaction without owning any lifecycle action."""

    if type(session) is not AsyncSession:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SESSION_INVALID")
    try:
        in_transaction = session.in_transaction()
        in_nested_transaction = session.in_nested_transaction()
        healthy = session.is_active
        sync_session = session.sync_session
        nested = sync_session.get_nested_transaction()
    except Exception as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SESSION_INVALID"
        ) from exc
    if (
        type(in_transaction) is not bool
        or type(in_nested_transaction) is not bool
        or type(healthy) is not bool
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SESSION_INVALID")
    if in_nested_transaction is True or nested is not None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_NESTED_TRANSACTION_FORBIDDEN")
    if in_transaction is not True:
        _fail(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_TERMINAL"
            if existing_diagnostic
            else "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_REQUIRED"
        )
    if healthy is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_TERMINAL")
    try:
        root = sync_session.get_transaction()
        bind = session.get_bind()
    except Exception as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SESSION_INVALID"
        ) from exc
    if root is None:
        _fail(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_TERMINAL"
            if existing_diagnostic
            else "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_REQUIRED"
        )
    if getattr(root, "nested", None) is not False:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_NESTED_TRANSACTION_FORBIDDEN")
    if getattr(root, "is_active", None) is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_TERMINAL")
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_POSTGRES_REQUIRED")
    return root


def _request_capture(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig,
    request: object,
    capture: object,
) -> _driver.PhysicalFullMatrixV4ExecutionRequest:
    if type(request) is not _driver.PhysicalFullMatrixV4ExecutionRequest or request.phase != _PHASE:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_REQUEST_INVALID")
    try:
        capture_state = _checkpoint._capture_state(
            capture,
            config=config.checkpoint_config,
            request=request,
        )
    except _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCheckpointError as exc:
        raise PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CAPTURE_REQUIRED"
        ) from exc
    # The diagnostic does not claim or consume a capture.  A claimed/consumed
    # object has already crossed an unsupported P1 path and cannot be reused
    # as fresh evidence of unavailability.
    if capture_state.consumed is not False or capture_state.same_root_envelope_claimed is not False:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CAPTURE_UNAVAILABLE")
    return request


def _matches(
    value: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable,
    *,
    state: _State,
) -> bool:
    facts = _checkpoint._request_facts(state.request)
    return (
        value.schema
        == PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_SCHEMA
        and value.status
        == PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_STATUS
        and type(value.diagnostic_id) is str
        and value.diagnostic_id.startswith("v4-p1-same-root-unavailable-")
        and value.run_id == facts.run_id
        and value.plan_sha256 == facts.plan_sha256
        and value.phase_name == facts.phase_name
        and value.phase_sequence == facts.phase_sequence
        and value.effect_key == facts.effect_key
        and value.phase_request_sha256 == facts.phase_request_sha256
        and value.claim_id == facts.claim_id
        and value.journaled_effect_start_identity_sha256
        == facts.journaled_effect_start_identity_sha256
        and value.capture_id == state.capture.capture_id
        and value.live_root_transaction_observed is True
        and value.checkpoint_durable is False
        and value.gen2_pending_session_provenance_available is False
        and value.db_causal_fence_established is False
        and value.checkpoint_prepare_available is False
        and value.checkpoint_projection_available is False
        and value.phase_completion_evidenced is False
        and value.writer_authorized is False
        and value.promotion_authorized is False
        and value.execution_authorized is False
        and value.full_matrix_authorized is False
        and value.full_matrix_executed is False
        and value._capability is _CAPABILITY
    )


def record_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig,
    session: AsyncSession,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
    capture: _checkpoint.PhysicalFullMatrixV4Phase1PostEffectStrictAckCapture,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable:
    """Record only that the missing causal-fence prerequisite was observed.

    No raw Gen2 pending object is accepted or inspected.  The result explicitly
    says that prepare/projection remain unavailable; it cannot be converted to
    a checkpoint, transaction receipt, writer permit, or phase completion.
    """

    checked_config = _config(config)
    checked_request = _request_capture(
        config=checked_config,
        request=request,
        capture=capture,
    )
    root = _live_root_transaction(session, existing_diagnostic=False)
    facts = _checkpoint._request_facts(checked_request)
    result = PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable(
        diagnostic_id="v4-p1-same-root-unavailable-" + uuid4().hex,
        request=checked_request,
        capture=capture,
        claim_id=facts.claim_id,
        journaled_effect_start_identity_sha256=facts.journaled_effect_start_identity_sha256,
        capability=_CAPABILITY,
    )
    _STATES[result] = _State(
        config=checked_config,
        session=session,
        root_transaction=root,
        request=checked_request,
        capture=capture,
    )
    return require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
        result,
        config=checked_config,
        session=session,
        request=checked_request,
    )


def require_physical_full_matrix_v4_phase1_post_effect_strict_ack_same_root_envelope_unavailable(
    value: object,
    *,
    config: PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeConfig,
    session: AsyncSession,
    request: _driver.PhysicalFullMatrixV4ExecutionRequest,
) -> PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable:
    """Revalidate a diagnostic while its exact observed root remains live."""

    checked_config = _config(config)
    if (
        type(value)
        is not PhysicalFullMatrixV4Phase1PostEffectStrictAckSameRootEnvelopeUnavailable
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or state.config is not checked_config or state.request is not request:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_CAPABILITY_REQUIRED")
    if session is not state.session:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_FOREIGN_SESSION")
    root = _live_root_transaction(state.session, existing_diagnostic=True)
    if root is not state.root_transaction:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_ROOT_TRANSACTION_REPLACED")
    checked_request = _request_capture(
        config=checked_config,
        request=state.request,
        capture=state.capture,
    )
    if checked_request is not state.request or not _matches(value, state=state):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE1_POST_EFFECT_STRICT_ACK_SAME_ROOT_ENVELOPE_TAMPERED")
    return value
