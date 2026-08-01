"""Root-only, non-materializing handoff preparation for V4 Phase 6.

This is the narrow seam between the already-attested staged reverse bundle and
the already-bound, empty WA-FI PGDATA destination.  It is deliberately not a
recovery runner: it does not open paths, enumerate or read either directory,
decrypt, download, invoke a command, contact a service, or alter PostgreSQL,
writer, promotion, or traffic state.

The only descriptor operation is duplicating two *previously verified* file
descriptors into non-inheritable process-local handles.  That makes a future,
separately reviewed root-owned runner receive an exact, fail-closed handoff
rather than uncorrelated caller-provided descriptor integers.  This handoff is
evidence only and grants no materialization authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_phase6_failback_rebuild_admission as _admission
from core import physical_full_matrix_v4_phase6_fd_only_rebuild_binder as _target
from core import physical_full_matrix_v4_phase6_source_fd_attestation as _source


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_STATUS",
    "PhysicalFullMatrixV4Phase6ReconstructionHandoff",
    "PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig",
    "PhysicalFullMatrixV4Phase6ReconstructionHandoffError",
    "PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs",
    "prepare_physical_full_matrix_v4_phase6_reconstruction_handoff",
    "require_prepared_physical_full_matrix_v4_phase6_reconstruction_handoff",
)


PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-reconstruction-handoff-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_STATUS = (
    "root-gated-source-target-fd-handoff-prepared-evidence-only"
)

_ROOT_UID = 0
_FORBIDDEN = "forbidden"
_CAPABILITY = object()


class PhysicalFullMatrixV4Phase6ReconstructionHandoffError(ValueError):
    """A redacted refusal from the non-materializing P6 handoff boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase6ReconstructionHandoffError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig:
    """Default-off pins for precisely one previously admitted Phase-6 run."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SCHEMA
    expected_admission_sha256: str = ""
    expected_target_binding_sha256: str = ""
    expected_source_attestation_sha256: str = ""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DEFAULT_ENABLED
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    handoff_authorized: bool = False
    materialization_authorized: bool = False
    runner_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs:
    """Only opaque upstream provenance; neither path nor raw FD is accepted."""

    admission: object | None = field(default=None, repr=False, compare=False)
    target_binding: object | None = field(default=None, repr=False, compare=False)
    source_attestation: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase6ReconstructionHandoff:
    """Opaque, process-local source/target descriptor handoff evidence.

    The caller owns and must close both descriptor handles.  They remain
    diagnostic handles only; all authorization flags are intentionally false.
    """

    schema: str
    status: str
    handoff_sha256: str
    admission_sha256: str
    target_binding_sha256: str
    source_attestation_sha256: str
    run_id: str
    plan_sha256: str
    phase6_effect_start_identity_sha256: str
    reverse_recovery_plan_sha256: str
    route_binding_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    source_descriptor_identity_sha256: str
    source_staged_recovery_fd: int
    target_pgdata_fd: int
    handoff_authorized: bool = False
    materialization_authorized: bool = False
    runner_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission
    target: _target.PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget
    source: _source.PhysicalFullMatrixV4Phase6SourceFdAttestation
    source_identity: tuple[int, int]
    target_identity: tuple[int, int]
    source_handoff_fd: int
    target_handoff_fd: int


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase6ReconstructionHandoff, _State] = (
    WeakKeyDictionary()
)


def _require_root() -> None:
    try:
        if os.geteuid() != _ROOT_UID:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_ROOT_REQUIRED")
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_ROOT_REQUIRED")


def _sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == "0" * 64
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_CONFIG_INVALID")
    return value


def _config(value: object) -> PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig:
    if type(value) is not PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SCHEMA
        or value.enabled is not True
        or value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or any(
            getattr(value, name) is not False
            for name in (
                "handoff_authorized",
                "materialization_authorized",
                "runner_authorized",
                "promotion_authorized",
                "writer_authorized",
                "traffic_switch_authorized",
                "execution_authorized",
                "full_matrix_authorized",
                "full_matrix_executed",
            )
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_CONFIG_INVALID")
    _sha256(value.expected_admission_sha256)
    _sha256(value.expected_target_binding_sha256)
    _sha256(value.expected_source_attestation_sha256)
    return value


def _admitted(value: object) -> _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    try:
        result = _admission.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(value)
    except _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError as exc:
        raise PhysicalFullMatrixV4Phase6ReconstructionHandoffError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_ADMISSION_REQUIRED"
        ) from exc
    return result


def _target_bound(value: object) -> _target.PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget:
    try:
        return _target.require_bound_physical_full_matrix_v4_phase6_fd_only_rebuild_target(value)
    except _target.PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError as exc:
        raise PhysicalFullMatrixV4Phase6ReconstructionHandoffError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_TARGET_REQUIRED"
        ) from exc


def _source_attested(value: object) -> _source.PhysicalFullMatrixV4Phase6SourceFdAttestation:
    try:
        return _source.require_attested_physical_full_matrix_v4_phase6_source_fd(value)
    except _source.PhysicalFullMatrixV4Phase6SourceFdAttestationError as exc:
        raise PhysicalFullMatrixV4Phase6ReconstructionHandoffError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SOURCE_REQUIRED"
        ) from exc


def _identity(fd: int) -> tuple[int, int]:
    try:
        details = os.fstat(fd)
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DESCRIPTOR_UNSAFE")
    return details.st_dev, details.st_ino


def _checked_handoff_descriptor(fd: int, *, expected_identity: tuple[int, int]) -> None:
    try:
        if os.get_inheritable(fd) or _identity(fd) != expected_identity:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DESCRIPTOR_UNSAFE")
    except PhysicalFullMatrixV4Phase6ReconstructionHandoffError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DESCRIPTOR_UNSAFE")


def _correlate(
    *,
    config: PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig,
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission,
    target: _target.PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget,
    source: _source.PhysicalFullMatrixV4Phase6SourceFdAttestation,
) -> None:
    if (
        admission.admission_sha256 != config.expected_admission_sha256
        or target.binding_sha256 != config.expected_target_binding_sha256
        or source.attestation_sha256 != config.expected_source_attestation_sha256
        or target.admission_sha256 != admission.admission_sha256
        or source.admission_sha256 != admission.admission_sha256
        or target.run_id != str(admission.run_id)
        or source.run_id != str(admission.run_id)
        or target.plan_sha256 != admission.plan_sha256
        or target.phase6_effect_start_identity_sha256
        != admission.phase6_effect_start_identity_sha256
        or source.phase6_effect_start_identity_sha256
        != admission.phase6_effect_start_identity_sha256
        or target.reverse_recovery_plan_sha256 != admission.reverse_recovery_plan_sha256
        or source.reverse_recovery_plan_sha256 != admission.reverse_recovery_plan_sha256
        or source.route_binding_sha256 != admission.route_binding_sha256
        or source.bundle_id != admission.bundle_id
        or source.stage_receipt_sha256 != admission.stage_receipt_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_PROVENANCE_MISMATCH")


def _projection(
    *,
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission,
    target: _target.PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget,
    source: _source.PhysicalFullMatrixV4Phase6SourceFdAttestation,
    source_fd: int,
    target_fd: int,
) -> dict[str, object]:
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_STATUS,
        "admission_sha256": admission.admission_sha256,
        "target_binding_sha256": target.binding_sha256,
        "source_attestation_sha256": source.attestation_sha256,
        "run_id": str(admission.run_id),
        "plan_sha256": admission.plan_sha256,
        "phase6_effect_start_identity_sha256": admission.phase6_effect_start_identity_sha256,
        "reverse_recovery_plan_sha256": admission.reverse_recovery_plan_sha256,
        "route_binding_sha256": admission.route_binding_sha256,
        "bundle_id": admission.bundle_id,
        "stage_receipt_sha256": admission.stage_receipt_sha256,
        "source_descriptor_identity_sha256": source.descriptor_identity_sha256,
        "source_staged_recovery_fd": source_fd,
        "target_pgdata_fd": target_fd,
        "handoff_authorized": False,
        "materialization_authorized": False,
        "runner_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return {**body, "handoff_sha256": hashlib.sha256(canonical_json_bytes(body)).hexdigest()}


def prepare_physical_full_matrix_v4_phase6_reconstruction_handoff(
    *,
    config: PhysicalFullMatrixV4Phase6ReconstructionHandoffConfig,
    inputs: PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs,
) -> PhysicalFullMatrixV4Phase6ReconstructionHandoff:
    """Prepare exact source/target duplicates; never materialize recovery data."""

    _require_root()
    checked_config = _config(config)
    if type(inputs) is not PhysicalFullMatrixV4Phase6ReconstructionHandoffInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_INPUTS_INVALID")
    admission = _admitted(inputs.admission)
    target = _target_bound(inputs.target_binding)
    source = _source_attested(inputs.source_attestation)
    _correlate(config=checked_config, admission=admission, target=target, source=source)

    source_identity = _identity(source.staged_recovery_fd)
    target_identity = _identity(target.target_pgdata_fd)
    if source_identity == target_identity:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_SOURCE_TARGET_ALIAS")
    source_duplicate = target_duplicate = -1
    try:
        source_duplicate = os.dup(source.staged_recovery_fd)
        target_duplicate = os.dup(target.target_pgdata_fd)
        os.set_inheritable(source_duplicate, False)
        os.set_inheritable(target_duplicate, False)
        _checked_handoff_descriptor(source_duplicate, expected_identity=source_identity)
        _checked_handoff_descriptor(target_duplicate, expected_identity=target_identity)
        if _identity(source_duplicate) == _identity(target_duplicate):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DESCRIPTOR_UNSAFE")
        # Revalidate the original opaque capabilities after duplication.  This
        # prevents a closed/reused upstream FD from silently becoming a valid
        # handoff input between the initial check and return.
        _target_bound(target)
        _source_attested(source)
        if (
            _identity(source.staged_recovery_fd) != source_identity
            or _identity(target.target_pgdata_fd) != target_identity
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_DESCRIPTOR_UNSAFE")
        result = PhysicalFullMatrixV4Phase6ReconstructionHandoff(
            **_projection(
                admission=admission,
                target=target,
                source=source,
                source_fd=source_duplicate,
                target_fd=target_duplicate,
            )
        )
        object.__setattr__(result, "_capability", _CAPABILITY)
        _STATES[result] = _State(
            admission=admission,
            target=target,
            source=source,
            source_identity=source_identity,
            target_identity=target_identity,
            source_handoff_fd=source_duplicate,
            target_handoff_fd=target_duplicate,
        )
        return result
    except Exception:
        for fd in (source_duplicate, target_duplicate):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        raise


def require_prepared_physical_full_matrix_v4_phase6_reconstruction_handoff(
    value: object,
) -> PhysicalFullMatrixV4Phase6ReconstructionHandoff:
    """Require live, untampered handoff provenance; it remains non-authorizing."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase6ReconstructionHandoff
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_UNAUTHORIZED")
    state = _STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_UNAUTHORIZED")
    _admitted(state.admission)
    _target_bound(state.target)
    _source_attested(state.source)
    expected = _projection(
        admission=state.admission,
        target=state.target,
        source=state.source,
        source_fd=value.source_staged_recovery_fd,
        target_fd=value.target_pgdata_fd,
    )
    if any(getattr(value, name) != expected_value for name, expected_value in expected.items()):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_TAMPERED")
    if (
        value.source_staged_recovery_fd != state.source_handoff_fd
        or value.target_pgdata_fd != state.target_handoff_fd
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_TAMPERED")
    try:
        _checked_handoff_descriptor(
            value.source_staged_recovery_fd, expected_identity=state.source_identity
        )
        _checked_handoff_descriptor(
            value.target_pgdata_fd, expected_identity=state.target_identity
        )
    except PhysicalFullMatrixV4Phase6ReconstructionHandoffError as exc:
        raise PhysicalFullMatrixV4Phase6ReconstructionHandoffError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_TAMPERED"
        ) from exc
    if state.source_identity == state.target_identity:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_RECONSTRUCTION_HANDOFF_TAMPERED")
    return value
