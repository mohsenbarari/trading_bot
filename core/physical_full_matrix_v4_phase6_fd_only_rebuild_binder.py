"""Root-only, FD-only preparation seam for V4 Phase-6 FI standby rebuild.

This deliberately does not implement reverse recovery.  In particular it
does not read a path, open a network/socket connection, invoke a program,
contact Object Storage, materialize PostgreSQL, or change traffic/writer
state.  It only duplicates one already-open, empty root-owned FI PGDATA
directory descriptor after requiring the exact process-local Phase-6
admission provenance.

Phase-6's current canonical evidence intentionally contains no descriptor
identity for staged recovery material.  Passing a staging descriptor here
would therefore create an unbound input channel.  This seam accepts *only*
the destination descriptor; a future runner must introduce and attest a
separate, exact descriptor-to-plan binding before it can receive recovery
input material.
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


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDER_SCHEMA",
    "PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError",
    "PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget",
    "bind_physical_full_matrix_v4_phase6_fd_only_rebuild_target",
    "require_bound_physical_full_matrix_v4_phase6_fd_only_rebuild_target",
)


PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDER_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-phase6-fd-only-rebuild-binder-v1"
)

_ROOT_UID = 0
_CAPABILITY = object()


class PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError(ValueError):
    """A redacted refusal from the non-executing Phase-6 FD seam."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError(code)


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget:
    """One independent target-directory descriptor for a future reviewed runner.

    The result is diagnostic/provenance material only.  Its descriptor grants
    no recovery, runner, promotion, writer, traffic, or full-matrix authority.
    The caller owns and must close ``target_pgdata_fd``.
    """

    schema: str
    binding_sha256: str
    admission_sha256: str
    run_id: str
    plan_sha256: str
    phase6_effect_start_identity_sha256: str
    reverse_recovery_plan_sha256: str
    target_pgdata_fd: int
    fd_binder_authorized: bool = False
    runner_authorized: bool = False
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    traffic_switch_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission
    target_device: int
    target_inode: int


_STATES: WeakKeyDictionary[PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget, _State] = (
    WeakKeyDictionary()
)


def _require_root() -> None:
    try:
        if os.geteuid() != _ROOT_UID:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_ROOT_REQUIRED")
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_ROOT_REQUIRED")


def _checked_admission(
    value: object,
) -> _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission:
    try:
        checked = _admission.require_admitted_physical_full_matrix_v4_phase6_failback_rebuild(
            value
        )
    except _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmissionError as exc:
        raise PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_ADMISSION_REQUIRED"
        ) from exc
    if (
        checked.fd_binder_authorized is not False
        or checked.runner_authorized is not False
        or checked.materialization_authorized is not False
        or checked.promotion_authorized is not False
        or checked.writer_authorized is not False
        or checked.traffic_switch_authorized is not False
        or checked.execution_authorized is not False
        or checked.full_matrix_authorized is not False
        or checked.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_ADMISSION_INVALID")
    return checked


def _checked_target_directory(fd_value: object) -> tuple[int, os.stat_result]:
    if type(fd_value) is not int or fd_value < 0:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
    fd = fd_value
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if (flags & os.O_ACCMODE) != os.O_RDONLY:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
        path_only = getattr(os, "O_PATH", 0)
        if path_only and flags & path_only:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
        metadata = os.fstat(fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != _ROOT_UID
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or os.listdir(fd)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
    except PhysicalFullMatrixV4Phase6FdOnlyRebuildBinderError:
        raise
    except OSError:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
    return fd, metadata


def _projection(
    admission: _admission.PhysicalFullMatrixV4Phase6FailbackRebuildAdmission,
    *,
    target_fd: int,
) -> dict[str, object]:
    payload = {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDER_SCHEMA,
        "admission_sha256": admission.admission_sha256,
        "run_id": str(admission.run_id),
        "plan_sha256": admission.plan_sha256,
        "phase6_effect_start_identity_sha256": admission.phase6_effect_start_identity_sha256,
        "reverse_recovery_plan_sha256": admission.reverse_recovery_plan_sha256,
        "target_descriptor_present": True,
        "fd_binder_authorized": False,
        "runner_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }
    return {
        "schema": payload["schema"],
        "binding_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "admission_sha256": payload["admission_sha256"],
        "run_id": payload["run_id"],
        "plan_sha256": payload["plan_sha256"],
        "phase6_effect_start_identity_sha256": payload[
            "phase6_effect_start_identity_sha256"
        ],
        "reverse_recovery_plan_sha256": payload["reverse_recovery_plan_sha256"],
        "target_pgdata_fd": target_fd,
        "fd_binder_authorized": False,
        "runner_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "traffic_switch_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
        "full_matrix_executed": False,
    }


def bind_physical_full_matrix_v4_phase6_fd_only_rebuild_target(
    *,
    admission: object,
    target_pgdata_fd: object,
) -> PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget:
    """Duplicate one safe empty FI target FD; never run or materialize anything."""

    _require_root()
    checked_admission = _checked_admission(admission)
    source_fd, source_metadata = _checked_target_directory(target_pgdata_fd)
    duplicated_fd = -1
    try:
        duplicated_fd = os.dup(source_fd)
        os.set_inheritable(duplicated_fd, False)
        _, duplicate_metadata = _checked_target_directory(duplicated_fd)
        if (duplicate_metadata.st_dev, duplicate_metadata.st_ino) != (
            source_metadata.st_dev,
            source_metadata.st_ino,
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_TARGET_UNSAFE")
        result = PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget(
            **_projection(checked_admission, target_fd=duplicated_fd)
        )
        object.__setattr__(result, "_capability", _CAPABILITY)
        _STATES[result] = _State(
            admission=checked_admission,
            target_device=duplicate_metadata.st_dev,
            target_inode=duplicate_metadata.st_ino,
        )
        return result
    except Exception:
        if duplicated_fd >= 0:
            try:
                os.close(duplicated_fd)
            except OSError:
                pass
        raise


def require_bound_physical_full_matrix_v4_phase6_fd_only_rebuild_target(
    value: object,
) -> PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget:
    """Require untampered local provenance; this remains non-authorizing."""

    if (
        type(value) is not PhysicalFullMatrixV4Phase6FdOnlyRebuildBoundTarget
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_UNAUTHORIZED")
    state = _STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_UNAUTHORIZED")
    checked_admission = _checked_admission(state.admission)
    expected = _projection(checked_admission, target_fd=value.target_pgdata_fd)
    for name, expected_value in expected.items():
        if getattr(value, name) != expected_value:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_TAMPERED")
    _, metadata = _checked_target_directory(value.target_pgdata_fd)
    if (metadata.st_dev, metadata.st_ino) != (state.target_device, state.target_inode):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE6_FD_ONLY_REBUILD_BINDING_TAMPERED")
    return value
