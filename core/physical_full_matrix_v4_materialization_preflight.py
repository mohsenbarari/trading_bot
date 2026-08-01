"""Fail-closed V4 materialization preflight, without installing an adapter.

This is the narrow boundary immediately before a future root-only deployment
layer could install host/provider phase adapters.  It is deliberately *not*
that layer: it opens no host, provider, SSH, network, Object Storage, Docker,
or subprocess boundary.  Its only invoked seam is the exact root-owned
trusted UTC clock already pinned by the composition, sampled solely to
revalidate the opaque Gen2 readiness at a concrete time.

The preflight accepts only a process-local V4 root composition, a fresh exact
Gen2 witnessed-readiness capability, all eight already-named phase adapter
bindings, and a narrow Witness-journal-anchor interface.  Its opaque result
is diagnostic material only; it grants no materialization, installation,
writer, promotion, transport, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_root_composition as _root_composition
from core import physical_full_matrix_v4_witness_anchor_wire as _anchor_wire
from core.physical_full_matrix_v2_gen2_witnessed_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
    PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS,
    PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError,
    VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness,
    require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_REQUIRED_PHASE_NAMES",
    "PhysicalFullMatrixV4MaterializationPreflight",
    "PhysicalFullMatrixV4MaterializationPreflightConfig",
    "PhysicalFullMatrixV4MaterializationPreflightError",
    "PhysicalFullMatrixV4MaterializationPreflightInputs",
    "PhysicalFullMatrixV4MaterializationTrustedClock",
    "PhysicalFullMatrixV4MaterializationWitnessAnchor",
    "PhysicalFullMatrixV4MaterializationWitnessAnchorBinding",
    "prepare_physical_full_matrix_v4_materialization_preflight",
    "require_prepared_physical_full_matrix_v4_materialization_preflight",
)


PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-materialization-preflight-v1"
)
PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_REQUIRED_PHASE_NAMES: Final = tuple(
    phase.name for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES
)

_FORBIDDEN = "forbidden"
_PREFLIGHT_ONLY = "preflight-only"
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CAPABILITY = object()


class PhysicalFullMatrixV4MaterializationPreflightError(ValueError):
    """A V4 installation-material input is incomplete, foreign, or stale."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4MaterializationPreflightError(code)


class PhysicalFullMatrixV4MaterializationWitnessAnchor(Protocol):
    """The sole static Witness-anchor shape required before installation.

    The methods intentionally mirror the journal's narrow immutable-anchor
    seam.  Preflight checks only their existence and never calls either one.
    """

    def read_head(
        self,
        *,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        expected_anchor_sequence: int,
        expected_anchor_head_sha256: str,
    ) -> object: ...

    def append_commitment(self, *, commitment: object) -> object: ...


class PhysicalFullMatrixV4MaterializationTrustedClock(Protocol):
    """Exact root-owned UTC clock required for a fresh preflight check.

    This is intentionally separate from a caller-provided ``datetime``: a
    cached capability cannot choose an old timestamp and call itself fresh.
    The object must be the same clock object captured in the root composition.
    """

    def now_utc(self) -> datetime: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4MaterializationWitnessAnchorBinding:
    """Static non-secret identity pin for a narrow Witness-anchor interface."""

    identity: _anchor_wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity | None = None
    anchor: PhysicalFullMatrixV4MaterializationWitnessAnchor | None = None


@dataclass(frozen=True)
class PhysicalFullMatrixV4MaterializationPreflightConfig:
    """Default-off policy that allows preparation, never installation itself."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DEFAULT_ENABLED
    legacy_runner_artifacts: object = ()
    legacy_runner_compatibility: str = _FORBIDDEN
    host_provider_installation_mode: str = _PREFLIGHT_ONLY
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN


@dataclass(frozen=True)
class PhysicalFullMatrixV4MaterializationPreflightInputs:
    """Exact V4 composition material required before any installer exists."""

    composition: _root_composition.PhysicalFullMatrixV4RootComposition | None = None
    readiness: VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness | None = None
    phase_adapter_material: (
        Mapping[str, _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding]
        | None
    ) = None
    witness_anchor: PhysicalFullMatrixV4MaterializationWitnessAnchorBinding | None = None
    trusted_clock: PhysicalFullMatrixV4MaterializationTrustedClock | None = None


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4MaterializationPreflight:
    """Opaque process-local diagnostic material, never an install permit."""

    schema: str
    preflight_sha256: str
    campaign_id: str
    release_sha: str
    run_id: UUID
    prepared_at: datetime
    plan_sha256: str
    policy_sha256: str
    readiness_binding_sha256: str
    adapter_names: tuple[str, ...]
    witness_anchor_identity: _anchor_wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
    materialization_authorized: bool = False
    host_provider_installation_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _Facts:
    composition: _root_composition.PhysicalFullMatrixV4RootComposition
    readiness: VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness
    phase_material: Mapping[str, _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding]
    anchor: PhysicalFullMatrixV4MaterializationWitnessAnchorBinding
    campaign_id: str
    release_sha: str
    run_id: UUID
    observed_at: datetime
    plan_sha256: str
    policy_sha256: str
    readiness_binding_sha256: str


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV4MaterializationPreflightConfig
    inputs: PhysicalFullMatrixV4MaterializationPreflightInputs
    facts: _Facts


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4MaterializationPreflight, _State] = (
    WeakKeyDictionary()
)


def _empty_legacy(value: object) -> bool:
    return (
        value is None
        or (type(value) is tuple and not value)
        or (type(value) is list and not value)
        or (type(value) is str and not value)
    )


def _sha(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or _SHA256_RE.fullmatch(value) is None
        or value == _ZERO_SHA256
    ):
        _fail(code)
    return value


def _config(value: object) -> PhysicalFullMatrixV4MaterializationPreflightConfig:
    if type(value) is not PhysicalFullMatrixV4MaterializationPreflightConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CONFIG_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DISABLED")
    if not _empty_legacy(value.legacy_runner_artifacts):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_LEGACY_REJECTED")
    if value.legacy_runner_compatibility != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_LEGACY_REJECTED")
    if value.host_provider_installation_mode != _PREFLIGHT_ONLY:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_INSTALL_MODE_INVALID")
    if value.direct_fi_to_ir_control != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DIRECT_FI_TO_IR_FORBIDDEN")
    if value.direct_ir_to_fi_control != _FORBIDDEN:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DIRECT_IR_TO_FI_FORBIDDEN")
    return value


def _require_composition(
    value: object,
) -> _root_composition.PhysicalFullMatrixV4RootComposition:
    try:
        result = _root_composition.require_physical_full_matrix_v4_root_composition(value)
    except _root_composition.PhysicalFullMatrixV4RootCompositionError as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_COMPOSITION_INVALID"
        ) from exc
    if type(result) is not _root_composition.PhysicalFullMatrixV4RootComposition:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_COMPOSITION_INVALID")
    return result


def _trusted_now(
    value: object,
    *,
    composition: _root_composition.PhysicalFullMatrixV4RootComposition,
    floor: datetime | None,
) -> datetime:
    """Sample exactly the root-pinned clock and reject stale clock tricks."""

    if value is not composition.execution_adapters.trusted_clock:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_MISMATCH")
    callback = getattr(value, "now_utc", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_REQUIRED")
    try:
        observed = callback()
    except Exception as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_FAILED"
        ) from exc
    if (
        type(observed) is not datetime
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_INVALID")
    try:
        normalized = observed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_INVALID"
        ) from exc
    # The Gen2 readiness owner deliberately accepts only whole-second clocks.
    # Enforcing that here makes the clock contract explicit rather than
    # converting a root-clock error into a generic readiness mismatch.
    if normalized.microsecond != 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_INVALID")
    if floor is not None and normalized < floor:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_TRUSTED_CLOCK_REGRESSION")
    return normalized


def _readiness(
    value: object,
    *,
    composition: _root_composition.PhysicalFullMatrixV4RootComposition,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
    if type(value) is not VerifiedPhysicalFullMatrixV2Gen2WitnessedCampaignReadiness:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_GEN2_READINESS_REQUIRED")
    try:
        report = require_verified_physical_full_matrix_v2_gen2_witnessed_campaign_readiness(
            value,
            now=now,
        )
    except PhysicalFullMatrixV2Gen2WitnessedCampaignReadinessError as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_GEN2_READINESS_INVALID"
        ) from exc
    plan_binding = composition.plan.binding
    if (
        report.schema != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_SCHEMA
        or report.status
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or report.reason_codes != ()
        or report.observed_slots
        != PHYSICAL_FULL_MATRIX_V2_GEN2_WITNESSED_REQUIRED_READINESS_SLOTS
        or report.campaign_id != composition.campaign_id
        or report.release_sha != composition.release_sha
        or report.campaign_id != plan_binding.campaign_id
        or report.release_sha != plan_binding.release_sha
        or report.binding_sha256 != plan_binding.readiness_binding_sha256
        or report.external_execution_authorized is not False
        or report.promotion_authorized is not False
        or report.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_GEN2_READINESS_MISMATCH")
    return value


def _phase_material(
    value: object,
    *,
    composition: _root_composition.PhysicalFullMatrixV4RootComposition,
) -> Mapping[str, _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding]:
    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_MATERIAL_REQUIRED")
    try:
        supplied = dict(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_MATERIAL_INVALID"
        ) from exc
    expected = PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_REQUIRED_PHASE_NAMES
    if set(supplied) != set(expected):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_SET_INVALID")
    composition_adapters = composition.execution_adapters.phase_adapters
    if not isinstance(composition_adapters, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_COMPOSITION_INVALID")
    adapter_ids: set[int] = set()
    material: dict[str, _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding] = {}
    for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES:
        name = phase.name
        candidate = supplied[name]
        if type(candidate) is not _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding:
            _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_MATERIAL_INVALID")
        adapter = candidate.phase_adapter
        if not callable(getattr(adapter, "execute_phase", None)):
            _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_INTERFACE_INVALID")
        if id(adapter) in adapter_ids:
            _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DUPLICATE_ADAPTER_MATERIAL")
        adapter_ids.add(id(adapter))
        if (
            candidate is not composition.phase_bindings.get(name)
            or adapter is not composition_adapters.get(name)
            or candidate.phase_name != name
            or candidate.phase_sequence != phase.sequence
            or candidate.oracle != phase.oracle
            or candidate.transport_profile != phase.transport_profile
            or candidate.destructive is not phase.destructive
            or candidate.campaign_id != composition.campaign_id
            or candidate.release_sha != composition.release_sha
            or candidate.policy_sha256 != composition.policy_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PHASE_MATERIAL_MISMATCH")
        material[name] = candidate
    return MappingProxyType(material)


def _anchor(
    value: object,
    *,
    composition: _root_composition.PhysicalFullMatrixV4RootComposition,
    phase_material: Mapping[str, _root_composition.PhysicalFullMatrixV4RootPhaseAdapterBinding],
) -> PhysicalFullMatrixV4MaterializationWitnessAnchorBinding:
    if type(value) is not PhysicalFullMatrixV4MaterializationWitnessAnchorBinding:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_WITNESS_ANCHOR_REQUIRED")
    identity = value.identity
    anchor = value.anchor
    if type(identity) is not _anchor_wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_WITNESS_IDENTITY_INVALID")
    if (
        identity.schema
        != _anchor_wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA
        or not isinstance(identity.run_id, UUID)
        or identity.run_id.int == 0
        or identity.run_id != composition.run_id
        or identity.plan_sha256 != composition.plan_sha256
        or type(identity.anchor_genesis_sequence) is not int
        or identity.anchor_genesis_sequence < 0
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_WITNESS_IDENTITY_MISMATCH")
    for item in (
        identity.journal_binding_sha256,
        identity.baseline_plan_binding_sha256,
        identity.plan_sha256,
        identity.anchor_genesis_head_sha256,
        identity.canonical_genesis_sha256,
    ):
        _sha(item, code="PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_WITNESS_IDENTITY_INVALID")
    if not callable(getattr(anchor, "read_head", None)) or not callable(
        getattr(anchor, "append_commitment", None)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_WITNESS_INTERFACE_INVALID")
    if any(
        anchor is binding.phase_adapter for binding in phase_material.values()
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_DUPLICATE_ADAPTER_MATERIAL")
    return value


def _preflight_sha256(
    *,
    composition: _root_composition.PhysicalFullMatrixV4RootComposition,
    identity: _anchor_wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    prepared_at: datetime,
) -> str:
    payload = {
        "schema": PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA,
        "campaign_id": composition.campaign_id,
        "release_sha": composition.release_sha,
        "run_id": str(composition.run_id),
        "prepared_at": prepared_at.isoformat().replace("+00:00", "Z"),
        "plan_sha256": composition.plan_sha256,
        "policy_sha256": composition.policy_sha256,
        "readiness_binding_sha256": composition.plan.binding.readiness_binding_sha256,
        "adapter_names": PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_REQUIRED_PHASE_NAMES,
        "witness_anchor_identity": {
            "schema": identity.schema,
            "journal_binding_sha256": identity.journal_binding_sha256,
            "baseline_plan_binding_sha256": identity.baseline_plan_binding_sha256,
            "run_id": str(identity.run_id),
            "plan_sha256": identity.plan_sha256,
            "anchor_genesis_sequence": identity.anchor_genesis_sequence,
            "anchor_genesis_head_sha256": identity.anchor_genesis_head_sha256,
            "canonical_genesis_sha256": identity.canonical_genesis_sha256,
        },
        "materialization_authorized": False,
        "host_provider_installation_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "execution_authorized": False,
    }
    try:
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4MaterializationPreflightError(
            "PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_HASH_INVALID"
        ) from exc


def _derive(
    *,
    config: object,
    inputs: object,
    clock_floor: datetime | None = None,
) -> _Facts:
    _config(config)
    if type(inputs) is not PhysicalFullMatrixV4MaterializationPreflightInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_INPUTS_INVALID")
    composition = _require_composition(inputs.composition)
    observed_at = _trusted_now(
        inputs.trusted_clock,
        composition=composition,
        floor=clock_floor,
    )
    readiness = _readiness(
        inputs.readiness,
        composition=composition,
        now=observed_at,
    )
    material = _phase_material(inputs.phase_adapter_material, composition=composition)
    anchor = _anchor(
        inputs.witness_anchor,
        composition=composition,
        phase_material=material,
    )
    assert anchor.identity is not None
    return _Facts(
        composition=composition,
        readiness=readiness,
        phase_material=material,
        anchor=anchor,
        campaign_id=composition.campaign_id,
        release_sha=composition.release_sha,
        run_id=composition.run_id,
        observed_at=observed_at,
        plan_sha256=composition.plan_sha256,
        policy_sha256=composition.policy_sha256,
        readiness_binding_sha256=composition.plan.binding.readiness_binding_sha256,
    )


def _result_from_facts(
    _facts: _Facts,
    *,
    prepared_at: datetime | None = None,
) -> PhysicalFullMatrixV4MaterializationPreflight:
    assert _facts.anchor.identity is not None
    pinned_prepared_at = _facts.observed_at if prepared_at is None else prepared_at
    return PhysicalFullMatrixV4MaterializationPreflight(
        schema=PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_SCHEMA,
        preflight_sha256=_preflight_sha256(
            composition=_facts.composition,
            identity=_facts.anchor.identity,
            prepared_at=pinned_prepared_at,
        ),
        campaign_id=_facts.campaign_id,
        release_sha=_facts.release_sha,
        run_id=_facts.run_id,
        prepared_at=pinned_prepared_at,
        plan_sha256=_facts.plan_sha256,
        policy_sha256=_facts.policy_sha256,
        readiness_binding_sha256=_facts.readiness_binding_sha256,
        adapter_names=PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_REQUIRED_PHASE_NAMES,
        witness_anchor_identity=_facts.anchor.identity,
    )


def _assert_result(
    value: object,
    *,
    expected: PhysicalFullMatrixV4MaterializationPreflight,
) -> PhysicalFullMatrixV4MaterializationPreflight:
    if type(value) is not PhysicalFullMatrixV4MaterializationPreflight:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CAPABILITY_REQUIRED")
    for name in (
        "schema",
        "preflight_sha256",
        "campaign_id",
        "release_sha",
        "run_id",
        "prepared_at",
        "plan_sha256",
        "policy_sha256",
        "readiness_binding_sha256",
        "adapter_names",
        "witness_anchor_identity",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CAPABILITY_TAMPERED")
    if (
        value.materialization_authorized is not False
        or value.host_provider_installation_authorized is not False
        or value.promotion_authorized is not False
        or value.writer_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CAPABILITY_TAMPERED")
    return value


def prepare_physical_full_matrix_v4_materialization_preflight(
    *,
    config: PhysicalFullMatrixV4MaterializationPreflightConfig,
    inputs: PhysicalFullMatrixV4MaterializationPreflightInputs,
) -> PhysicalFullMatrixV4MaterializationPreflight:
    """Prepare opaque diagnostics without installing or invoking an effect."""

    facts = _derive(config=config, inputs=inputs)
    result = _result_from_facts(facts)
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = _State(config=config, inputs=inputs, facts=facts)
    return _assert_result(result, expected=result)


def require_prepared_physical_full_matrix_v4_materialization_preflight(
    value: object,
    *,
    config: PhysicalFullMatrixV4MaterializationPreflightConfig,
) -> PhysicalFullMatrixV4MaterializationPreflight:
    """Freshly recheck readiness/material; still never install or execute it."""

    if (
        type(value) is not PhysicalFullMatrixV4MaterializationPreflight
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or config != state.config:
        _fail("PHYSICAL_FULL_MATRIX_V4_MATERIALIZATION_PREFLIGHT_PROVENANCE_MISSING")
    fresh_facts = _derive(
        config=config,
        inputs=state.inputs,
        clock_floor=state.facts.observed_at,
    )
    expected = _result_from_facts(
        fresh_facts,
        prepared_at=state.facts.observed_at,
    )
    return _assert_result(value, expected=expected)
