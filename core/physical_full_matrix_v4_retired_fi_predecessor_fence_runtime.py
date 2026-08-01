"""Root-gated, single-attempt boundary for V4 phase-2 FI retirement.

This is deliberately a *boundary*, not a host fencer.  It has no endpoint,
credential, shell command, Docker, database, provider, socket, or Object
Storage implementation.  The only operational seams are three explicitly
injected root-owned components: an FI fencer, an independent observer, and a
Witness anti-replay admission client.  They are called once, in that order,
only after a root runtime has enabled and cross-pinned one exact P2 policy.

The boundary marks an attempt consumed before invoking the FI fencer.  It
never retries an ambiguous operation and never invokes the observer or
Witness after a preceding seam fails.  Its output is merely the existing
signed P2 evidence verification; it is not authority to write, promote,
switch traffic, run a phase, or continue Full Matrix.

An actual deployment still has to provide the three independent root-owned
implementations.  In particular the FI component must enforce the real
server-side writer fence, the observer must independently inspect it, and the
Witness component must durably reserve the deterministic replay identity.
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
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as _fence


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SCHEMA",
    "PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation",
    "PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest",
    "PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime",
    "PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig",
    "PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError",
    "RetiredFiPredecessorFenceExecutor",
    "RetiredFiPredecessorFenceObserver",
    "RetiredFiPredecessorFenceWitnessAdmission",
    "build_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime",
    "derive_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime_policy_sha256",
    "execute_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime",
    "require_physical_full_matrix_v4_retired_fi_predecessor_fence_execution_observation",
)


PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-retired-fi-predecessor-fence-runtime-v1"
)
PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_DEFAULT_ENABLED: Final = False

_FORBIDDEN: Final = "forbidden"
_STATUS: Final = "p2-retired-fi-evidence-verified-not-authorized"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RUNTIME_CAPABILITY = object()
_OBSERVATION_CAPABILITY = object()


class PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(RuntimeError):
    """The P2 root execution boundary failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest:
    """Exact public P2 correlation handed to each independently owned seam.

    This contains no credential or execution permit.  A real FI seam must
    require its own local root policy and must not accept this correlation as
    authority by itself.
    """

    schema: str
    runtime_policy_sha256: str
    effect_start: _fence.PhysicalFullMatrixV4EffectStartPin
    effect_start_anchor: _fence.PhysicalFullMatrixV4EffectStartAnchorPin
    predecessor_term: _fence.RetiredFiPredecessorFenceTermPin
    evidence_pins: _fence.RetiredFiPredecessorFenceEvidencePins
    anti_replay_policy: _fence.RetiredFiPredecessorFenceAntiReplayPolicy
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False


class RetiredFiPredecessorFenceExecutor(Protocol):
    """Future FI root-owned fencer; this module supplies no implementation."""

    def execute_retired_fi_predecessor_fence(
        self,
        request: PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest,
    ) -> bytes: ...


class RetiredFiPredecessorFenceObserver(Protocol):
    """Future independently operated post-fence observer."""

    def observe_retired_fi_predecessor_fence(
        self,
        request: PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest,
        *,
        executor_receipt_sha256: str,
    ) -> bytes: ...


class RetiredFiPredecessorFenceWitnessAdmission(Protocol):
    """Future durable Witness single-use admission owner."""

    def admit_retired_fi_predecessor_fence(
        self,
        request: PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest,
        *,
        executor_receipt_sha256: str,
        observer_receipt_sha256: str,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig:
    """Root-only, default-off pins for exactly one P2 fencing attempt.

    ``enabled`` permits construction only.  It does not authorize a writer,
    promotion, traffic switch, external effect, phase continuation, or Full
    Matrix.  The supplied policy digest prevents a deployment from silently
    swapping the expected P2 term, V4 effect-start, anchor, evidence pins, or
    signer policy after independent review.
    """

    enabled: bool = PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_DEFAULT_ENABLED
    verification_config: _fence.RetiredFiPredecessorFenceVerificationConfig | None = None
    runtime_policy_sha256: str | None = None
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime:
    """Opaque root-built, one-shot P2 boundary; never a V4 execution permit."""

    schema: str
    runtime_policy_sha256: str
    request: PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation:
    """Fresh, verified P2 evidence observation; never operational authority."""

    status: str
    runtime_policy_sha256: str
    verified_fence: _fence.VerifiedRetiredFiPredecessorFence
    writer_authorized: bool = False
    promotion_authorized: bool = False
    traffic_switch_authorized: bool = False
    external_effect_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_COPY_FORBIDDEN")


@dataclass
class _RuntimeState:
    config: _fence.RetiredFiPredecessorFenceVerificationConfig
    executor: RetiredFiPredecessorFenceExecutor
    observer: RetiredFiPredecessorFenceObserver
    witness: RetiredFiPredecessorFenceWitnessAdmission
    lock: Lock = field(default_factory=Lock)
    consumed: bool = False


@dataclass(frozen=True)
class _ObservationState:
    runtime: PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime
    verified_fence: _fence.VerifiedRetiredFiPredecessorFence


_RUNTIME_STATES: WeakKeyDictionary[PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime, _RuntimeState] = WeakKeyDictionary()
_OBSERVATION_STATES: WeakKeyDictionary[PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation, _ObservationState] = WeakKeyDictionary()


def _root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_ROOT_REQUIRED")
    except (AttributeError, OSError) as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_ROOT_REQUIRED"
        ) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _verification_facts(
    value: object,
) -> tuple[
    _fence.PhysicalFullMatrixV4EffectStartPin,
    _fence.PhysicalFullMatrixV4EffectStartAnchorPin,
    _fence.RetiredFiPredecessorFenceTermPin,
    _fence.RetiredFiPredecessorFenceEvidencePins,
    _fence.RetiredFiPredecessorFenceAntiReplayPolicy,
]:
    if type(value) is not _fence.RetiredFiPredecessorFenceVerificationConfig:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_VERIFICATION_CONFIG_INVALID")
    try:
        facts = _fence._config(value)
    except _fence.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc
    return (
        facts.effect_start,
        facts.effect_start_anchor,
        facts.predecessor_term,
        facts.evidence_pins,
        facts.anti_replay_policy,
    )


def _runtime_policy_payload(
    config: _fence.RetiredFiPredecessorFenceVerificationConfig,
) -> dict[str, object]:
    effect, anchor, term, evidence, replay = _verification_facts(config)
    try:
        effect_mapping = _fence._effect_start_mapping(effect, code="RUNTIME_POLICY_INVALID")[1]
        anchor_mapping = _fence._effect_start_anchor_mapping(anchor, code="RUNTIME_POLICY_INVALID")[1]
        term_mapping = _fence._term_mapping(term, code="RUNTIME_POLICY_INVALID")[1]
        evidence_mapping = _fence._evidence_pins_mapping(evidence, code="RUNTIME_POLICY_INVALID")[1]
        _fence._anti_replay_policy(replay, code="RUNTIME_POLICY_INVALID")
        replay_mapping = {
            "anti_replay_namespace": replay.anti_replay_namespace,
            "witness_ledger_scope_sha256": replay.witness_ledger_scope_sha256,
        }
    except _fence.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SCHEMA,
        "phase": "fence-fi-writer-v2",
        "effect_start": effect_mapping,
        "effect_start_anchor": anchor_mapping,
        "predecessor_term": term_mapping,
        "evidence_pins": evidence_mapping,
        "anti_replay_policy": replay_mapping,
        "executor_signer_public_key_sha256": hashlib.sha256(
            config.executor_signer_public_key
        ).hexdigest(),
        "observer_signer_public_key_sha256": hashlib.sha256(
            config.observer_signer_public_key
        ).hexdigest(),
        "witness_signer_public_key_sha256": hashlib.sha256(
            config.witness_anti_replay_signer_public_key
        ).hexdigest(),
        "maximum_evidence_age_seconds": config.maximum_evidence_age_seconds,
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
        "object_storage_authority": _FORBIDDEN,
        "writer_authorized": False,
        "promotion_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def derive_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime_policy_sha256(
    *,
    verification_config: _fence.RetiredFiPredecessorFenceVerificationConfig,
) -> str:
    """Derive a non-secret policy pin for one exact P2 boundary attempt."""

    try:
        return hashlib.sha256(
            canonical_json_bytes(_runtime_policy_payload(verification_config))
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_VERIFICATION_CONFIG_INVALID"
        ) from exc


def _config(
    value: object,
) -> tuple[
    PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig,
    _fence.PhysicalFullMatrixV4EffectStartPin,
    _fence.PhysicalFullMatrixV4EffectStartAnchorPin,
    _fence.RetiredFiPredecessorFenceTermPin,
    _fence.RetiredFiPredecessorFenceEvidencePins,
    _fence.RetiredFiPredecessorFenceAntiReplayPolicy,
]:
    if type(value) is not PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_DISABLED")
    if (
        value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_DIRECT_SITE_CONTROL_FORBIDDEN")
    if value.object_storage_authority != _FORBIDDEN:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_OBJECT_STORAGE_AUTHORITY_FORBIDDEN")
    if any(
        flag is not False
        for flag in (
            value.writer_authorized,
            value.promotion_authorized,
            value.traffic_switch_authorized,
            value.execution_authorized,
            value.full_matrix_authorized,
        )
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_AUTHORITY_FORBIDDEN")
    policy = _sha256(
        value.runtime_policy_sha256,
        code="RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_CONFIG_INVALID",
    )
    facts = _verification_facts(value.verification_config)
    expected = derive_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime_policy_sha256(
        verification_config=value.verification_config,
    )
    if policy != expected:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_POLICY_MISMATCH")
    return (value, *facts)


def _adapter(value: object, method: str, *, code: str) -> None:
    if value is None or not callable(getattr(value, method, None)):
        _fail(code)


def build_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
    *,
    config: PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeConfig,
    executor: RetiredFiPredecessorFenceExecutor,
    observer: RetiredFiPredecessorFenceObserver,
    witness_admission: RetiredFiPredecessorFenceWitnessAdmission,
) -> PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime:
    """Root-build a one-shot boundary without calling any injected component."""

    _root_runtime()
    checked, effect, anchor, term, evidence, replay = _config(config)
    _adapter(
        executor,
        "execute_retired_fi_predecessor_fence",
        code="RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_EXECUTOR_INVALID",
    )
    _adapter(
        observer,
        "observe_retired_fi_predecessor_fence",
        code="RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_OBSERVER_INVALID",
    )
    _adapter(
        witness_admission,
        "admit_retired_fi_predecessor_fence",
        code="RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_WITNESS_INVALID",
    )
    request = PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionRequest(
        schema=PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SCHEMA,
        runtime_policy_sha256=checked.runtime_policy_sha256,
        effect_start=effect,
        effect_start_anchor=anchor,
        predecessor_term=term,
        evidence_pins=evidence,
        anti_replay_policy=replay,
    )
    runtime = PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime(
        schema=PHYSICAL_FULL_MATRIX_V4_RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_SCHEMA,
        runtime_policy_sha256=checked.runtime_policy_sha256,
        request=request,
    )
    object.__setattr__(runtime, "_capability", _RUNTIME_CAPABILITY)
    _RUNTIME_STATES[runtime] = _RuntimeState(
        config=checked.verification_config,
        executor=executor,
        observer=observer,
        witness=witness_admission,
    )
    return runtime


def _runtime_state(value: object) -> _RuntimeState:
    if (
        type(value) is not PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime
        or value._capability is not _RUNTIME_CAPABILITY
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_REQUIRED")
    state = _RUNTIME_STATES.get(value)
    if state is None:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_REQUIRED")
    return state


def execute_physical_full_matrix_v4_retired_fi_predecessor_fence_runtime(
    *,
    runtime: PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime,
    now: datetime,
) -> PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation:
    """Make exactly one injected P2 attempt and verify its signed evidence.

    There is intentionally no retry path.  An exception from any seam leaves
    this runtime consumed so an operator must investigate and root-build a
    fresh, newly pinned attempt rather than replaying an ambiguous fence.
    """

    _root_runtime()
    state = _runtime_state(runtime)
    with state.lock:
        if state.consumed:
            _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_ATTEMPT_ALREADY_CONSUMED")
        state.consumed = True
    executor_receipt = state.executor.execute_retired_fi_predecessor_fence(runtime.request)
    executor_sha256 = hashlib.sha256(executor_receipt).hexdigest() if type(executor_receipt) is bytes else ""
    observer_receipt = state.observer.observe_retired_fi_predecessor_fence(
        runtime.request,
        executor_receipt_sha256=executor_sha256,
    )
    observer_sha256 = hashlib.sha256(observer_receipt).hexdigest() if type(observer_receipt) is bytes else ""
    witness_receipt = state.witness.admit_retired_fi_predecessor_fence(
        runtime.request,
        executor_receipt_sha256=executor_sha256,
        observer_receipt_sha256=observer_sha256,
    )
    try:
        verified = _fence.verify_retired_fi_predecessor_fence(
            executor_receipt=executor_receipt,
            observer_receipt=observer_receipt,
            witness_admission_receipt=witness_receipt,
            config=state.config,
            now=now,
        )
        _fence.require_verified_retired_fi_predecessor_fence(
            verified,
            config=state.config,
            now=now,
        )
    except _fence.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_EVIDENCE_INVALID"
        ) from exc
    observation = PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation(
        status=_STATUS,
        runtime_policy_sha256=runtime.runtime_policy_sha256,
        verified_fence=verified,
    )
    object.__setattr__(observation, "_capability", _OBSERVATION_CAPABILITY)
    _OBSERVATION_STATES[observation] = _ObservationState(
        runtime=runtime,
        verified_fence=verified,
    )
    return observation


def require_physical_full_matrix_v4_retired_fi_predecessor_fence_execution_observation(
    value: object,
    *,
    runtime: PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntime,
    now: datetime,
) -> PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation:
    """Revalidate a result and preserve its non-authorizing meaning."""

    state = _runtime_state(runtime)
    if (
        type(value) is not PhysicalFullMatrixV4RetiredFiPredecessorFenceExecutionObservation
        or value._capability is not _OBSERVATION_CAPABILITY
        or value.status != _STATUS
        or value.runtime_policy_sha256 != runtime.runtime_policy_sha256
        or any(
            flag is not False
            for flag in (
                value.writer_authorized,
                value.promotion_authorized,
                value.traffic_switch_authorized,
                value.external_effect_authorized,
                value.execution_authorized,
                value.full_matrix_authorized,
            )
        )
    ):
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_OBSERVATION_INVALID")
    observed = _OBSERVATION_STATES.get(value)
    if observed is None or observed.runtime is not runtime or observed.verified_fence is not value.verified_fence:
        _fail("RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_OBSERVATION_INVALID")
    try:
        _fence.require_verified_retired_fi_predecessor_fence(
            value.verified_fence,
            config=state.config,
            now=now,
        )
    except _fence.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4RetiredFiPredecessorFenceRuntimeError(
            "RETIRED_FI_PREDECESSOR_FENCE_RUNTIME_OBSERVATION_INVALID"
        ) from exc
    return value
