"""Root-gated, FD-only attestation seam for a future V4 Phase-6 source.

This deliberately stops *before* recovery.  It receives no path and never
opens, walks, lists, reads, writes, closes, or otherwise materializes staged
content.  It merely duplicates an already-open root-owned, read-only staging
directory descriptor after cross-checking it against both the opaque P6
admission and the exact opaque reverse-bundle descriptor provenance.

The returned descriptor is a non-inheritable diagnostic handle only.  It is
not a permit to read recovery material, restore PostgreSQL, invoke a runner,
contact Object Storage, change writer/traffic state, or execute Full Matrix.
Those operations require a separately reviewed, owner-specific runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import os
import stat
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_phase6_failback_rebuild_admission as _admission
from core import physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding as _binding


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_STATUS",
    "PhysicalFullMatrixV4Phase6SourceFdAttestation",
    "PhysicalFullMatrixV4Phase6SourceFdAttestationConfig",
    "PhysicalFullMatrixV4Phase6SourceFdAttestationError",
    "PhysicalFullMatrixV4Phase6SourceFdAttestationInputs",
    "attest_physical_full_matrix_v4_phase6_source_fd",
    "require_attested_physical_full_matrix_v4_phase6_source_fd",
)


PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-source-fd-attestation-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_STATUS = (
    "root-gated-staged-source-fd-attested-evidence-only"
)

_ROOT_UID = 0
_CAPABILITY = object()
_FORBIDDEN = "forbidden"


class PhysicalFullMatrixV4Phase6SourceFdAttestationError(ValueError):
    """A redacted refusal from the non-executing P6 source-FD seam."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase6SourceFdAttestationError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6SourceFdAttestationConfig:
    """Default-off root policy pins for one exact P6 source descriptor."""

    schema: str = PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SCHEMA
    expected_admission_sha256: str = ""
    expected_reverse_bundle_descriptor_binding_sha256: str = ""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DEFAULT_ENABLED
    direct_fi_to_ir_control: str = _FORBIDDEN
    direct_ir_to_fi_control: str = _FORBIDDEN
    source_descriptor_use_authorized: bool = False
    fd_attester_authorized: bool = False
    materialization_authorized: bool = False
    runner_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False


@dataclass(frozen=True)
class PhysicalFullMatrixV4Phase6SourceFdAttestationInputs:
    """Only opaque P6 provenance and one already-open staged directory FD."""

    admission: object | None = field(default=None, repr=False, compare=False)
    reverse_bundle_descriptor_binding: object | None = field(
        default=None, repr=False, compare=False
    )
    staged_recovery_fd: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase6SourceFdAttestation:
    """Opaque provenance with one duplicate non-inheritable source FD.

    The caller owns and must close ``staged_recovery_fd``.  This diagnostic
    descriptor remains non-authorizing even though it is a live FD.
    """

    schema: str
    status: str
    attestation_sha256: str
    admission_sha256: str
    reverse_bundle_descriptor_binding_sha256: str
    run_id: str
    phase6_effect_start_identity_sha256: str
    reverse_recovery_plan_sha256: str
    route_binding_sha256: str
    bundle_id: str
    stage_receipt_sha256: str
    recovery_bundle_binding_sha256: str
    descriptor_identity_sha256: str
    staged_recovery_fd: int
    descriptor_device: int
    descriptor_inode: int
    source_descriptor_use_authorized: bool = False
    fd_attester_authorized: bool = False
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
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission
    binding: _binding.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding
    device: int
    inode: int


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase6SourceFdAttestation, _State] = (
    WeakKeyDictionary()
)


def _require_root() -> None:
    try:
        if os.geteuid() != _ROOT_UID:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_ROOT_REQUIRED")
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_ROOT_REQUIRED")


def _sha256(value: object, *, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == "0" * 64
    ):
        _fail(code)
    return value


def _config(value: object) -> PhysicalFullMatrixV4Phase6SourceFdAttestationConfig:
    if type(value) is not PhysicalFullMatrixV4Phase6SourceFdAttestationConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_CONFIG_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SCHEMA
        or value.enabled is not True
        or value.direct_fi_to_ir_control != _FORBIDDEN
        or value.direct_ir_to_fi_control != _FORBIDDEN
        or value.source_descriptor_use_authorized is not False
        or value.fd_attester_authorized is not False
        or value.materialization_authorized is not False
        or value.runner_authorized is not False
        or value.promotion_authorized is not False
        or value.writer_authorized is not False
        or value.traffic_switch_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_CONFIG_INVALID")
    _sha256(value.expected_admission_sha256, code="PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_CONFIG_INVALID")
    _sha256(
        value.expected_reverse_bundle_descriptor_binding_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_CONFIG_INVALID",
    )
    return value


def _admitted(value: object) -> _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    try:
        result = _admission.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(value)
    except _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError as exc:
        raise PhysicalFullMatrixV4Phase6SourceFdAttestationError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_ADMISSION_REQUIRED"
        ) from exc
    if any(
        getattr(result, name) is not False
        for name in (
            "fd_binder_authorized",
            "runner_authorized",
            "materialization_authorized",
            "promotion_authorized",
            "writer_authorized",
            "traffic_switch_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_ADMISSION_INVALID")
    return result


def _bound(value: object) -> _binding.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding:
    try:
        result = _binding.require_bound_physical_full_matrix_v4_phase6_reverse_bundle_descriptor(
            value
        )
    except _binding.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBindingError as exc:
        raise PhysicalFullMatrixV4Phase6SourceFdAttestationError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_BINDING_REQUIRED"
        ) from exc
    if any(
        getattr(result, name) is not False
        for name in (
            "source_descriptor_use_authorized",
            "materialization_authorized",
            "execution_authorized",
            "full_matrix_authorized",
            "full_matrix_executed",
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_BINDING_INVALID")
    return result


def _correlate(
    *,
    config: PhysicalFullMatrixV4Phase6SourceFdAttestationConfig,
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission,
    binding: _binding.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding,
) -> None:
    if (
        admission.admission_sha256 != config.expected_admission_sha256
        or binding.binding_sha256
        != config.expected_reverse_bundle_descriptor_binding_sha256
        or binding.admission_sha256 != admission.admission_sha256
        or binding.run_id != str(admission.run_id)
        or binding.phase6_effect_start_identity_sha256
        != admission.phase6_effect_start_identity_sha256
        or binding.reverse_recovery_plan_sha256 != admission.reverse_recovery_plan_sha256
        or binding.route_binding_sha256 != admission.route_binding_sha256
        or binding.bundle_id != admission.bundle_id
        or binding.stage_receipt_sha256 != admission.stage_receipt_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_PROVENANCE_MISMATCH")


def _checked_staged_directory(fd_value: object) -> tuple[int, os.stat_result]:
    if type(fd_value) is not int or fd_value < 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
    try:
        if os.get_inheritable(fd_value):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
        flags = fcntl.fcntl(fd_value, fcntl.F_GETFL)
        if (flags & os.O_ACCMODE) != os.O_RDONLY:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
        path_only = getattr(os, "O_PATH", 0)
        if path_only and flags & path_only:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
        metadata = os.fstat(fd_value)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != _ROOT_UID
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
    except PhysicalFullMatrixV4Phase6SourceFdAttestationError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
    return fd_value, metadata


def _projection(
    *,
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission,
    binding: _binding.PhysicalFullMatrixV4Phase6ReverseBundleDescriptorBinding,
    staged_recovery_fd: int,
) -> dict[str, object]:
    body = {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_STATUS,
        "admission_sha256": admission.admission_sha256,
        "reverse_bundle_descriptor_binding_sha256": binding.binding_sha256,
        "run_id": str(admission.run_id),
        "phase6_effect_start_identity_sha256": admission.phase6_effect_start_identity_sha256,
        "reverse_recovery_plan_sha256": admission.reverse_recovery_plan_sha256,
        "route_binding_sha256": admission.route_binding_sha256,
        "bundle_id": admission.bundle_id,
        "stage_receipt_sha256": admission.stage_receipt_sha256,
        "recovery_bundle_binding_sha256": binding.recovery_bundle_binding_sha256,
        "descriptor_identity_sha256": binding.descriptor_identity_sha256,
        "staged_recovery_fd": staged_recovery_fd,
        "descriptor_device": binding.descriptor_device,
        "descriptor_inode": binding.descriptor_inode,
        "source_descriptor_use_authorized": False,
        "fd_attester_authorized": False,
        "materialization_authorized": False,
        "runner_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return {**body, "attestation_sha256": hashlib.sha256(canonical_json_bytes(body)).hexdigest()}


def attest_physical_full_matrix_v4_phase6_source_fd(
    *,
    config: PhysicalFullMatrixV4Phase6SourceFdAttestationConfig,
    inputs: PhysicalFullMatrixV4Phase6SourceFdAttestationInputs,
) -> PhysicalFullMatrixV4Phase6SourceFdAttestation:
    """Duplicate and attest one exact staged source FD; never recover anything."""

    _require_root()
    checked_config = _config(config)
    if type(inputs) is not PhysicalFullMatrixV4Phase6SourceFdAttestationInputs:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_INPUTS_INVALID")
    admission = _admitted(inputs.admission)
    binding = _bound(inputs.reverse_bundle_descriptor_binding)
    _correlate(config=checked_config, admission=admission, binding=binding)
    source_fd, source_metadata = _checked_staged_directory(inputs.staged_recovery_fd)
    if (
        source_fd != binding.staged_recovery_fd
        or source_metadata.st_dev != binding.descriptor_device
        or source_metadata.st_ino != binding.descriptor_inode
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_MISMATCH")
    duplicated_fd = -1
    try:
        duplicated_fd = os.dup(source_fd)
        os.set_inheritable(duplicated_fd, False)
        _, duplicate_metadata = _checked_staged_directory(duplicated_fd)
        if (
            duplicate_metadata.st_dev != source_metadata.st_dev
            or duplicate_metadata.st_ino != source_metadata.st_ino
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_DESCRIPTOR_UNSAFE")
        result = PhysicalFullMatrixV4Phase6SourceFdAttestation(
            **_projection(
                admission=admission,
                binding=binding,
                staged_recovery_fd=duplicated_fd,
            )
        )
        object.__setattr__(result, "_capability", _CAPABILITY)
        _STATES[result] = _State(
            admission=admission,
            binding=binding,
            device=duplicate_metadata.st_dev,
            inode=duplicate_metadata.st_ino,
        )
        return result
    except Exception:
        if duplicated_fd >= 0:
            try:
                os.close(duplicated_fd)
            except OSError:
                pass
        raise


def require_attested_physical_full_matrix_v4_phase6_source_fd(
    value: object,
) -> PhysicalFullMatrixV4Phase6SourceFdAttestation:
    """Require untampered local source-FD provenance; it remains non-authorizing."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase6SourceFdAttestation
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_UNAUTHORIZED")
    state = _STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_UNAUTHORIZED")
    _admitted(state.admission)
    _bound(state.binding)
    expected = _projection(
        admission=state.admission,
        binding=state.binding,
        staged_recovery_fd=value.staged_recovery_fd,
    )
    for name, expected_value in expected.items():
        if getattr(value, name) != expected_value:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_TAMPERED")
    _, metadata = _checked_staged_directory(value.staged_recovery_fd)
    if (metadata.st_dev, metadata.st_ino) != (state.device, state.inode):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_SOURCE_FD_ATTESTATION_TAMPERED")
    return value
