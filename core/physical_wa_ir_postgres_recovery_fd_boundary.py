"""Root-only, FD-only input binder for WA-IR phase-3 recovery.

This is deliberately *not* a PostgreSQL runner.  It cannot restore a base
backup, start a process, connect a socket, contact Object Storage, or mint an
acknowledgement.  It gives a future, separately reviewed local runner a small
defensive preparation step: revalidate the canonical materialization intent
and duplicate only the three already-open local descriptors that it may use.

The caller retains ownership of its descriptors.  The returned descriptors
are independent, non-inheritable handles and become the caller's
responsibility to close.  No filesystem path is accepted or returned.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import os
import stat
from typing import Any, Mapping

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core import physical_postgres_standby_bootstrap_materialization as _bootstrap
from core import physical_wa_ir_postgres_recovery_materialization_runtime as _runtime
from core.physical_wal_receiver_staging import (
    MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
    PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA,
    PHYSICAL_WAL_RECEIVER_STAGING_STATUS,
)


__all__ = (
    "PHYSICAL_WA_IR_POSTGRES_RECOVERY_FD_BOUNDARY_SCHEMA",
    "PhysicalWaIrPostgresRecoveryBoundMaterializationFds",
    "PhysicalWaIrPostgresRecoveryFdBoundaryError",
    "bind_wa_ir_postgres_socket_only_recovery_materialization_fds",
)


PHYSICAL_WA_IR_POSTGRES_RECOVERY_FD_BOUNDARY_SCHEMA = (
    "gold-trade-physical-wa-ir-postgres-recovery-fd-boundary-v1"
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_ROOT_UID = 0
_STAGE_RECEIPT_NAME = "stage-receipt.json"
_MAX_PLAN_BYTES = 64 * 1024
_MAX_INT64 = 2**63 - 1


class PhysicalWaIrPostgresRecoveryFdBoundaryError(ValueError):
    """One redacted refusal from the phase-3 FD-only binder."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalWaIrPostgresRecoveryBoundMaterializationFds:
    """Three verified duplicate descriptors for one future local runner.

    These descriptors are intentionally non-inheritable.  A later reviewed
    runner, if one is installed, must choose and attest its own narrowly
    scoped descriptor-passing boundary; this binder never launches anything.
    """

    schema: str
    invocation_sha256: str
    bootstrap_plan_sha256: str
    source_stage_fd: int
    target_pgdata_fd: int
    recovery_signal_seed_fd: int


def _fail(code: str) -> None:
    raise PhysicalWaIrPostgresRecoveryFdBoundaryError(code)


def _require_root() -> None:
    try:
        if os.geteuid() != _ROOT_UID:
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_ROOT_REQUIRED")
    except OSError:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_ROOT_REQUIRED")


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _positive(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_INT64:
        _fail(code)
    return value


def _canonical_mapping(
    raw: object,
    *,
    fields: frozenset[str],
    maximum_bytes: int,
    code: str,
) -> dict[str, Any]:
    """Use the bootstrap's strict canonical JSON parser, never a loose copy."""

    if type(raw) is not bytes or not 1 <= len(raw) <= maximum_bytes:
        _fail(code)
    try:
        return _bootstrap._parse_canonical_mapping(raw, fields=fields, code=code)
    except Exception:
        _fail(code)


def _validated_plan(
    value: object,
) -> tuple[_bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan, dict[str, Any]]:
    if type(value) is not _bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    plan = value
    raw = plan.canonical_plan
    payload = _canonical_mapping(
        raw,
        fields=_bootstrap._PLAN_FIELDS,
        maximum_bytes=_MAX_PLAN_BYTES,
        code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != _sha256(
        plan.plan_sha256,
        code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID",
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    if (
        payload.get("schema") != _bootstrap.PHYSICAL_POSTGRES_STANDBY_BOOTSTRAP_PLAN_SCHEMA
        or payload.get("kind") != "local_standby_bootstrap_materialization_intent"
        or payload.get("source_site") != "webapp_fi"
        or payload.get("receiver_site") != "webapp_ir"
        or payload.get("receiver_role") != "standby"
        or payload.get("bootstrap_id") != plan.bootstrap_id
        or payload.get("source_site") != plan.source_site
        or payload.get("receiver_site") != plan.receiver_site
        or payload.get("bundle_id") != plan.bundle_id
        or payload.get("stage_receipt_sha256") != plan.stage_receipt_sha256
        or payload.get("route_binding_sha256") != plan.route_binding_sha256
        or payload.get("terminal_wal_lsn") != plan.terminal_wal_lsn
        or payload.get("source_stage_device") != plan.source_stage_device
        or payload.get("source_stage_inode") != plan.source_stage_inode
        or payload.get("target_pgdata_device") != plan.target_pgdata_device
        or payload.get("target_pgdata_inode") != plan.target_pgdata_inode
        or payload.get("recovery_signal_seed_sha256") != plan.recovery_signal_seed_sha256
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    for item in (
        plan.bootstrap_id,
        plan.bundle_id,
        plan.stage_receipt_sha256,
        plan.route_binding_sha256,
        plan.witnessed_term_proof_sha256,
        plan.recovery_signal_seed_sha256,
    ):
        _sha256(item, code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    if LEASE_ID_RE.fullmatch(plan.writer_lease_id) is None:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    _positive(plan.writer_epoch, code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    for item in (
        plan.source_stage_device,
        plan.source_stage_inode,
        plan.target_pgdata_device,
        plan.target_pgdata_inode,
    ):
        _positive(item, code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    try:
        _bootstrap._lsn(plan.terminal_wal_lsn, code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    except Exception:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    writer_term = payload.get("writer_term")
    if not isinstance(writer_term, Mapping) or set(writer_term) != _bootstrap._TERM_FIELDS:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    if (
        writer_term.get("holder_site") != "webapp_fi"
        or writer_term.get("writer_epoch") != plan.writer_epoch
        or writer_term.get("writer_lease_id") != plan.writer_lease_id
        or writer_term.get("witnessed_term_proof_sha256") != plan.witnessed_term_proof_sha256
        or type(writer_term.get("witness_transition_id")) is not str
        or _bootstrap._TRANSITION_ID_RE.fullmatch(writer_term["witness_transition_id"]) is None
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    _sha256(
        writer_term.get("witnessed_term_proof_sha256"),
        code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID",
    )
    _sha256(
        payload.get("recovery_evidence_sha256"),
        code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID",
    )
    manifests = payload.get("manifest_sha256es")
    if not isinstance(manifests, list) or not manifests:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    normalized_manifests = tuple(
        _sha256(item, code="WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID") for item in manifests
    )
    if len(set(normalized_manifests)) != len(normalized_manifests):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    versions = payload.get("object_versions")
    if not isinstance(versions, list) or not versions:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    normalized_versions: list[tuple[str, str]] = []
    for item in versions:
        if not isinstance(item, Mapping) or set(item) != _bootstrap._STAGE_OBJECT_FIELDS:
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
        object_key = item.get("object_key")
        version_id = item.get("version_id")
        if (
            type(object_key) is not str
            or OBJECT_KEY_RE.fullmatch(object_key) is None
            or ".." in object_key.split("/")
            or type(version_id) is not str
            or VERSION_ID_RE.fullmatch(version_id) is None
        ):
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
        normalized_versions.append((object_key, version_id))
    if len(set(normalized_versions)) != len(normalized_versions):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")
    return plan, payload


def _validated_rendered_inputs(
    value: object,
) -> _runtime.PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs:
    if type(value) is not _runtime.PhysicalWaIrPostgresSocketOnlyRecoveryRenderedInputs:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    rendered = value
    raw = rendered.canonical_input
    payload = _canonical_mapping(
        raw,
        fields=_runtime._SOCKET_INPUT_FIELDS,
        maximum_bytes=_MAX_PLAN_BYTES,
        code="WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID",
    )
    if hashlib.sha256(raw).hexdigest() != _sha256(
        rendered.input_sha256,
        code="WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID",
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    if (
        payload.get("schema") != _runtime.PHYSICAL_WA_IR_POSTGRES_SOCKET_ONLY_RECOVERY_INPUT_SCHEMA
        or payload.get("status") != "default-off-socket-only-recovery-input"
        or payload.get("campaign_id") != rendered.campaign_id
        or payload.get("release_sha") != rendered.release_sha
        or payload.get("route_binding_sha256") != rendered.route_binding_sha256
        or payload.get("deployment_manifest_lock_sha256")
        != rendered.deployment_manifest_lock_sha256
        or payload.get("sealed_release_descriptor_sha256")
        != rendered.sealed_release_descriptor_sha256
        or payload.get("postgres_image") != rendered.postgres_image
        or payload.get("postgres_major") != 15
        or payload.get("network_mode") != "none"
        or payload.get("tcp_listener") != "disabled"
        or payload.get("unix_socket_directory") != "/var/run/postgresql"
        or payload.get("unix_socket_port") != 5432
        or payload.get("socket_authentication") != "peer-local-only"
        or payload.get("recovery_mode") != "standby-replay-only"
        or payload.get("direct_site_control") != "forbidden"
        or payload.get("destination_object_ingest") != "pull-only"
        or payload.get("promotion_authorized") is not False
        or payload.get("full_matrix_authorized") is not False
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    if (
        CAMPAIGN_ID_RE.fullmatch(rendered.campaign_id) is None
        or RELEASE_SHA_RE.fullmatch(rendered.release_sha) is None
        or _runtime._IMAGE_REFERENCE_RE.fullmatch(rendered.postgres_image) is None
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    for item in (
        rendered.route_binding_sha256,
        rendered.deployment_manifest_lock_sha256,
        rendered.sealed_release_descriptor_sha256,
    ):
        _sha256(item, code="WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    return rendered


def _validated_invocation(
    value: object,
    *,
    plan: _bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan,
    plan_payload: Mapping[str, Any],
) -> _runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation:
    if type(value) is not _runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    invocation = value
    rendered = _validated_rendered_inputs(invocation.rendered_inputs)
    writer_term = plan_payload["writer_term"]
    if (
        invocation.schema != _runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA
        or rendered.route_binding_sha256 != plan.route_binding_sha256
        or invocation.bootstrap_id != plan.bootstrap_id
        or invocation.bootstrap_plan_sha256 != plan.plan_sha256
        or invocation.source_site != "webapp_fi"
        or invocation.source_site != plan.source_site
        or invocation.receiver_site != "webapp_ir"
        or invocation.receiver_site != plan.receiver_site
        or invocation.writer_epoch != plan.writer_epoch
        or invocation.writer_lease_id != plan.writer_lease_id
        or invocation.witness_transition_id != writer_term["witness_transition_id"]
        or invocation.witnessed_term_proof_sha256 != plan.witnessed_term_proof_sha256
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    expected_payload = {
        "schema": _runtime.PHYSICAL_WA_IR_POSTGRES_RECOVERY_MATERIALIZATION_RUNTIME_SCHEMA,
        "kind": "socket-only-standby-materialization",
        "socket_only_recovery_input_sha256": rendered.input_sha256,
        "bootstrap_id": plan.bootstrap_id,
        "bootstrap_plan_sha256": plan.plan_sha256,
        "source_site": plan.source_site,
        "receiver_site": plan.receiver_site,
        "writer_epoch": plan.writer_epoch,
        "writer_lease_id": plan.writer_lease_id,
        "witness_transition_id": writer_term["witness_transition_id"],
        "witnessed_term_proof_sha256": plan.witnessed_term_proof_sha256,
    }
    try:
        expected_hash = _runtime._invocation_hash(expected_payload)
    except Exception:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    if invocation.invocation_sha256 != expected_hash:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_INVOCATION_INVALID")
    return invocation


def _fd_number(value: object, *, code: str) -> int:
    if type(value) is not int or value < 0:
        _fail(code)
    return value


def _fd_flags(fd: int, *, code: str) -> None:
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except OSError:
        _fail(code)
    if (flags & os.O_ACCMODE) != os.O_RDONLY:
        _fail(code)
    path_only = getattr(os, "O_PATH", 0)
    if path_only and (flags & path_only):
        _fail(code)


def _directory_metadata(
    fd: int,
    *,
    expected_device: int,
    expected_inode: int,
    require_empty: bool,
    code: str,
) -> os.stat_result:
    _fd_flags(fd, code=code)
    try:
        metadata = os.fstat(fd)
    except OSError:
        _fail(code)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != _ROOT_UID
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_dev != expected_device
        or metadata.st_ino != expected_inode
    ):
        _fail(code)
    if require_empty:
        try:
            if os.listdir(fd):
                _fail(code)
        except PhysicalWaIrPostgresRecoveryFdBoundaryError:
            raise
        except OSError:
            _fail(code)
    return metadata


def _open_independent_directory(
    source_fd: int,
    *,
    expected_device: int,
    expected_inode: int,
    require_empty: bool,
    code: str,
) -> int:
    _directory_metadata(
        source_fd,
        expected_device=expected_device,
        expected_inode=expected_inode,
        require_empty=False,
        code=code,
    )
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLATFORM_UNSUPPORTED")
    descriptor = -1
    try:
        descriptor = os.open(
            ".",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_fd,
        )
        os.set_inheritable(descriptor, False)
        _directory_metadata(
            descriptor,
            expected_device=expected_device,
            expected_inode=expected_inode,
            require_empty=require_empty,
            code=code,
        )
        return descriptor
    except PhysicalWaIrPostgresRecoveryFdBoundaryError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _open_independent_seed(
    source_fd: int,
    *,
    expected_sha256: str,
) -> int:
    _fd_flags(source_fd, code="WA_IR_RECOVERY_FD_BOUNDARY_SEED_UNSAFE")
    descriptor = -1
    try:
        descriptor = os.dup(source_fd)
        os.set_inheritable(descriptor, False)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != _ROOT_UID
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != 0
            or os.read(descriptor, 1) != b""
            or expected_sha256 != _EMPTY_SHA256
        ):
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_SEED_UNSAFE")
        return descriptor
    except PhysicalWaIrPostgresRecoveryFdBoundaryError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_SEED_UNSAFE")


def _read_frozen_stage_receipt(
    source_fd: int,
    *,
    plan_payload: Mapping[str, Any],
) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLATFORM_UNSUPPORTED")
    descriptor = -1
    try:
        before = os.stat(_STAGE_RECEIPT_NAME, dir_fd=source_fd, follow_symlinks=False)
        descriptor = os.open(
            _STAGE_RECEIPT_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_fd,
        )
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != _ROOT_UID
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or not 1 <= opened.st_size <= MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES
        ):
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_UNSAFE")
        raw = bytearray()
        while len(raw) < opened.st_size:
            chunk = os.read(descriptor, opened.st_size - len(raw))
            if not chunk:
                _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_UNSAFE")
            raw.extend(chunk)
        if os.read(descriptor, 1):
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_UNSAFE")
        after = os.fstat(descriptor)
        path_after = os.stat(_STAGE_RECEIPT_NAME, dir_fd=source_fd, follow_symlinks=False)
        if (
            (after.st_dev, after.st_ino, after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or (path_after.st_dev, path_after.st_ino, path_after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_UNSAFE")
    except PhysicalWaIrPostgresRecoveryFdBoundaryError:
        raise
    except OSError:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_UNSAFE")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    receipt = _canonical_mapping(
        bytes(raw),
        fields=_bootstrap._STAGE_FIELDS,
        maximum_bytes=MAX_PHYSICAL_WAL_RECEIVER_RECEIPT_BYTES,
        code="WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_INVALID",
    )
    if (
        receipt.get("schema") != PHYSICAL_WAL_RECEIVER_STAGE_RECEIPT_SCHEMA
        or receipt.get("status") != PHYSICAL_WAL_RECEIVER_STAGING_STATUS
        or receipt.get("bundle_id") != plan_payload["bundle_id"]
        or receipt.get("route_binding_sha256") != plan_payload["route_binding_sha256"]
        or receipt.get("manifest_sha256es") != plan_payload["manifest_sha256es"]
        or receipt.get("object_versions") != plan_payload["object_versions"]
        or not isinstance(receipt.get("candidate_path"), str)
        or not isinstance(receipt.get("artifacts"), list)
        or not receipt["artifacts"]
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_INVALID")
    receipt_sha256 = _sha256(
        receipt.get("receipt_sha256"),
        code="WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_INVALID",
    )
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    try:
        canonical_unsigned = canonical_json_bytes(unsigned)
    except (TypeError, ValueError):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_INVALID")
    if (
        hashlib.sha256(canonical_unsigned).hexdigest() != receipt_sha256
        or receipt_sha256 != plan_payload["stage_receipt_sha256"]
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_STAGE_RECEIPT_INVALID")


def bind_wa_ir_postgres_socket_only_recovery_materialization_fds(
    *,
    invocation: _runtime.PhysicalWaIrPostgresSocketOnlyRecoveryMaterializationInvocation,
    plan: _bootstrap.PhysicalPostgresStandbyBootstrapMaterializationPlan,
    source_stage_fd: int,
    target_pgdata_fd: int,
    recovery_signal_seed_fd: int,
) -> PhysicalWaIrPostgresRecoveryBoundMaterializationFds:
    """Revalidate and duplicate the only three local phase-3 descriptors.

    This is a defensive binder, not an admission boundary: it relies on the
    existing root-owned bootstrap and phase-3 runtime for Witness, sealed
    release, pull, and recovery-evidence admission.  It performs no I/O other
    than local descriptor/receipt inspection and creates no filesystem state.
    """

    _require_root()
    checked_plan, plan_payload = _validated_plan(plan)
    checked_invocation = _validated_invocation(
        invocation,
        plan=checked_plan,
        plan_payload=plan_payload,
    )
    source_fd = _fd_number(source_stage_fd, code="WA_IR_RECOVERY_FD_BOUNDARY_SOURCE_UNSAFE")
    target_fd = _fd_number(target_pgdata_fd, code="WA_IR_RECOVERY_FD_BOUNDARY_TARGET_UNSAFE")
    seed_fd = _fd_number(recovery_signal_seed_fd, code="WA_IR_RECOVERY_FD_BOUNDARY_SEED_UNSAFE")
    if len({source_fd, target_fd, seed_fd}) != 3:
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_DESCRIPTOR_ALIAS")
    if (
        checked_plan.source_stage_device == checked_plan.target_pgdata_device
        and checked_plan.source_stage_inode == checked_plan.target_pgdata_inode
    ):
        _fail("WA_IR_RECOVERY_FD_BOUNDARY_PLAN_INVALID")

    bound_source = -1
    bound_target = -1
    bound_seed = -1
    try:
        bound_source = _open_independent_directory(
            source_fd,
            expected_device=checked_plan.source_stage_device,
            expected_inode=checked_plan.source_stage_inode,
            require_empty=False,
            code="WA_IR_RECOVERY_FD_BOUNDARY_SOURCE_UNSAFE",
        )
        _read_frozen_stage_receipt(bound_source, plan_payload=plan_payload)
        bound_target = _open_independent_directory(
            target_fd,
            expected_device=checked_plan.target_pgdata_device,
            expected_inode=checked_plan.target_pgdata_inode,
            require_empty=True,
            code="WA_IR_RECOVERY_FD_BOUNDARY_TARGET_UNSAFE",
        )
        bound_seed = _open_independent_seed(
            seed_fd,
            expected_sha256=checked_plan.recovery_signal_seed_sha256,
        )
        return PhysicalWaIrPostgresRecoveryBoundMaterializationFds(
            schema=PHYSICAL_WA_IR_POSTGRES_RECOVERY_FD_BOUNDARY_SCHEMA,
            invocation_sha256=checked_invocation.invocation_sha256,
            bootstrap_plan_sha256=checked_plan.plan_sha256,
            source_stage_fd=bound_source,
            target_pgdata_fd=bound_target,
            recovery_signal_seed_fd=bound_seed,
        )
    except Exception:
        for descriptor in (bound_seed, bound_target, bound_source):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        raise
