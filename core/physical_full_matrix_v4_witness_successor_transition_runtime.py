"""Root-gated, one-shot boundary for V4 P4/P7 successor transition evidence.

This module deliberately has no implementation of promotion, routing, a host
operation, a network client, or a provider client.  It only coordinates three
*injected* root-owned seams once: a target-site executor, an independent
observer, and the durable Witness admission owner.  Their signed outputs are
then checked by the exact V4 successor-transition verifier.

The wrapper is not a permit for any of those seams.  Each concrete seam must
enforce its own site-local policy and capabilities.  In particular, passing a
request to an injected seam never grants writer, promotion, traffic, external
effect, phase-completion, next-phase, execution, or Full-Matrix authority.
An ambiguous attempt is consumed before the first seam is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
import re
from threading import Lock
from typing import Final, Protocol
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_witness_successor_transition_evidence as _evidence


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig",
    "PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError",
    "WitnessSuccessorTransitionExecutor",
    "WitnessSuccessorTransitionObserver",
    "WitnessSuccessorTransitionWitnessAdmission",
    "build_physical_full_matrix_v4_witness_successor_transition_runtime",
    "derive_physical_full_matrix_v4_witness_successor_transition_runtime_policy_sha256",
    "execute_physical_full_matrix_v4_witness_successor_transition_runtime",
    "require_physical_full_matrix_v4_witness_successor_transition_execution_observation",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-witness-successor-transition-runtime-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_DEFAULT_ENABLED: Final = False

_FORBIDDEN: Final = "forbidden"
_STATUS: Final = "p4-p7-successor-transition-evidence-verified-not-authorized"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RUNTIME_CAPABILITY = object()
_OBSERVATION_CAPABILITY = object()


class PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(RuntimeError):
    """One typed refusal from the root-gated P4/P7 boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest:
    """Public correlation only, handed to each independently owned seam."""

    schema: str
    runtime_policy_sha256: str
    effect_start: _evidence.PhysicalFullMatrixV4SuccessorTransitionEffectStartPin
    effect_start_anchor: _evidence.PhysicalFullMatrixV4SuccessorTransitionAnchorPin
    predecessor_binding: object
    successor_binding: object
    successor_readiness: _evidence.PhysicalFullMatrixV4SuccessorTransitionReadinessEvidencePin
    evidence_pins: _evidence.PhysicalFullMatrixV4SuccessorTransitionEvidencePins
    replay_policy: _evidence.PhysicalFullMatrixV4SuccessorTransitionReplayPolicy
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False


class WitnessSuccessorTransitionExecutor(Protocol):
    """Future target root seam; no implementation is supplied here."""

    def execute_witness_successor_transition(
        self, request: PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest
    ) -> bytes: ...


class WitnessSuccessorTransitionObserver(Protocol):
    """Future independent observer seam; no implementation is supplied here."""

    def observe_witness_successor_transition(
        self,
        request: PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest,
        *,
        executor_receipt_sha256: str,
    ) -> bytes: ...


class WitnessSuccessorTransitionWitnessAdmission(Protocol):
    """Future durable Witness admission seam; no implementation is supplied here."""

    def admit_witness_successor_transition(
        self,
        request: PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest,
        *,
        executor_receipt_sha256: str,
        observer_receipt_sha256: str,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig:
    """Default-off root-only pins for exactly one P4 or P7 attempt."""

    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_DEFAULT_ENABLED
    verification_config: _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig | None = None
    runtime_policy_sha256: str | None = None
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime:
    """Opaque root-built, single-attempt boundary; never an operation permit."""

    schema: str
    runtime_policy_sha256: str
    request: PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation:
    """Verified signed evidence only; never phase completion or continuation."""

    status: str
    runtime_policy_sha256: str
    verified_transition: _evidence.VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    phase_completion_evidenced: bool = False
    next_phase_start_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_COPY_FORBIDDEN")


@dataclass
class _RuntimeState:
    config: _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig
    executor: WitnessSuccessorTransitionExecutor
    observer: WitnessSuccessorTransitionObserver
    witness: WitnessSuccessorTransitionWitnessAdmission
    request: PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest
    lock: Lock = field(default_factory=Lock)
    consumed: bool = False


@dataclass(frozen=True)
class _ObservationState:
    runtime: PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime
    verified: _evidence.VerifiedPhysicalFullMatrixV4WitnessSuccessorTransition


_RUNTIME_STATES: WeakKeyDictionary[PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime, _RuntimeState] = WeakKeyDictionary()
_OBSERVATION_STATES: WeakKeyDictionary[PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation, _ObservationState] = WeakKeyDictionary()


def _root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_ROOT_REQUIRED")
    except (AttributeError, OSError) as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_ROOT_REQUIRED"
        ) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _verification_facts(value: object) -> tuple[object, object, object, object, object, object, object]:
    if type(value) is not _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_VERIFICATION_CONFIG_INVALID")
    try:
        facts = _evidence._config(value)
    except _evidence.PhysicalFullMatrixV4WitnessSuccessorTransitionError as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc
    return (
        facts.effect_start,
        facts.anchor,
        facts.predecessor,
        facts.successor,
        facts.readiness,
        facts.evidence_pins,
        facts.replay_policy,
    )


def _runtime_policy_payload(config: _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig) -> dict[str, object]:
    effect, anchor, predecessor, successor, readiness, pins, replay = _verification_facts(config)
    try:
        phase_name = effect.phase.name
        predecessor_direction, successor_direction = _evidence._TRANSITION_DIRECTIONS[phase_name]
        effect_mapping = _evidence._effect_start_mapping(effect, code="RUNTIME_POLICY_INVALID")[1]
        anchor_mapping = _evidence._anchor_mapping(anchor, code="RUNTIME_POLICY_INVALID")[1]
        predecessor_mapping = _evidence._binding_mapping(
            predecessor, direction=predecessor_direction, code="RUNTIME_POLICY_INVALID"
        )[1]
        successor_mapping = _evidence._binding_mapping(
            successor, direction=successor_direction, code="RUNTIME_POLICY_INVALID"
        )[1]
        readiness_mapping = _evidence._readiness_mapping(
            readiness,
            successor=successor,
            now=None,
            maximum_age=config.maximum_evidence_age_seconds,
            code="RUNTIME_POLICY_INVALID",
        )[1]
        pins_mapping = _evidence._evidence_pins_mapping(pins, code="RUNTIME_POLICY_INVALID")[1]
        _evidence._replay_policy(replay, code="RUNTIME_POLICY_INVALID")
    except (KeyError, _evidence.PhysicalFullMatrixV4WitnessSuccessorTransitionError) as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA,
        "phase": phase_name,
        "effect_start": effect_mapping,
        "effect_start_anchor": anchor_mapping,
        "predecessor_binding": predecessor_mapping,
        "successor_binding": successor_mapping,
        "successor_readiness": readiness_mapping,
        "evidence_pins": pins_mapping,
        "replay_policy": {
            "anti_replay_namespace": replay.anti_replay_namespace,
            "witness_ledger_scope_sha256": replay.witness_ledger_scope_sha256,
        },
        "executor_signer_public_key_sha256": hashlib.sha256(config.executor_signer_public_key).hexdigest(),
        "observer_signer_public_key_sha256": hashlib.sha256(config.observer_signer_public_key).hexdigest(),
        "witness_signer_public_key_sha256": hashlib.sha256(config.witness_signer_public_key).hexdigest(),
        "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
        "object_storage_authority": _FORBIDDEN,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_switch_authorized": False,
        "external_effect_authorized": False,
        "phase_completion_evidenced": False,
        "next_phase_start_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def derive_physical_full_matrix_v4_witness_successor_transition_runtime_policy_sha256(
    *, verification_config: _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig
) -> str:
    """Derive a non-secret pin for one exact, non-authorizing P4/P7 boundary."""

    try:
        return hashlib.sha256(canonical_json_bytes(_runtime_policy_payload(verification_config))).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc


def _config(value: object) -> tuple[PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig, object, object, object, object, object, object, object]:
    if type(value) is not PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_DISABLED")
    if value.direct_fi_to_ir_control != _FORBIDDEN or value.direct_ir_to_fi_control != _FORBIDDEN:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_DIRECT_SITE_CONTROL_FORBIDDEN")
    if value.object_storage_authority != _FORBIDDEN:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_OBJECT_STORAGE_AUTHORITY_FORBIDDEN")
    if any(flag is not False for flag in (
        value.writer_authorized, value.promotion_authorized, value.traffic_switch_authorized,
        value.external_effect_authorized, value.phase_completion_evidenced,
        value.next_phase_start_authorized, value.execution_authorized, value.full_matrix_authorized,
    )):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_AUTHORITY_FORBIDDEN")
    policy = _sha256(value.runtime_policy_sha256, code="V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_CONFIG_INVALID")
    facts = _verification_facts(value.verification_config)
    if policy != derive_physical_full_matrix_v4_witness_successor_transition_runtime_policy_sha256(
        verification_config=value.verification_config
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_POLICY_MISMATCH")
    return (value, *facts)


def _adapter(value: object, method: str, *, code: str) -> None:
    if value is None or not callable(getattr(value, method, None)):
        _fail(code)


def build_physical_full_matrix_v4_witness_successor_transition_runtime(
    *,
    config: PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeConfig,
    executor: WitnessSuccessorTransitionExecutor,
    observer: WitnessSuccessorTransitionObserver,
    witness_admission: WitnessSuccessorTransitionWitnessAdmission,
) -> PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime:
    """Root-build a one-shot boundary without calling any injected seam."""

    _root_runtime()
    checked, effect, anchor, predecessor, successor, readiness, pins, replay = _config(config)
    _adapter(executor, "execute_witness_successor_transition", code="V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_EXECUTOR_INVALID")
    _adapter(observer, "observe_witness_successor_transition", code="V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_OBSERVER_INVALID")
    _adapter(witness_admission, "admit_witness_successor_transition", code="V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_WITNESS_INVALID")
    request = PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA,
        runtime_policy_sha256=checked.runtime_policy_sha256,
        effect_start=effect,
        effect_start_anchor=anchor,
        predecessor_binding=predecessor,
        successor_binding=successor,
        successor_readiness=readiness,
        evidence_pins=pins,
        replay_policy=replay,
    )
    runtime = PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime(
        schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA,
        runtime_policy_sha256=checked.runtime_policy_sha256,
        request=request,
    )
    object.__setattr__(runtime, "_capability", _RUNTIME_CAPABILITY)
    _RUNTIME_STATES[runtime] = _RuntimeState(
        checked.verification_config,
        executor,
        observer,
        witness_admission,
        request,
    )
    return runtime


def _request_is_pinned(
    request: object,
    *,
    policy: str,
    config: _evidence.PhysicalFullMatrixV4SuccessorTransitionVerificationConfig,
) -> bool:
    try:
        effect, anchor, predecessor, successor, readiness, pins, replay = _verification_facts(config)
        expected_policy = derive_physical_full_matrix_v4_witness_successor_transition_runtime_policy_sha256(
            verification_config=config
        )
    except PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError:
        return False
    return (
        type(request) is PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionRequest
        and request.schema == PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA
        and request.runtime_policy_sha256 == policy == expected_policy
        and request.effect_start == effect
        and request.effect_start_anchor == anchor
        and request.predecessor_binding == predecessor
        and request.successor_binding == successor
        and request.successor_readiness == readiness
        and request.evidence_pins == pins
        and request.replay_policy == replay
        and all(
            flag is False
            for flag in (
                request.writer_authorized,
                request.promotion_authorized,
                request.traffic_switch_authorized,
                request.external_effect_authorized,
                request.phase_completion_evidenced,
                request.next_phase_start_authorized,
                request.execution_authorized,
                request.full_matrix_authorized,
            )
        )
    )


def _runtime_state(value: object) -> _RuntimeState:
    if type(value) is not PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime or value._capability is not _RUNTIME_CAPABILITY:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_REQUIRED")
    state = _RUNTIME_STATES.get(value)
    if state is None:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_REQUIRED")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_SCHEMA
        or value.runtime_policy_sha256 != state.request.runtime_policy_sha256
        or value.request is not state.request
        or not _request_is_pinned(
            value.request,
            policy=value.runtime_policy_sha256,
            config=state.config,
        )
        or any(
            flag is not False
            for flag in (
                value.writer_authorized,
                value.promotion_authorized,
                value.traffic_switch_authorized,
                value.external_effect_authorized,
                value.phase_completion_evidenced,
                value.next_phase_start_authorized,
                value.execution_authorized,
                value.full_matrix_authorized,
            )
        )
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_TAMPERED")
    return state


def execute_physical_full_matrix_v4_witness_successor_transition_runtime(
    *, runtime: PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime, now: datetime
) -> PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation:
    """Call the three seams once in order, then verify only their evidence."""

    _root_runtime()
    state = _runtime_state(runtime)
    with state.lock:
        if state.consumed:
            _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_ATTEMPT_ALREADY_CONSUMED")
        state.consumed = True
    executor_receipt = state.executor.execute_witness_successor_transition(runtime.request)
    _runtime_state(runtime)
    executor_sha256 = hashlib.sha256(executor_receipt).hexdigest() if type(executor_receipt) is bytes else ""
    observer_receipt = state.observer.observe_witness_successor_transition(
        runtime.request, executor_receipt_sha256=executor_sha256
    )
    _runtime_state(runtime)
    observer_sha256 = hashlib.sha256(observer_receipt).hexdigest() if type(observer_receipt) is bytes else ""
    witness_receipt = state.witness.admit_witness_successor_transition(
        runtime.request,
        executor_receipt_sha256=executor_sha256,
        observer_receipt_sha256=observer_sha256,
    )
    try:
        verified = _evidence.verify_physical_full_matrix_v4_witness_successor_transition(
            executor_receipt=executor_receipt,
            observer_receipt=observer_receipt,
            witness_admission_receipt=witness_receipt,
            config=state.config,
            now=now,
        )
        _evidence.require_verified_physical_full_matrix_v4_witness_successor_transition(
            verified, config=state.config, now=now
        )
    except _evidence.PhysicalFullMatrixV4WitnessSuccessorTransitionError as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_EVIDENCE_INVALID"
        ) from exc
    observation = PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation(
        status=_STATUS,
        runtime_policy_sha256=runtime.runtime_policy_sha256,
        verified_transition=verified,
    )
    object.__setattr__(observation, "_capability", _OBSERVATION_CAPABILITY)
    _OBSERVATION_STATES[observation] = _ObservationState(runtime, verified)
    return observation


def require_physical_full_matrix_v4_witness_successor_transition_execution_observation(
    value: object, *, runtime: PhysicalFullMatrixV4WitnessSuccessorTransitionRuntime, now: datetime
) -> PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation:
    """Revalidate a result while preserving its non-authorizing semantics."""

    state = _runtime_state(runtime)
    if (
        type(value) is not PhysicalFullMatrixV4WitnessSuccessorTransitionExecutionObservation
        or value._capability is not _OBSERVATION_CAPABILITY
        or value.status != _STATUS
        or value.runtime_policy_sha256 != runtime.runtime_policy_sha256
        or any(flag is not False for flag in (
            value.writer_authorized, value.promotion_authorized, value.traffic_switch_authorized,
            value.external_effect_authorized, value.phase_completion_evidenced,
            value.next_phase_start_authorized, value.execution_authorized, value.full_matrix_authorized,
        ))
    ):
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_OBSERVATION_INVALID")
    observed = _OBSERVATION_STATES.get(value)
    if observed is None or observed.runtime is not runtime or observed.verified is not value.verified_transition:
        _fail("V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_OBSERVATION_INVALID")
    try:
        _evidence.require_verified_physical_full_matrix_v4_witness_successor_transition(
            value.verified_transition, config=state.config, now=now
        )
    except _evidence.PhysicalFullMatrixV4WitnessSuccessorTransitionError as exc:
        raise PhysicalFullMatrixV4WitnessSuccessorTransitionRuntimeError(
            "V4_WITNESS_SUCCESSOR_TRANSITION_RUNTIME_OBSERVATION_INVALID"
        ) from exc
    return value
