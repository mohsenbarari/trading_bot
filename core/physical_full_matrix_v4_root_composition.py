"""Fail-closed, root-gated composition boundary for V4 Full Matrix.

This module deliberately does *not* run a V4 phase.  It merely binds the
eight already-named V4 phase-adapter seams to one exact, process-local V4
plan and to one explicit policy identity.  It also pins eight separately
owned post-effect verifier seams; those are typed dependencies only, never
synthetic completion proof.  No callback supplied here is
called: not the journal, readiness resolver, trusted clock, continuity gate,
or any phase adapter.

The boundary exists to make the missing deployment composition explicit before
any live integration is attempted.  In particular it refuses a generic
"adapter bag", a legacy runner, a direct FI<->IR control path, a mutable
phase map, or a policy/configuration that does not exactly match the plan.
Object Storage is represented only as a future callback carrier; it is never
an election, lease, writer, promotion, journal, or secret authority here.
The V4 campaign controller key and journal are explicitly forbidden from
being copied between FI and IR.

Readiness remains exclusively the driver's exact Gen2 witnessed capability;
this composition never imports, adapts, or accepts historical Gen1 readiness.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import os
import re
from types import MappingProxyType
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA",
    "PhysicalFullMatrixV4RootComposition",
    "PhysicalFullMatrixV4RootCompositionConfig",
    "PhysicalFullMatrixV4RootCompositionError",
    "PhysicalFullMatrixV4RootPhaseAdapterBinding",
    "build_physical_full_matrix_v4_root_composition",
    "derive_physical_full_matrix_v4_root_composition_policy_sha256",
    "require_physical_full_matrix_v4_root_composition",
)


PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-root-composition-v1"
)
PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-root-composition-policy-v1"
)
PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DEFAULT_ENABLED: Final = False

_FORBIDDEN = "forbidden"
_OBJECT_STORAGE_CALLBACK_CARRIER_ONLY = "future-callback-carrier-only"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_COMPOSITION_CAPABILITY = object()


class PhysicalFullMatrixV4RootCompositionError(ValueError):
    """The root-only V4 composition boundary failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4RootCompositionError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4RootCompositionConfig:
    """Explicit default-off pins for one non-operational V4 composition.

    ``enabled`` permits only *building* a typed composition after a root
    runtime check.  It does not permit a phase call, a promotion, a writer,
    or Full Matrix execution.  The policy digest is an independently supplied
    pin so an installer cannot silently accept a changed phase catalog,
    campaign, release, witnessed binding, or timing policy.
    """

    schema: str = PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DEFAULT_ENABLED
    campaign_id: str | None = None
    release_sha: str | None = None
    run_id: UUID | None = None
    maximum_oracle_age_seconds: int | None = None
    policy_sha256: str | None = None
    legacy_runner_artifacts: object = ()
    legacy_runner_compatibility: str = _FORBIDDEN
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    object_storage_role: str = _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY
    controller_key_or_journal_cross_site_copy: str = _FORBIDDEN

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalFullMatrixV4RootPhaseAdapterBinding:
    """One named, policy-pinned V4 phase adapter without invoking it.

    The wrapper prevents a later integrator from treating a generic callback
    as interchangeable with all eight phases.  It holds no endpoint,
    credential, peer-control, provider, Object Storage, Docker, or host
    operation.
    """

    phase_name: str
    phase_sequence: int
    oracle: str
    transport_profile: str
    destructive: bool
    campaign_id: str
    release_sha: str
    policy_sha256: str
    phase_adapter: _driver.PhysicalFullMatrixV4ExecutionAdapter | None = None
    legacy_runner_compatibility: str = _FORBIDDEN
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    object_storage_role: str = _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY
    controller_key_or_journal_cross_site_copy: str = _FORBIDDEN

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4RootComposition:
    """A process-local adapter composition; it is never execution authority."""

    schema: str
    plan: _driver.PhysicalFullMatrixV4ExecutionPlan
    execution_adapters: _driver.PhysicalFullMatrixV4ExecutionAdapters
    phase_bindings: Mapping[str, PhysicalFullMatrixV4RootPhaseAdapterBinding]
    campaign_id: str
    release_sha: str
    run_id: UUID
    plan_sha256: str
    policy_sha256: str
    maximum_oracle_age_seconds: int
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_executed: bool = False
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    object_storage_authority: str = _FORBIDDEN
    object_storage_role: str = _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY
    controller_key_or_journal_cross_site_copy: str = _FORBIDDEN
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _CompositionState:
    plan: _driver.PhysicalFullMatrixV4ExecutionPlan
    execution_adapters: _driver.PhysicalFullMatrixV4ExecutionAdapters
    phase_bindings: Mapping[str, PhysicalFullMatrixV4RootPhaseAdapterBinding]
    campaign_id: str
    release_sha: str
    run_id: UUID
    plan_sha256: str
    policy_sha256: str
    maximum_oracle_age_seconds: int


_COMPOSITION_STATES: WeakKeyDictionary[
    PhysicalFullMatrixV4RootComposition, _CompositionState
] = WeakKeyDictionary()


def _empty_legacy(value: object) -> bool:
    return (
        value is None
        or (type(value) is tuple and not value)
        or (type(value) is list and not value)
        or (type(value) is str and not value)
    )


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_ROOT_RUNTIME_REQUIRED")
    except (AttributeError, OSError) as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _phase_payload() -> list[dict[str, object]]:
    return [
        {
            "sequence": phase.sequence,
            "name": phase.name,
            "oracle": phase.oracle,
            "destructive": phase.destructive,
            "transport_profile": phase.transport_profile,
        }
        for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES
    ]


def derive_physical_full_matrix_v4_root_composition_policy_sha256(
    *,
    binding: _driver.PhysicalFullMatrixV4ExecutionBinding,
    run_id: UUID,
    maximum_oracle_age_seconds: int,
) -> str:
    """Derive the exact non-secret pin for one normal-direction V4 plan.

    The result is not an authorization token.  It intentionally covers the
    entire witnessed binding and fixed phase catalog, rather than only a
    campaign label, so an installer cannot retain a policy digest across a
    release, term, route, writer, phase, or maximum-age drift.
    """

    try:
        snapshot = _driver._snapshot_binding(
            binding,
            direction=("webapp_fi", "webapp_ir"),
        )
        if not isinstance(run_id, UUID) or run_id.int == 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_RUN_ID_INVALID")
        maximum_age = _driver._maximum_age(maximum_oracle_age_seconds)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_INPUT_INVALID"
        ) from exc

    payload = {
        "schema": PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_SCHEMA,
        "campaign_id": snapshot.campaign_id,
        "release_sha": snapshot.release_sha,
        "run_id": str(run_id),
        "maximum_oracle_age_seconds": maximum_age,
        "initial_normal_binding": dict(snapshot.__dict__),
        "phases": _phase_payload(),
        "legacy_runner_compatibility": _FORBIDDEN,
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
        "object_storage_authority": _FORBIDDEN,
        "object_storage_role": _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY,
        "controller_key_or_journal_cross_site_copy": _FORBIDDEN,
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_INPUT_INVALID"
        ) from exc


def _root_config(value: object) -> PhysicalFullMatrixV4RootCompositionConfig:
    if type(value) is not PhysicalFullMatrixV4RootCompositionConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIG_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DISABLED")
    if not _empty_legacy(value.legacy_runner_artifacts):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_LEGACY_RUNNER_REJECTED")
    if value.legacy_runner_compatibility != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_LEGACY_RUNNER_REJECTED")
    if value.direct_fi_to_ir_control != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DIRECT_FI_TO_IR_FORBIDDEN")
    if value.direct_ir_to_fi_control != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DIRECT_IR_TO_FI_FORBIDDEN")
    if value.object_storage_authority != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_OBJECT_STORAGE_AUTHORITY_FORBIDDEN")
    if value.object_storage_role != _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_OBJECT_STORAGE_ROLE_INVALID")
    if value.controller_key_or_journal_cross_site_copy != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONTROLLER_JOURNAL_COPY_FORBIDDEN")
    if (
        type(value.campaign_id) is not str
        or type(value.release_sha) is not str
        or not isinstance(value.run_id, UUID)
        or value.run_id.int == 0
        or type(value.maximum_oracle_age_seconds) is not int
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIG_INVALID")
    _sha256(value.policy_sha256, code="PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIG_INVALID")
    return value


def _static_plan(
    *,
    execution_config: object,
    plan: object,
) -> tuple[
    _driver.PhysicalFullMatrixV4ExecutionPlan,
    _driver._BindingSnapshot,
    UUID,
    int,
    _driver._PlanSnapshot,
]:
    try:
        binding, run_id, maximum_age = _driver._static_config(
            execution_config,
            require_enabled=True,
        )
        candidate = _driver.require_physical_full_matrix_v4_execution_plan(plan)
        snapshot = _driver._snapshot(candidate)
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_V4_INPUT_INVALID"
        ) from exc
    if (
        snapshot.binding != binding
        or snapshot.run_id != run_id
        or snapshot.maximum_oracle_age_seconds != maximum_age
        or snapshot.phases != _driver._phase_snapshots()
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIGURATION_DRIFT")
    return candidate, binding, run_id, maximum_age, snapshot


def _cross_pin_root_config(
    *,
    config: PhysicalFullMatrixV4RootCompositionConfig,
    binding: _driver._BindingSnapshot,
    run_id: UUID,
    maximum_age: int,
) -> str:
    if (
        config.campaign_id != binding.campaign_id
        or config.release_sha != binding.release_sha
        or config.run_id != run_id
        or config.maximum_oracle_age_seconds != maximum_age
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONFIGURATION_DRIFT")
    expected_policy_sha256 = derive_physical_full_matrix_v4_root_composition_policy_sha256(
        binding=_driver._binding_from_snapshot(binding),
        run_id=run_id,
        maximum_oracle_age_seconds=maximum_age,
    )
    if config.policy_sha256 != expected_policy_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_POLICY_PIN_MISMATCH")
    return expected_policy_sha256


def _strict_phase_bindings(
    *,
    value: object,
    campaign_id: str,
    release_sha: str,
    policy_sha256: str,
) -> tuple[
    Mapping[str, PhysicalFullMatrixV4RootPhaseAdapterBinding],
    Mapping[str, _driver.PhysicalFullMatrixV4ExecutionAdapter],
]:
    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTERS_MISSING")
    try:
        supplied = dict(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTERS_INVALID"
        ) from exc
    expected = {phase.name: phase for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES}
    if set(supplied) != set(expected):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTER_SET_INVALID")

    bindings: dict[str, PhysicalFullMatrixV4RootPhaseAdapterBinding] = {}
    adapters: dict[str, _driver.PhysicalFullMatrixV4ExecutionAdapter] = {}
    adapter_ids: set[int] = set()
    for name, phase in expected.items():
        candidate = supplied[name]
        if type(candidate) is not PhysicalFullMatrixV4RootPhaseAdapterBinding:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTER_BINDING_INVALID")
        if (
            candidate.phase_name != name
            or candidate.phase_sequence != phase.sequence
            or candidate.oracle != phase.oracle
            or candidate.transport_profile != phase.transport_profile
            or candidate.destructive is not phase.destructive
            or candidate.campaign_id != campaign_id
            or candidate.release_sha != release_sha
            or candidate.policy_sha256 != policy_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTER_SUBSTITUTION_REJECTED")
        if (
            candidate.legacy_runner_compatibility != _FORBIDDEN
            or candidate.direct_fi_to_ir_control != _FORBIDDEN
            or candidate.direct_ir_to_fi_control != _FORBIDDEN
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_CONTROL_FORBIDDEN")
        if candidate.object_storage_authority != _FORBIDDEN:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_OBJECT_STORAGE_AUTHORITY_FORBIDDEN")
        if candidate.object_storage_role != _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_OBJECT_STORAGE_ROLE_INVALID")
        if candidate.controller_key_or_journal_cross_site_copy != _FORBIDDEN:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONTROLLER_JOURNAL_COPY_FORBIDDEN")
        adapter = candidate.phase_adapter
        if not callable(getattr(adapter, "execute_phase", None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTER_INVALID")
        if id(adapter) in adapter_ids:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_PHASE_ADAPTER_SUBSTITUTION_REJECTED")
        adapter_ids.add(id(adapter))
        bindings[name] = candidate
        adapters[name] = adapter
    return MappingProxyType(bindings), MappingProxyType(adapters)


def _require_callback(value: object, *, method: str, code: str) -> None:
    if not callable(getattr(value, method, None)):
        _fail(code)


def _control_adapters(
    *,
    receipt_journal: object,
    readiness_resolver: object,
    trusted_clock: object,
    campaign_continuity_gate: object,
) -> None:
    for method in (
        "read_receipts",
        "claim_phase",
        "mark_effect_started",
        "project_effect_start_anchor_proof",
        "append_started",
    ):
        _require_callback(
            receipt_journal,
            method=method,
            code="PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_RECEIPT_JOURNAL_MISSING",
        )
    _require_callback(
        readiness_resolver,
        method="resolve_readiness",
        code="PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_READINESS_RESOLVER_MISSING",
    )
    _require_callback(
        trusted_clock,
        method="now_utc",
        code="PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_TRUSTED_CLOCK_MISSING",
    )
    _require_callback(
        campaign_continuity_gate,
        method="verify_campaign_continuity",
        code="PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_CONTINUITY_GATE_MISSING",
    )


def _strict_phase_post_effect_verifiers(
    *,
    value: object,
    phases: tuple[_driver.PhysicalFullMatrixV4ExecutionPhase, ...],
    adapters: Mapping[str, _driver.PhysicalFullMatrixV4ExecutionAdapter],
) -> Mapping[str, _driver.PhysicalFullMatrixV4PhasePostEffectVerifier]:
    """Pin eight distinct owner verifiers without invoking one.

    This prevents the composition boundary from silently discarding the
    execution driver's required verifier dependencies.  The driver repeats
    the same checks immediately before any claim, so this preflight does not
    grant execution authority or replace owner-specific verification.
    """

    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_MAP_REQUIRED")
    try:
        supplied = dict(value)
    except (TypeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_MAP_INVALID")
    expected = {phase.name for phase in phases}
    if set(supplied) != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_SET_INVALID")
    result: dict[str, _driver.PhysicalFullMatrixV4PhasePostEffectVerifier] = {}
    seen: set[int] = set()
    for phase in phases:
        verifier = supplied.get(phase.name)
        if not callable(getattr(verifier, "require_post_effect_completion", None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_INVALID")
        try:
            matches = (
                type(getattr(verifier, "phase_name")) is str
                and verifier.phase_name == phase.name
                and type(getattr(verifier, "phase_sequence")) is int
                and verifier.phase_sequence == phase.sequence
                and type(getattr(verifier, "oracle")) is str
                and verifier.oracle == phase.oracle
                and type(getattr(verifier, "transport_profile")) is str
                and verifier.transport_profile == phase.transport_profile
            )
        except Exception:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_INVALID")
        if not matches:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_BINDING_MISMATCH")
        if verifier is adapters.get(phase.name) or id(verifier) in seen:
            _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_VERIFIER_ALIAS")
        seen.add(id(verifier))
        result[phase.name] = verifier
    return MappingProxyType(result)


def build_physical_full_matrix_v4_root_composition(
    *,
    root_config: PhysicalFullMatrixV4RootCompositionConfig,
    execution_config: _driver.PhysicalFullMatrixV4ExecutionConfig,
    plan: _driver.PhysicalFullMatrixV4ExecutionPlan,
    phase_adapters: Mapping[str, PhysicalFullMatrixV4RootPhaseAdapterBinding],
    phase_post_effect_verifiers: Mapping[
        str, _driver.PhysicalFullMatrixV4PhasePostEffectVerifier
    ],
    receipt_journal: _driver.PhysicalFullMatrixV4ReceiptJournal,
    readiness_resolver: _driver.PhysicalFullMatrixV4ReadinessResolver,
    trusted_clock: _driver.PhysicalFullMatrixV4TrustedClock,
    campaign_continuity_gate: _driver.PhysicalFullMatrixV4CampaignContinuityGate,
) -> PhysicalFullMatrixV4RootComposition:
    """Build an exact typed composition without calling any injected seam.

    This is intentionally not a convenience runner.  It does not build a
    plan, execute a phase, read a journal, resolve readiness, observe a clock,
    verify continuity, contact a peer, or touch Object Storage.  A future
    root-only deployment layer must still separately implement all of those
    runtime operations and any human-approval gate.
    """

    config = _root_config(root_config)
    _root_runtime()
    candidate, binding, run_id, maximum_age, snapshot = _static_plan(
        execution_config=execution_config,
        plan=plan,
    )
    policy_sha256 = _cross_pin_root_config(
        config=config,
        binding=binding,
        run_id=run_id,
        maximum_age=maximum_age,
    )
    bindings, adapter_map = _strict_phase_bindings(
        value=phase_adapters,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        policy_sha256=policy_sha256,
    )
    verifier_map = _strict_phase_post_effect_verifiers(
        value=phase_post_effect_verifiers,
        phases=snapshot.phases,
        adapters=adapter_map,
    )
    _control_adapters(
        receipt_journal=receipt_journal,
        readiness_resolver=readiness_resolver,
        trusted_clock=trusted_clock,
        campaign_continuity_gate=campaign_continuity_gate,
    )
    execution_adapters = _driver.PhysicalFullMatrixV4ExecutionAdapters(
        phase_adapters=adapter_map,
        receipt_journal=receipt_journal,
        readiness_resolver=readiness_resolver,
        trusted_clock=trusted_clock,
        campaign_continuity_gate=campaign_continuity_gate,
        phase_post_effect_verifiers=verifier_map,
    )
    try:
        # This V4 helper checks only types and exact names; it never calls any
        # callback.  Retaining it prevents this foundation from drifting from
        # the driver's own phase-adapter protocol.
        _driver.prepare_physical_full_matrix_v4_execution_adapters(
            plan=candidate,
            adapters=execution_adapters,
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_DRIVER_ADAPTER_REJECTED"
        ) from exc

    result = PhysicalFullMatrixV4RootComposition(
        schema=PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA,
        plan=candidate,
        execution_adapters=execution_adapters,
        phase_bindings=bindings,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        run_id=run_id,
        plan_sha256=snapshot.plan_sha256,
        policy_sha256=policy_sha256,
        maximum_oracle_age_seconds=maximum_age,
    )
    object.__setattr__(result, "_capability", _COMPOSITION_CAPABILITY)
    _COMPOSITION_STATES[result] = _CompositionState(
        plan=candidate,
        execution_adapters=execution_adapters,
        phase_bindings=bindings,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        run_id=run_id,
        plan_sha256=snapshot.plan_sha256,
        policy_sha256=policy_sha256,
        maximum_oracle_age_seconds=maximum_age,
    )
    return require_physical_full_matrix_v4_root_composition(result)


def require_physical_full_matrix_v4_root_composition(
    value: object,
) -> PhysicalFullMatrixV4RootComposition:
    """Accept only a process-local composition minted by this boundary."""

    if type(value) is not PhysicalFullMatrixV4RootComposition:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_INVALID")
    if value._capability is not _COMPOSITION_CAPABILITY:
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_INVALID")
    state = _COMPOSITION_STATES.get(value)
    if state is None or (
        value.schema != PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_SCHEMA
        or value.plan is not state.plan
        or value.execution_adapters is not state.execution_adapters
        or value.phase_bindings is not state.phase_bindings
        or value.campaign_id != state.campaign_id
        or value.release_sha != state.release_sha
        or value.run_id != state.run_id
        or value.plan_sha256 != state.plan_sha256
        or value.policy_sha256 != state.policy_sha256
        or value.maximum_oracle_age_seconds != state.maximum_oracle_age_seconds
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.writer_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_executed is not False
        or value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or value.object_storage_authority != _FORBIDDEN
        or value.object_storage_role != _OBJECT_STORAGE_CALLBACK_CARRIER_ONLY
        or value.controller_key_or_journal_cross_site_copy != _FORBIDDEN
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_INVALID")
    try:
        _driver.require_physical_full_matrix_v4_execution_plan(value.plan)
        _driver.prepare_physical_full_matrix_v4_execution_adapters(
            plan=value.plan,
            adapters=value.execution_adapters,
        )
    except _driver.PhysicalFullMatrixV4ExecutionDriverError as exc:
        raise PhysicalFullMatrixV4RootCompositionError(
            "PHYSICAL_FULL_MATRIX_V4_ROOT_COMPOSITION_INVALID"
        ) from exc
    return value
