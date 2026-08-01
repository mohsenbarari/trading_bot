"""Pure, default-off provenance binding for a future V4 Phase-6 source FD.

This is deliberately *not* a recovery-input or materialization interface.
It cross-pins a caller-injected descriptor observation to the exact canonical
reverse bundle already admitted for Phase 6.  No descriptor is opened,
``fstat``-ed, duplicated, read, closed, or otherwise acted upon here; the
observation is only a claim made by a future separately reviewed root-owned
descriptor attester.  In particular, this module has no transport, storage,
path, command-execution, database, or runtime dependency.

The existing FD-only binder accepts only an empty WA-FI target.  A future
source/staging-FD seam must require this opaque, process-local provenance *and*
independently verify the actual descriptor before it may use any source FD.
This evidence grants none of those authorities itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    OBJECT_KEY_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core import physical_full_matrix_v4_phase6_failback_rebuild_admission as _admission


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_STATUS",
    "PhysicalFullMatrixV4Phase6InjectedStagedDescriptor",
    "PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding",
    "PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig",
    "PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError",
    "PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs",
    "bind_physical_full_matrix_v4_phase6_reverse_bundle_descriptor",
    "require_bound_physical_full_matrix_v4_phase6_reverse_bundle_descriptor",
)


PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-reverse-bundle-descriptor-binding-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_STATUS = (
    "reverse-bundle-staged-descriptor-bound-evidence-only"
)

_PLAN_SCHEMA = "gold-trade-physical-full-matrix-v4-phase6-reverse-recovery-plan-v1"
_PLAN_STATUS = "canonical-reverse-recovery-plan-evidence-only"
_DESCRIPTOR_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-injected-staged-descriptor-v1"
)
_DESCRIPTOR_STATUS = "injected-staged-descriptor-identity-evidence-only"
_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_FORBIDDEN = "forbidden"
_ZERO_SHA256 = "0" * 64
_MAX_WIRE_BYTES = 64 * 1024
_CAPABILITY = object()

_PLAN_FIELDS = frozenset(
    {
        "schema",
        "status",
        "plan_id",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "object_storage_namespace",
        "route_binding_sha256",
        "four_role_binding_sha256",
        "phase5_completion_receipt_sha256",
        "phase5_completion_anchor_sequence",
        "phase5_completion_anchor_head_sha256",
        "phase5_completion_anchor_commitment_sha256",
        "phase5_completion_anchor_attestation_sha256",
        "writer_term",
        "bundle_id",
        "stage_receipt_sha256",
        "manifest_sha256es",
        "object_versions",
        "terminal_wal_lsn",
        "recovery_evidence_sha256",
        "recovery_bundle_binding_sha256",
    }
)
_OBJECT_VERSION_FIELDS = frozenset({"object_key", "version_id"})
_DESCRIPTOR_KIND = "staged-recovery-directory"
_DESCRIPTOR_ACCESS = "read-only"
_IDENTITY_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError(ValueError):
    """A redacted refusal from the non-authorizing P6 provenance seam."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig:
    """Root policy pins; disabled unless an explicit exact campaign is selected."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SCHEMA
    expected_admission_sha256: str = ""
    expected_reverse_recovery_plan_sha256: str = ""
    expected_route_binding_sha256: str = ""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_DEFAULT_ENABLED
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    source_descriptor_use_authorized: bool = False
    materialization_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6InjectedStagedDescriptor:
    """A declarative descriptor observation, never a descriptor verifier.

    ``staged_recovery_fd`` and its device/inode values are supplied by a future
    trusted attester.  This class does not make their truth claim: it gives a
    reviewed later seam a canonical identity to verify against the live FD.
    """

    schema: str
    status: str
    staged_recovery_fd: int
    descriptor_device: int
    descriptor_inode: int
    descriptor_identity_sha256: str
    descriptor_kind: str
    descriptor_access: str
    source_site: str
    destination_site: str
    route_binding_sha256: str
    reverse_recovery_plan_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    object_versions_sha256: str
    recovery_bundle_binding_sha256: str
    source_descriptor_use_authorized: bool = False
    materialization_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs:
    """Only already-admitted P6 evidence and one injected descriptor claim."""

    admission: object | None = field(default=None, repr=False, compare=False)
    reverse_recovery_plan: (
        _admission.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence | None
    ) = field(default=None, repr=False, compare=False)
    injected_staged_descriptor: PhysicalFullMatrixV4Phase6InjectedStagedDescriptor | None = (
        field(default=None, repr=False, compare=False)
    )


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding:
    """Opaque descriptor-to-bundle provenance; never a source-FD permit."""

    schema: str
    status: str
    binding_sha256: str
    admission_sha256: str
    run_id: str
    phase6_effect_start_identity_sha256: str
    reverse_recovery_plan_sha256: str
    route_binding_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    object_versions_sha256: str
    recovery_bundle_binding_sha256: str
    descriptor_identity_sha256: str
    staged_recovery_fd: int
    descriptor_device: int
    descriptor_inode: int
    source_descriptor_use_authorized: bool = False
    materialization_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SERIALIZATION_FORBIDDEN"
        )

    def __copy__(self) -> object:
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_COPY_FORBIDDEN"
        )

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_COPY_FORBIDDEN"
        )


@dataclass(frozen=True)
class _PlanFacts:
    plan_sha256: str
    route_binding_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    object_versions_sha256: str
    recovery_bundle_binding_sha256: str


@dataclass(frozen=True)
class _Facts:
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission
    plan: _PlanFacts
    descriptor: PhysicalFullMatrixV4Phase6InjectedStagedDescriptor


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding, _Facts] = (
    WeakKeyDictionary()
)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _IDENTITY_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _canonical_mapping(raw: object) -> dict[str, object]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_WIRE_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")

    def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if type(key) is not str or key in result:
                _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=_no_duplicates)
        if type(payload) is not dict or set(payload) != _PLAN_FIELDS:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
        if canonical_json_bytes(payload) != raw:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError,
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    return payload


def _config(value: object) -> PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig:
    if type(value) is not PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SCHEMA
        or value.enabled is not True
        or value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or value.source_descriptor_use_authorized is not False
        or value.materialization_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_CONFIG_INVALID")
    for item in (
        value.expected_admission_sha256,
        value.expected_reverse_recovery_plan_sha256,
        value.expected_route_binding_sha256,
    ):
        _sha256(item, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_CONFIG_INVALID")
    return value


def _admitted(value: object, *, config: PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig) -> _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    try:
        admitted = _admission.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(value)
    except _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError as exc:
        raise PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_ADMISSION_REQUIRED"
        ) from exc
    if (
        admitted.admission_sha256 != config.expected_admission_sha256
        or admitted.reverse_recovery_plan_sha256 != config.expected_reverse_recovery_plan_sha256
        or admitted.route_binding_sha256 != config.expected_route_binding_sha256
        or admitted.fd_binder_authorized is not False
        or admitted.runner_authorized is not False
        or admitted.materialization_authorized is not False
        or admitted.promotion_authorized is not False
        or admitted.writer_authorized is not False
        or admitted.traffic_switch_authorized is not False
        or admitted.execution_authorized is not False
        or admitted.full_matrix_authorized is not False
        or admitted.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_ADMISSION_MISMATCH")
    return admitted


def _plan(value: object, *, admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission, config: PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig) -> _PlanFacts:
    if type(value) is not _admission.PhysicalFullMatrixV4Phase6ReverseRecoveryPlanEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_REQUIRED")
    payload = _canonical_mapping(value.canonical_plan)
    plan_sha256 = _sha256(value.plan_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    if hashlib.sha256(value.canonical_plan).hexdigest() != plan_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    required_equal = {
        "schema": _PLAN_SCHEMA,
        "status": _PLAN_STATUS,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "route_binding_sha256": admission.route_binding_sha256,
        "bundle_id": admission.bundle_id,
        "stage_receipt_sha256": admission.stage_receipt_sha256,
    }
    if any(payload.get(name) != expected for name, expected in required_equal.items()):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_MISMATCH")
    if (
        plan_sha256 != admission.reverse_recovery_plan_sha256
        or plan_sha256 != config.expected_reverse_recovery_plan_sha256
        or payload["route_binding_sha256"] != config.expected_route_binding_sha256
        or payload["campaign_id"] != admission.campaign_id
        or payload["release_sha"] != admission.release_sha
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_MISMATCH")
    for name in (
        "route_binding_sha256",
        "bundle_id",
        "stage_receipt_sha256",
        "recovery_bundle_binding_sha256",
    ):
        _sha256(payload[name], code="PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    versions = payload["object_versions"]
    if type(versions) is not list or not versions:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
    canonical_versions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in versions:
        if type(item) is not dict or set(item) != _OBJECT_VERSION_FIELDS:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
        object_key, version_id = item["object_key"], item["version_id"]
        if (
            type(object_key) is not str
            or OBJECT_KEY_RE.fullmatch(object_key) is None
            or ".." in object_key.split("/")
            or type(version_id) is not str
            or VERSION_ID_RE.fullmatch(version_id) is None
            or (object_key, version_id) in seen
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_PLAN_INVALID")
        seen.add((object_key, version_id))
        canonical_versions.append({"object_key": object_key, "version_id": version_id})
    return _PlanFacts(
        plan_sha256=plan_sha256,
        route_binding_sha256=payload["route_binding_sha256"],
        bundle_id=payload["bundle_id"],
        stage_receipt_sha256=payload["stage_receipt_sha256"],
        object_versions_sha256=hashlib.sha256(canonical_json_bytes(canonical_versions)).hexdigest(),
        recovery_bundle_binding_sha256=payload["recovery_bundle_binding_sha256"],
    )


def _descriptor_identity_payload(value: PhysicalFullMatrixV4Phase6InjectedStagedDescriptor) -> dict[str, object]:
    return {
        "schema": _DESCRIPTOR_SCHEMA,
        "status": _DESCRIPTOR_STATUS,
        "staged_recovery_fd": value.staged_recovery_fd,
        "descriptor_device": value.descriptor_device,
        "descriptor_inode": value.descriptor_inode,
        "descriptor_kind": _DESCRIPTOR_KIND,
        "descriptor_access": _DESCRIPTOR_ACCESS,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "route_binding_sha256": value.route_binding_sha256,
        "reverse_recovery_plan_sha256": value.reverse_recovery_plan_sha256,
        "bundle_id": value.bundle_id,
        "stage_receipt_sha256": value.stage_receipt_sha256,
        "object_versions_sha256": value.object_versions_sha256,
        "recovery_bundle_binding_sha256": value.recovery_bundle_binding_sha256,
        "source_descriptor_use_authorized": False,
        "materialization_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def _descriptor(value: object, *, plan: _PlanFacts) -> PhysicalFullMatrixV4Phase6InjectedStagedDescriptor:
    if type(value) is not PhysicalFullMatrixV4Phase6InjectedStagedDescriptor:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_REQUIRED")
    if (
        value.schema != _DESCRIPTOR_SCHEMA
        or value.status != _DESCRIPTOR_STATUS
        or type(value.staged_recovery_fd) is not int
        or value.staged_recovery_fd < 0
        or type(value.descriptor_device) is not int
        or value.descriptor_device < 0
        or type(value.descriptor_inode) is not int
        or value.descriptor_inode < 1
        or value.descriptor_kind != _DESCRIPTOR_KIND
        or value.descriptor_access != _DESCRIPTOR_ACCESS
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.source_descriptor_use_authorized is not False
        or value.materialization_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_INVALID")
    for actual, expected in (
        (value.route_binding_sha256, plan.route_binding_sha256),
        (value.reverse_recovery_plan_sha256, plan.plan_sha256),
        (value.bundle_id, plan.bundle_id),
        (value.stage_receipt_sha256, plan.stage_receipt_sha256),
        (value.object_versions_sha256, plan.object_versions_sha256),
        (value.recovery_bundle_binding_sha256, plan.recovery_bundle_binding_sha256),
    ):
        if actual != expected:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_MISMATCH")
        _sha256(actual, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_INVALID")
    identity = _sha256(
        value.descriptor_identity_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_INVALID",
    )
    if hashlib.sha256(canonical_json_bytes(_descriptor_identity_payload(value))).hexdigest() != identity:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_INVALID")
    return value


def _projection(facts: _Facts) -> dict[str, object]:
    admission, plan, descriptor = facts.admission, facts.plan, facts.descriptor
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_BINDING_STATUS,
        "admission_sha256": admission.admission_sha256,
        "run_id": str(admission.run_id),
        "phase6_effect_start_identity_sha256": admission.phase6_effect_start_identity_sha256,
        "reverse_recovery_plan_sha256": plan.plan_sha256,
        "route_binding_sha256": plan.route_binding_sha256,
        "bundle_id": plan.bundle_id,
        "stage_receipt_sha256": plan.stage_receipt_sha256,
        "object_versions_sha256": plan.object_versions_sha256,
        "recovery_bundle_binding_sha256": plan.recovery_bundle_binding_sha256,
        "descriptor_identity_sha256": descriptor.descriptor_identity_sha256,
        "staged_recovery_fd": descriptor.staged_recovery_fd,
        "descriptor_device": descriptor.descriptor_device,
        "descriptor_inode": descriptor.descriptor_inode,
        "source_descriptor_use_authorized": False,
        "materialization_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return {**body, "binding_sha256": hashlib.sha256(canonical_json_bytes(body)).hexdigest()}


def bind_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(
    *,
    config: PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingConfig,
    inputs: PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs,
) -> PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding:
    """Bind exact P6 evidence to an injected descriptor claim, without I/O."""

    checked_config = _config(config)
    if type(inputs) is not PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_INPUTS_INVALID")
    admitted = _admitted(inputs.admission, config=checked_config)
    plan = _plan(inputs.reverse_recovery_plan, admission=admitted, config=checked_config)
    descriptor = _descriptor(inputs.injected_staged_descriptor, plan=plan)
    facts = _Facts(admission=admitted, plan=plan, descriptor=descriptor)
    result = PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding(**_projection(facts))
    object.__setattr__(result, "_capability", _CAPABILITY)
    _STATES[result] = facts
    return result


def require_bound_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(
    value: object,
) -> PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding:
    """Require untampered process-local provenance; it remains non-authorizing."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_UNAUTHORIZED")
    facts = _STATES.get(value)
    if facts is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_UNAUTHORIZED")
    expected = _projection(facts)
    for name, expected_value in expected.items():
        if getattr(value, name) != expected_value:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_REVERSE_BUNDLE_DESCRIPTOR_TAMPERED")
    return value
