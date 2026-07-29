#!/usr/bin/env python3
"""Produce one redacted, read-only production-shadow role observation.

This worker is intentionally a *local* role worker.  It never opens SSH,
Object Storage, or a peer connection and it never accepts caller supplied
convergence values.  In ``observe`` mode it verifies the exact Git
commit/tree/blob objects that bind the release worker and collector, runs the
fixed runtime collector from that release in an isolated interpreter for a repeatable-read local view,
reduces that view to hashes/counts, and publishes one root-only create-only
attestation.

The default command is ``plan``.  ``observe`` additionally requires the
explicit ``--execute-read-only`` switch.  The worker currently has an honest
local collector for database parity inputs and DR cursor inputs only.  It
deliberately reports blob round-trip, queue state, TLS, firewall, and Witness
live status as unavailable rather than turning configuration or caller values
into claims.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import keyword
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Awaitable, Callable, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import UUID

REQUEST_SCHEMA = "production-shadow-convergence-role-observer-request-v2"
ATTESTATION_SCHEMA = "production-shadow-convergence-role-observation-v2"
PLAN_SCHEMA = "production-shadow-convergence-role-observer-plan-v2"

PHASE = "convergence_gate"
OPERATION = "verify-shadow-three-site-convergence"
ROLES = ("bot_fi", "webapp_fi", "webapp_ir", "witness")
RUNTIME_SNAPSHOT_ROLES = frozenset({"bot_fi", "webapp_fi", "webapp_ir"})
WORKER_RELATIVE = Path("scripts/production_shadow_convergence_observer_worker.py")
LAUNCHER_RELATIVE = Path("scripts/production_shadow_convergence_observer_launcher")
RUNTIME_COLLECTOR_RELATIVE = Path("scripts/collect_three_site_staging_convergence_snapshot.py")
CONTAINER_COLLECTOR_RELATIVE = Path(
    "scripts/collect_production_shadow_compose_runtime_snapshot.py"
)
PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
SECRET_ROOT_PREFIX = Path("/root/secure-envs/trading-bot/three-site-production-shadow")
GIT = "/usr/bin/git"
IP = "/usr/sbin/ip"

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ROWS_PER_TABLE = 100_000
MAX_REQUEST_FUTURE_SKEW = timedelta(seconds=5)
MAX_OBSERVATION_FUTURE_SKEW = timedelta(seconds=5)
MAX_OBSERVATION_AGE = timedelta(minutes=15)
MAX_CAPTURE_TO_ATTESTATION_SKEW = timedelta(minutes=2)
MAX_HOST_PROOF_TO_ATTESTATION_SKEW = timedelta(minutes=2)
MAX_COLLECTOR_STDERR_BYTES = 64 * 1024
MAX_COLLECTOR_RUNTIME_CONFIG_BYTES = 64 * 1024
MAX_COLLECTOR_RUNTIME_ENV_VALUE_BYTES = 16 * 1024
MAX_COLLECTOR_STREAM_CHUNK_BYTES = 64 * 1024
COLLECTOR_SOURCE_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
COLLECTOR_SOURCE_MANIFEST_MAX_FILES = 5_000
COLLECTOR_SOURCE_MANIFEST_MAX_SOURCE_BYTES = 4 * 1024 * 1024
COLLECTOR_TIMEOUT_SECONDS = 120
COLLECTOR_REAP_TIMEOUT_SECONDS = 10
COLLECTOR_RESIDUE_POLL_SECONDS = 0.025
MAX_COLLECTOR_ADOPTED_CHILDREN = 64
OUTPUT_DIRECTORY_MODE = 0o700
OUTPUT_FILE_MODE = 0o600
COLLECTOR_RELEASE_ROOT_FD_ENV = "PRODUCTION_SHADOW_HELD_RELEASE_ROOT_FD"
COLLECTOR_FD_ENV = "PRODUCTION_SHADOW_HELD_CONVERGENCE_COLLECTOR_FD"
LAUNCHER_RELEASE_ROOT_FD_ENV = "PRODUCTION_SHADOW_HELD_RELEASE_ROOT_FD"
LAUNCHER_WORKER_FD_ENV = "PRODUCTION_SHADOW_HELD_CONVERGENCE_OBSERVER_WORKER_FD"
LAUNCHER_FD_ENV = "PRODUCTION_SHADOW_HELD_CONVERGENCE_OBSERVER_LAUNCHER_FD"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64

# Linux process-containment constants.  The observer does not accept a
# best-effort process-group kill as proof of cleanup: a collector can create a
# new session before its parent exits.  The worker therefore becomes a child
# subreaper only for the bounded collector interval and uses a pidfd for the
# direct child.  Any adopted child after the collector exits is evidence of
# residue, is killed/reaped, and causes the observation to fail.
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
}

# Local worktree config is untrusted input even when the release directory is
# root-owned.  The held-release Git helper accepts only fixed commit/tree/blob
# reads; it cannot run a worktree inspection, config, or transport command.
# This is a narrow mitigation, not a claim that a compromised root-owned Git
# binary or host is outside the root TCB.
GIT_STRICT_OPTIONS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.pager=cat",
    "-c",
    "protocol.file.allow=never",
)

# The runtime collector needs a small number of settings to import the release
# application code and open its local, read-only database transaction.  It must
# not inherit the controller/interactive shell environment: that would allow a
# caller's cwd, .env, PYTHONPATH, or arbitrary configuration variables to
# become evidence inputs.  The only accepted values live in a root-only,
# request-bound local JSON document described below.
COLLECTOR_RUNTIME_CONFIG_SCHEMA = (
    "production-shadow-convergence-runtime-config-v2"
)
COLLECTOR_RUNTIME_CONFIG_RELATIVE = Path(
    "convergence-observer-runtime/collector-runtime-config.json"
)
COLLECTOR_RUNTIME_TARGET_BINDING_NAME = "runtime-target-binding.json"
COLLECTOR_RUNTIME_TARGET_SET_NAME = "convergence-runtime-targets.json"
COMPOSE_EXECUTION_PLAN_NAME = "compose-observer-execution-plan.json"
COMPOSE_EXECUTION_MATERIAL_NAME = "compose-observer-execution-material.json"
COMPOSE_EXECUTION_PLAN_SCHEMA = "production-shadow-convergence-compose-observer-plan-v1"
COMPOSE_EXECUTION_MATERIAL_SCHEMA = "production-shadow-convergence-compose-observer-material-v1"
COMPOSE_EXECUTION_RECEIPT_SCHEMA = "production-shadow-convergence-compose-observer-receipt-v1"
COLLECTOR_SOURCE_MANIFEST_SCHEMA = "production-shadow-container-collector-source-manifest-v1"
COLLECTOR_SOURCE_MANIFEST_REQUIRED_PATHS = frozenset(
    {
        "scripts/collect_production_shadow_compose_runtime_snapshot.py",
        "scripts/collect_three_site_staging_convergence_snapshot.py",
        "core/__init__.py",
        "models/__init__.py",
    }
)
COLLECTOR_SOURCE_MANIFEST_PROJECT_PACKAGES = frozenset({"core", "models"})
COMPOSE_EXECUTION_TIMEOUT_SECONDS = 120
COMPOSE_EXECUTION_MAX_STDOUT_BYTES = 64 * 1024 * 1024
COMPOSE_EXECUTION_MAX_STDERR_BYTES = 64 * 1024
COMPOSE_DOCKER = "/usr/bin/docker"
COLLECTOR_RUNTIME_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "request_sha256",
        "runtime_target_binding_sha256",
        "environment",
        "config_sha256",
    }
)
COLLECTOR_RUNTIME_ENV_FIELDS = frozenset(
    {
        "TZ",
        "ENVIRONMENT",
        "TOPOLOGY_SCHEMA_VERSION",
        "THREE_SITE_DR_ENABLED",
        "DR_EVENT_PROTOCOL_ENABLED",
        "DR_EVENT_PROTOCOL_STRICT",
        "RELEASE_SHA",
        "SERVER_MODE",
        "LOGICAL_AUTHORITY",
        "PHYSICAL_SITE",
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "FRONTEND_URL",
        "JWT_SECRET_KEY",
        "REDIS_URL",
        "DR_PRODUCER_EPOCH",
        "DR_BLOB_ROOT",
    }
)
COMPOSE_EXECUTION_PLAN_FIELDS = frozenset(
    {
        "schema", "status", "campaign_id", "operation_id", "release_sha",
        "manifest_sha256", "canonical_compose_sha256", "role", "service",
        "profile", "project_name", "role_compose_path", "role_compose_sha256",
        "role_environment_path", "role_environment_sha256", "collector_path",
        "collector_sha256", "collector_delegate_sha256", "collector_closure_sha256",
        "collector_source_manifest_path", "collector_source_manifest_sha256",
        "collector_argv", "role_material_sha256",
        "role_material_inspection_sha256", "runtime_target_binding_sha256",
        "runtime_image_ids", "internal_network", "network_name", "release_mount",
        "runtime_input_mount", "container_id_file", "compose_argv", "collector_argv",
        "config_probe_argv", "resolved_observer_service_sha256", "cleanup_probe_argv",
        "timeout_seconds", "max_stdout_bytes", "max_stderr_bytes",
        "production_mutation_forbidden", "object_storage_contact_forbidden",
        "plan_sha256",
    }
)
COMPOSE_EXECUTION_MATERIAL_FIELDS = frozenset(
    {
        "schema", "campaign_id", "operation_id", "release_sha", "manifest_sha256",
        "role", "runtime_target_binding_sha256", "plan_sha256",
        "role_material_archive_inspection_sha256", "collector_source_manifest_sha256", "material_sha256",
    }
)

# This is a deliberately data-only copy of the small target-binding contract.
# The observer is itself verified as an exact release blob before it can read a
# runtime config.  Executing a second Python module obtained from that release
# would nevertheless grant that blob the root observer's ambient filesystem
# authority.  Keep the deterministic validation here instead of loading code
# from a Git blob or ``sys.path``.
_RUNTIME_TARGET_SET_SCHEMA = "production-shadow-convergence-runtime-target-set-v2"
_RUNTIME_TARGETS_FILENAME = "convergence-runtime-targets.json"
_RUNTIME_TARGET_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
_RUNTIME_TARGET_IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
_RUNTIME_TARGET_DOMAIN = "trading-bot/production-shadow/convergence-runtime-target/v1"
_RUNTIME_TARGET_SET_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "canonical_compose_sha256",
        "roles",
        "target_set_sha256",
    }
)
_RUNTIME_TARGET_ROW_FIELDS = frozenset(
    {
        "observer_service_sha256",
        "async_database_target_sha256",
        "sync_database_target_sha256",
        "runtime_identity_sha256",
        "runtime_target_descriptor_sha256",
    }
)
_RUNTIME_TARGET_DESCRIPTOR_FIELDS = frozenset(
    {"schema", "filename", "sha256", "bytes", "target_set_sha256", "roles"}
)
_OBSERVER_RUNTIME_TARGET_BINDING_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "canonical_compose_sha256",
        "role",
        "execution_contract",
        "convergence_runtime_targets",
        "runtime_target_row",
        "role_material_sha256",
        "role_runtime_image_ids",
        "database_target_identity_sha256",
        "runtime_config_projection_sha256",
        "binding_sha256",
    }
)
_OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA = (
    "production-shadow-convergence-observer-runtime-target-binding-v1"
)
_OBSERVER_RUNTIME_EXECUTION_CONTRACT = "compose-network-role-sync-observer-v1"
_RUNTIME_TARGET_MAX_BYTES = 64 * 1024
_RUNTIME_TARGET_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_TARGET_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_RUNTIME_TARGET_PASSWORD_RE = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")
_RUNTIME_TARGET_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

EXPECTED_CONSTRAINTS = {
    "plan_only_default": True,
    "read_only_runtime_snapshot_required": True,
    "caller_observation_values_forbidden": True,
    "raw_business_values_forbidden": True,
    "credentials_and_paths_forbidden": True,
    "worker_transport_io_forbidden": True,
    "direct_fi_to_ir_transfer_forbidden": True,
    "object_storage_operation_forbidden": True,
    "unsupported_observations_fail_closed": True,
    "create_only_root_only_artifact_required": True,
    "fixed_isolated_release_collector_required": True,
    "local_expected_host_ip_proof_required": True,
    "runtime_target_binding_required_for_runtime_roles": True,
}

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "release_root",
        "worker_path",
        "worker_sha256",
        "output_root",
        "max_rows_per_table",
        "constraints",
        "request_sha256",
    }
)
HOST_IDENTITY_PROOF_SCHEMA = "production-shadow-convergence-local-host-ip-proof-v1"
HOST_IDENTITY_PROOF_FIELDS = frozenset(
    {
        "schema",
        "expected_host",
        "observed_host",
        "address_family",
        "interface",
        "collector",
        "observed_at",
        "host_identity_proof_sha256",
    }
)
ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "runtime_target_binding_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase",
        "operation",
        "role",
        "expected_host",
        "phase_started_at",
        "request_sha256",
        "worker_sha256",
        "host_identity_proof",
        "observed_at",
        "release_identity",
        "runtime_snapshot",
        "compose_execution",
        "available_observations",
        "unavailable_observations",
        "redaction",
        "production_mutated",
        "worker_transport_contacted",
        "object_storage_contacted",
        "attestation_sha256",
    }
)

UNAVAILABLE_REASONS = {
    "blob_roundtrip": (
        "no exact-version object-storage readback collector is bound to the "
        "role runtime"
    ),
    "queue_state": (
        "no read-only runtime collector joins mutator processes, Redis due "
        "work, effects, leases, and provider-attempt deltas"
    ),
    "dr_tls": (
        "no release-bound bidirectional DR TLS handshake collector and peer "
        "endpoint contract is available"
    ),
    "destination_firewall": (
        "no canonical operation-labelled local/provider firewall allowlist "
        "readback collector is available"
    ),
    "witness_live": (
        "no minimal read-only Witness live-status exporter binds a signed "
        "proof to this convergence journal"
    ),
}


class ConvergenceRoleObserverError(RuntimeError):
    """The role observation cannot be proven to be bounded and truthful."""


class _SecureLocalFileError(RuntimeError):
    """A local root-only file operation could not establish its invariants."""


class _CollectorStreamLimitError(RuntimeError):
    """The isolated collector exceeded one of its fixed output bounds."""


class _CollectorCleanupError(RuntimeError):
    """The collector process group could not be bounded and reaped."""


@dataclass(frozen=True)
class _ObserverLauncherContract:
    """FD-bound capability supplied only by the fixed root-owned launcher.

    Root is the host TCB: a root operator can forge any local descriptor.  The
    contract instead prevents an accidental/direct worker invocation from
    becoming evidence, and binds the started worker and launcher inodes to the
    exact Git release before runtime observation begins.
    """

    release_root_descriptor: int
    worker_descriptor: int
    launcher_descriptor: int


@dataclass
class _CollectorContainmentBoundary:
    """Kernel-backed lifetime boundary for one isolated collector process."""

    previous_subreaper: bool
    direct_pid: int | None = None
    direct_pidfd: int | None = None


def _linux_prctl() -> Any:
    """Return the libc ``prctl`` entry point or fail before runtime contact."""

    if sys.platform != "linux" or os.name != "posix" or not Path("/proc").is_dir():
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector requires Linux child containment"
        )
    try:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        prctl.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector child containment is unavailable"
        ) from exc
    return prctl


def _child_subreaper_enabled() -> bool:
    """Read the current process's Linux child-subreaper state."""

    state = ctypes.c_int(0)
    result = _linux_prctl()(PR_GET_CHILD_SUBREAPER, ctypes.byref(state), 0, 0, 0)
    if result != 0:
        error = ctypes.get_errno()
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector child containment cannot be inspected"
        ) from OSError(error, os.strerror(error))
    return state.value == 1


def _set_child_subreaper(enabled: bool) -> None:
    """Set Linux child-subreaper state and verify the exact requested value."""

    result = _linux_prctl()(PR_SET_CHILD_SUBREAPER, int(enabled), 0, 0, 0)
    if result != 0:
        error = ctypes.get_errno()
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector child containment cannot be enabled"
        ) from OSError(error, os.strerror(error))
    if _child_subreaper_enabled() is not enabled:
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector child containment state differs"
        )


def _direct_child_pids(*, maximum_children: int | None) -> set[int]:
    """Return the process's current direct children without shelling out.

    A collector may call ``setsid`` and escape the launcher's process group.
    Once its direct parent exits, Linux reparents it to this temporary
    subreaper.  Reading this kernel-owned list is the authoritative cleanup
    input; a PID list supplied by the collector would not be trustworthy.
    """

    children: set[int] = set()
    task_root = Path("/proc/self/task")
    try:
        task_entries = tuple(task_root.iterdir())
    except OSError as exc:
        raise _CollectorCleanupError(
            "release-bound runtime collector child boundary is unavailable"
        ) from exc
    for task_entry in task_entries:
        if not task_entry.name.isdecimal():
            raise _CollectorCleanupError(
                "release-bound runtime collector child boundary is invalid"
            )
        try:
            payload = (task_entry / "children").read_bytes()
        except FileNotFoundError:
            # A concurrent thread exit reparents its children to the thread
            # group leader; a following cleanup pass will inspect that owner.
            continue
        except OSError as exc:
            raise _CollectorCleanupError(
                "release-bound runtime collector child boundary is unavailable"
            ) from exc
        if len(payload) > 16 * 1024:
            raise _CollectorCleanupError(
                "release-bound runtime collector child boundary exceeds its bound"
            )
        try:
            values = payload.decode("ascii").split()
        except UnicodeDecodeError as exc:
            raise _CollectorCleanupError(
                "release-bound runtime collector child boundary is invalid"
            ) from exc
        for value in values:
            if not value.isdecimal():
                raise _CollectorCleanupError(
                    "release-bound runtime collector child boundary is invalid"
                )
            pid = int(value)
            if pid <= 1 or pid == os.getpid():
                raise _CollectorCleanupError(
                    "release-bound runtime collector child boundary is invalid"
                )
            children.add(pid)
    if maximum_children is not None and len(children) > maximum_children:
        raise _CollectorCleanupError(
            "release-bound runtime collector child boundary exceeds its bound"
        )
    return children


def _open_collector_containment_boundary() -> _CollectorContainmentBoundary:
    """Establish a clean subreaper boundary before starting the collector."""

    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector requires pidfd containment"
        )
    # The worker must start with a small, proven-empty boundary.  This is a
    # start-time admission control, not a cleanup cap: after a hostile
    # collector runs, every PID listed by the bounded kernel children files
    # must still be processed even when it exceeds this normal-operation
    # threshold.
    if _direct_child_pids(maximum_children=MAX_COLLECTOR_ADOPTED_CHILDREN):
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector starts with pre-existing child residue"
        )
    previous_subreaper = _child_subreaper_enabled()
    changed = False
    try:
        if not previous_subreaper:
            _set_child_subreaper(True)
            changed = True
        if _direct_child_pids(maximum_children=MAX_COLLECTOR_ADOPTED_CHILDREN):
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector child boundary is not clean"
            )
    except BaseException:
        if changed:
            try:
                _set_child_subreaper(False)
            except ConvergenceRoleObserverError:
                pass
        raise
    return _CollectorContainmentBoundary(previous_subreaper=previous_subreaper)


def _register_collector_pidfd(
    boundary: _CollectorContainmentBoundary,
    process: Any,
) -> None:
    """Bind the direct native subprocess to a non-reusable kernel handle."""

    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 1:
        # ``asyncio.create_subprocess_exec`` always returns a positive PID in
        # production.  Unit-test process doubles intentionally have no kernel
        # child and therefore cannot carry a pidfd.
        return
    try:
        descriptor = os.pidfd_open(pid, 0)
    except ProcessLookupError:
        # A very short-lived collector can exit between spawn and pidfd_open.
        # It has no live direct PID to signal; the subreaper boundary still
        # drains any adopted residue before this one-shot path returns.
        return
    except (AttributeError, OSError) as exc:
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector pidfd is unavailable"
        ) from exc
    boundary.direct_pid = pid
    boundary.direct_pidfd = descriptor


def _close_collector_containment_boundary(
    boundary: _CollectorContainmentBoundary,
    *,
    restore_subreaper: bool,
) -> None:
    """Release the pidfd and restore subreaper state only after zero residue."""

    cleanup_error: Exception | None = None
    if boundary.direct_pidfd is not None:
        try:
            os.close(boundary.direct_pidfd)
        except OSError as exc:
            cleanup_error = exc
        boundary.direct_pidfd = None
    if restore_subreaper and not boundary.previous_subreaper:
        try:
            _set_child_subreaper(False)
        except ConvergenceRoleObserverError as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        raise _CollectorCleanupError(
            "release-bound runtime collector child containment could not be restored"
        ) from cleanup_error


def _reap_adopted_children_nonblocking() -> None:
    """Reap only children now owned by this temporary subreaper boundary."""

    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        except OSError as exc:
            raise _CollectorCleanupError(
                "release-bound runtime collector child residue cannot be reaped"
            ) from exc
        if pid == 0:
            return


async def _drain_collector_child_residue(
    boundary: _CollectorContainmentBoundary,
) -> bool:
    """Kill/reap every adopted descendant and report whether any existed."""

    deadline = time.monotonic() + COLLECTOR_REAP_TIMEOUT_SECONDS
    saw_residue = False
    while True:
        # Do not reuse the strict pre-start count limit here.  Each
        # kernel-owned /proc children file is still capped at 16 KiB by
        # _direct_child_pids, but cleanup has to kill/reap every listed PID so
        # that an oversized detached set cannot turn into an escape hatch.
        children = _direct_child_pids(maximum_children=None)
        if children:
            saw_residue = True
            for pid in children:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    raise _CollectorCleanupError(
                        "release-bound runtime collector child residue cannot be killed"
                    ) from exc
        _reap_adopted_children_nonblocking()
        if not _direct_child_pids(maximum_children=None):
            return saw_residue
        if time.monotonic() >= deadline:
            raise _CollectorCleanupError(
                "release-bound runtime collector cleanup could not prove zero live residue"
            )
        await asyncio.sleep(COLLECTOR_RESIDUE_POLL_SECONDS)


def _fail_stop_after_unproven_collector_cleanup() -> None:
    """Terminate this one-shot launcher process with subreaper state intact.

    Once the temporary subreaper cannot prove zero adopted children, clearing
    it would reparent an unbounded descendant beyond this worker's control.
    The fixed launcher execs one Python process for one observation, so the
    only safe response is terminal: kernel teardown releases the subreaper and
    no reusable worker process can continue with that authority retained.
    """

    os._exit(70)
    raise AssertionError("os._exit unexpectedly returned")


def _collector_containment_remains_enabled(
    boundary: _CollectorContainmentBoundary,
) -> bool:
    """Return whether this invocation failed to relinquish its subreaper bit."""

    if boundary.previous_subreaper:
        return False
    try:
        return _child_subreaper_enabled()
    except ConvergenceRoleObserverError:
        # An unknown post-cleanup state is not safe for a reusable worker.
        return True


def _require_isolated_observer_execution() -> None:
    """Reject production execution that started with ambient import state.

    This worker deliberately keeps its parent process free of project imports.
    Rewriting ``sys.path`` after normal Python startup cannot undo a caller's
    ``PYTHONPATH`` or module-cache influence, so the public execution paths
    require an interpreter started with ``-I -S`` before any project module is
    imported.  The fixed root-only launcher applies that boundary before
    Python begins; a post-start flag check alone is not accepted as authority.
    """

    flags = sys.flags
    if (
        getattr(flags, "isolated", 0) != 1
        or getattr(flags, "ignore_environment", 0) != 1
        or getattr(flags, "no_user_site", 0) != 1
        or getattr(flags, "no_site", 0) != 1
        or not bool(getattr(flags, "safe_path", False))
    ):
        raise ConvergenceRoleObserverError(
            "observer must be launched by an isolated Python interpreter (-I -S)"
        )
    if any(not isinstance(entry, str) or not Path(entry).is_absolute() for entry in sys.path):
        raise ConvergenceRoleObserverError("observer isolated interpreter path is unsafe")
    preloaded = sorted(
        name
        for name in sys.modules
        if name == "core"
        or name.startswith("core.")
        or name == "models"
        or name.startswith("models.")
    )
    if preloaded:
        raise ConvergenceRoleObserverError(
            "observer cannot trust preloaded project modules"
        )
    if any(name in sys.modules for name in ("site", "sitecustomize", "usercustomize")):
        raise ConvergenceRoleObserverError(
            "observer must start without site package processing"
        )


def _assert_root_owned_regular_file(
    metadata: os.stat_result,
    *,
    label: str,
    max_size: int,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or metadata.st_size < 0
        or metadata.st_size > max_size
    ):
        raise _SecureLocalFileError(f"{label} is not a stable root-only file")


def _read_root_only_bytes(path: Path, *, label: str, max_size: int) -> bytes:
    """Read one stable root-only file without importing ambient project code."""

    if max_size < 1:
        raise _SecureLocalFileError(f"{label} size limit is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise _SecureLocalFileError(f"cannot securely open {label}: {path}") from exc
    try:
        before = os.fstat(descriptor)
        _assert_root_owned_regular_file(before, label=label, max_size=max_size)
        payload = bytearray()
        while len(payload) <= max_size:
            block = os.read(descriptor, min(64 * 1024, max_size + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        if len(payload) > max_size:
            raise _SecureLocalFileError(f"{label} exceeds its approved size")
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise _SecureLocalFileError(f"{label} changed while being read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _write_root_only_new_bytes(
    path: Path,
    payload: bytes,
    *,
    label: str,
    mode: int,
    max_size: int,
) -> None:
    """Create one fsync'd root-only file without replacing an existing path."""

    if not isinstance(payload, bytes) or not payload or len(payload) > max_size:
        raise _SecureLocalFileError(f"{label} payload is invalid or oversized")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory_descriptor = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise _SecureLocalFileError(f"cannot securely open {label} directory") from exc
    temporary_descriptor = -1
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    published = False
    try:
        directory_metadata = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != 0
            or stat.S_IMODE(directory_metadata.st_mode) & 0o077
        ):
            raise _SecureLocalFileError(f"{label} directory is not root-only")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            mode,
            dir_fd=directory_descriptor,
        )
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(temporary_descriptor, view[offset:])
            if written <= 0:
                raise _SecureLocalFileError(f"{label} write made no progress")
            offset += written
        os.fchmod(temporary_descriptor, mode)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1
        try:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise _SecureLocalFileError(f"{label} already exists") from exc
        published = True
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise _SecureLocalFileError(f"{label} could not be published safely") from exc
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        os.close(directory_descriptor)
    if not published:
        raise _SecureLocalFileError(f"{label} was not published")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceRoleObserverError("JSON document has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConvergenceRoleObserverError("value is not canonical JSON") from exc


def _sha256(value: bytes | Mapping[str, Any] | list[Any]) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def _runtime_target_error(message: str) -> ConvergenceRoleObserverError:
    return ConvergenceRoleObserverError(f"runtime target contract: {message}")


def _runtime_target_text(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise _runtime_target_error(f"{label} is invalid")
    return value


def _runtime_target_nonzero_digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _RUNTIME_TARGET_SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise _runtime_target_error(f"{label} is not a nonzero SHA-256")
    return value


def _runtime_target_operation_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise _runtime_target_error(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise _runtime_target_error(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise _runtime_target_error(f"{label} is invalid")
    return value


def _runtime_target_hash(label: str, value: Mapping[str, Any]) -> str:
    if not isinstance(label, str) or not label or "\x00" in label:
        raise _runtime_target_error("digest domain is invalid")
    return hashlib.sha256(
        _RUNTIME_TARGET_DOMAIN.encode("ascii")
        + b"\x00"
        + label.encode("ascii")
        + b"\x00"
        + _canonical_json(dict(value))
    ).hexdigest()


def _runtime_target_service_shape(role: str) -> dict[str, Any]:
    if role not in _RUNTIME_TARGET_ROLES:
        raise _runtime_target_error("observer service role is invalid")
    return {
        "role": role,
        "service": f"{role}_sync_observer",
        "profiles": [f"{role.replace('_', '-')}-observe"],
        "restart": "no",
        "command": ["python", "-c", "raise SystemExit('invoke with docker compose run')"],
        "depends_on": {f"{role}_db": "service_healthy"},
        "networks": [role],
    }


def _runtime_target_row(value: Any, *, role: str, label: str) -> dict[str, str]:
    if role not in _RUNTIME_TARGET_ROLES:
        raise _runtime_target_error(f"{label} role is invalid")
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_TARGET_ROW_FIELDS:
        raise _runtime_target_error(f"{label} fields differ")
    row = {
        field: _runtime_target_nonzero_digest(value.get(field), label=f"{label}.{field}")
        for field in _RUNTIME_TARGET_ROW_FIELDS
    }
    expected = _runtime_target_hash(
        "runtime-target-descriptor",
        {
            "role": role,
            **{
                field: row[field]
                for field in row
                if field != "runtime_target_descriptor_sha256"
            },
        },
    )
    if row["runtime_target_descriptor_sha256"] != expected:
        raise _runtime_target_error(f"{label} descriptor differs")
    return row


def _runtime_target_binding_digests(
    row: Mapping[str, Any], *, role: str, release_sha: str
) -> dict[str, str]:
    checked = _runtime_target_row(row, role=role, label="runtime target binding row")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise _runtime_target_error("runtime target binding release is invalid")
    database_target_identity_sha256 = _runtime_target_hash(
        "database-target-identity",
        {
            "role": role,
            "async_database_target_sha256": checked["async_database_target_sha256"],
            "sync_database_target_sha256": checked["sync_database_target_sha256"],
        },
    )
    return {
        "database_target_identity_sha256": database_target_identity_sha256,
        "runtime_config_projection_sha256": _runtime_target_hash(
            "runtime-config-projection",
            {
                "role": role,
                "release_sha": release_sha,
                "runtime_identity_sha256": checked["runtime_identity_sha256"],
                "database_target_identity_sha256": database_target_identity_sha256,
            },
        ),
    }


def _runtime_target_descriptor(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _runtime_target_error(f"{label} is not an object")
    descriptor = dict(value)
    if set(descriptor) != _RUNTIME_TARGET_DESCRIPTOR_FIELDS:
        raise _runtime_target_error(f"{label} fields differ")
    if (
        descriptor.get("schema") != _RUNTIME_TARGET_SET_SCHEMA
        or descriptor.get("filename") != _RUNTIME_TARGETS_FILENAME
        or descriptor.get("roles") != list(_RUNTIME_TARGET_ROLES)
        or isinstance(descriptor.get("bytes"), bool)
        or not isinstance(descriptor.get("bytes"), int)
        or not 1 <= descriptor["bytes"] <= _RUNTIME_TARGET_MAX_BYTES
    ):
        raise _runtime_target_error(f"{label} identity differs")
    for field in ("sha256", "target_set_sha256"):
        _runtime_target_nonzero_digest(descriptor.get(field), label=f"{label}.{field}")
    return descriptor


def _runtime_target_set(value: Any, *, operation_id: str, release_sha: str,
                        canonical_compose_sha256: str, label: str) -> dict[str, Any]:
    operation = _runtime_target_operation_id(operation_id, label=f"{label} operation")
    if not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise _runtime_target_error(f"{label} release is invalid")
    compose_digest = _runtime_target_nonzero_digest(
        canonical_compose_sha256, label=f"{label} canonical Compose"
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != _RUNTIME_TARGET_SET_FIELDS
        or value.get("schema") != _RUNTIME_TARGET_SET_SCHEMA
        or value.get("operation_id") != operation
        or value.get("release_sha") != release_sha
        or value.get("canonical_compose_sha256") != compose_digest
    ):
        raise _runtime_target_error(f"{label} identity differs")
    roles = value.get("roles")
    if not isinstance(roles, Mapping) or set(roles) != set(_RUNTIME_TARGET_ROLES):
        raise _runtime_target_error(f"{label} role coverage differs")
    normalized = {
        "schema": _RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": operation,
        "release_sha": release_sha,
        "canonical_compose_sha256": compose_digest,
        "roles": {
            role: _runtime_target_row(roles[role], role=role, label=f"{label}.{role}")
            for role in _RUNTIME_TARGET_ROLES
        },
        "target_set_sha256": _runtime_target_nonzero_digest(
            value.get("target_set_sha256"), label=f"{label} target set"
        ),
    }
    expected = _runtime_target_hash(
        "runtime-target-set",
        {key: item for key, item in normalized.items() if key != "target_set_sha256"},
    )
    if normalized["target_set_sha256"] != expected:
        raise _runtime_target_error(f"{label} digest differs")
    return normalized


def _validate_runtime_target_payload_descriptor(
    payload: bytes, descriptor: Mapping[str, Any], *, operation_id: str,
    release_sha: str, canonical_compose_sha256: str, label: str,
) -> dict[str, Any]:
    checked_descriptor = _runtime_target_descriptor(descriptor, label=label)
    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= _RUNTIME_TARGET_MAX_BYTES
        or len(payload) != checked_descriptor["bytes"]
        or hashlib.sha256(payload).hexdigest() != checked_descriptor["sha256"]
    ):
        raise _runtime_target_error(f"{label} payload differs")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _runtime_target_error(f"{label} payload is not strict JSON") from exc
    if not isinstance(document, dict) or _canonical_json(document) != payload:
        raise _runtime_target_error(f"{label} payload is not canonical JSON")
    normalized = _runtime_target_set(
        document,
        operation_id=operation_id,
        release_sha=release_sha,
        canonical_compose_sha256=canonical_compose_sha256,
        label=label,
    )
    expected_descriptor = {
        "schema": _RUNTIME_TARGET_SET_SCHEMA,
        "filename": _RUNTIME_TARGETS_FILENAME,
        "sha256": hashlib.sha256(_canonical_json(normalized)).hexdigest(),
        "bytes": len(_canonical_json(normalized)),
        "target_set_sha256": normalized["target_set_sha256"],
        "roles": list(_RUNTIME_TARGET_ROLES),
    }
    if expected_descriptor != checked_descriptor:
        raise _runtime_target_error(f"{label} descriptor differs")
    return normalized


def _runtime_target_identity(environment: Mapping[str, Any], *, role: str,
                             release_sha: str) -> dict[str, str]:
    fields = (
        "TZ", "ENVIRONMENT", "TOPOLOGY_SCHEMA_VERSION", "THREE_SITE_DR_ENABLED",
        "DR_EVENT_PROTOCOL_ENABLED", "DR_EVENT_PROTOCOL_STRICT", "RELEASE_SHA",
        "SERVER_MODE", "LOGICAL_AUTHORITY", "PHYSICAL_SITE",
    )
    values = {name: _runtime_target_text(environment.get(name), label=f"runtime identity {name}") for name in fields}
    expected = {
        "TZ": "UTC", "ENVIRONMENT": "production", "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true", "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true", "RELEASE_SHA": release_sha,
        "SERVER_MODE": "foreign" if role == "bot_fi" else "iran",
        "LOGICAL_AUTHORITY": "foreign" if role == "bot_fi" else "webapp",
        "PHYSICAL_SITE": role,
    }
    if values != expected:
        raise _runtime_target_error(f"runtime identity for {role} differs")
    return values


def _runtime_target_database_url(value: str, *, role: str, expected_scheme: str,
                                 label: str) -> tuple[dict[str, Any], str]:
    text = _runtime_target_text(value, label=label)
    if "?" in text or "#" in text:
        raise _runtime_target_error(f"{label} is not a canonical database URL")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise _runtime_target_error(f"{label} is not a canonical database URL") from exc
    host = f"{role}_db"
    if (
        parsed.scheme != expected_scheme or parsed.query or parsed.fragment
        or parsed.netloc.count("@") != 1 or parsed.hostname != host or port is not None
        or parsed.username is None or parsed.password is None or not parsed.path.startswith("/")
        or parsed.path.count("/") != 1
    ):
        raise _runtime_target_error(f"{label} is not a canonical database URL")
    username, password, database = parsed.username, parsed.password, parsed.path[1:]
    if (
        _RUNTIME_TARGET_IDENTIFIER_RE.fullmatch(username) is None
        or _RUNTIME_TARGET_IDENTIFIER_RE.fullmatch(database) is None
        or _RUNTIME_TARGET_PASSWORD_RE.fullmatch(password) is None
        or text != f"{expected_scheme}://{username}:{password}@{host}/{database}"
    ):
        raise _runtime_target_error(f"{label} is ambiguous")
    scheme, dialect = expected_scheme.split("+", 1) if "+" in expected_scheme else (expected_scheme, "default")
    return ({"scheme": scheme, "dialect": dialect, "host_service": host, "port": 5432,
             "database": database, "username": username}, password)


def _derive_runtime_target_binding(environment: Mapping[str, Any], *, role: str,
                                   release_sha: str) -> dict[str, Any]:
    if not isinstance(environment, Mapping) or role not in _RUNTIME_TARGET_ROLES:
        raise _runtime_target_error("runtime environment or role is invalid")
    fields = ("DATABASE_URL", "SYNC_DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    values = {name: _runtime_target_text(environment.get(name), label=f"runtime environment {name}") for name in fields}
    async_target, async_password = _runtime_target_database_url(
        values["DATABASE_URL"], role=role, expected_scheme="postgresql+asyncpg", label=f"{role} observer DATABASE_URL"
    )
    sync_target, sync_password = _runtime_target_database_url(
        values["SYNC_DATABASE_URL"], role=role, expected_scheme="postgresql", label=f"{role} observer SYNC_DATABASE_URL"
    )
    if (
        {key: item for key, item in async_target.items() if key != "dialect"}
        != {key: item for key, item in sync_target.items() if key != "dialect"}
        or async_password != sync_password or async_password != values["POSTGRES_PASSWORD"]
        or async_target["username"] != f"{role}_observer"
        or values["POSTGRES_USER"] != f"{role}_observer"
        or async_target["database"] != values["POSTGRES_DB"]
    ):
        raise _runtime_target_error(f"runtime database targets for {role} differ")
    row = {
        "observer_service_sha256": _runtime_target_hash("observer-service-definition", _runtime_target_service_shape(role)),
        "async_database_target_sha256": _runtime_target_hash("database-target-async", async_target),
        "sync_database_target_sha256": _runtime_target_hash("database-target-sync", sync_target),
        "runtime_identity_sha256": _runtime_target_hash("runtime-identity", _runtime_target_identity(environment, role=role, release_sha=release_sha)),
    }
    row["runtime_target_descriptor_sha256"] = _runtime_target_hash(
        "runtime-target-descriptor", {"role": role, **row}
    )
    return {"runtime_target_row": row, **_runtime_target_binding_digests(row, role=role, release_sha=release_sha)}


def _runtime_target_image_ids(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_RUNTIME_TARGET_IMAGE_KINDS):
        raise _runtime_target_error(f"{label} fields differ")
    result = {kind: str(value[kind]) for kind in _RUNTIME_TARGET_IMAGE_KINDS}
    if any(_RUNTIME_TARGET_IMAGE_ID_RE.fullmatch(item) is None for item in result.values()) or len(set(result.values())) != len(result):
        raise _runtime_target_error(f"{label} is invalid")
    return result


def _validate_observer_runtime_target_binding(
    value: Any, *, campaign_id: str, operation_id: str, release_sha: str,
    manifest_sha256: str, role: str, label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _OBSERVER_RUNTIME_TARGET_BINDING_FIELDS:
        raise _runtime_target_error(f"{label} fields differ")
    document = dict(value)
    campaign = _runtime_target_operation_id(campaign_id, label=f"{label} campaign")
    operation = _runtime_target_operation_id(operation_id, label=f"{label} operation")
    if campaign == operation or role not in _RUNTIME_TARGET_ROLES or not isinstance(release_sha, str) or SHA40_RE.fullmatch(release_sha) is None:
        raise _runtime_target_error(f"{label} identity differs")
    if (
        document.get("schema") != _OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA
        or document.get("campaign_id") != campaign or document.get("operation_id") != operation
        or document.get("release_sha") != release_sha
        or document.get("manifest_sha256") != _runtime_target_nonzero_digest(manifest_sha256, label=f"{label} manifest")
        or document.get("role") != role or document.get("execution_contract") != _OBSERVER_RUNTIME_EXECUTION_CONTRACT
    ):
        raise _runtime_target_error(f"{label} identity differs")
    descriptor = _runtime_target_descriptor(document["convergence_runtime_targets"], label=f"{label} target descriptor")
    row = _runtime_target_row(document["runtime_target_row"], role=role, label=f"{label} target row")
    digests = _runtime_target_binding_digests(row, role=role, release_sha=release_sha)
    expected = {
        "schema": _OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA,
        "campaign_id": campaign, "operation_id": operation, "release_sha": release_sha,
        "manifest_sha256": _runtime_target_nonzero_digest(manifest_sha256, label=f"{label} manifest"),
        "canonical_compose_sha256": _runtime_target_nonzero_digest(document.get("canonical_compose_sha256"), label=f"{label} canonical Compose"),
        "role": role, "execution_contract": _OBSERVER_RUNTIME_EXECUTION_CONTRACT,
        "convergence_runtime_targets": descriptor, "runtime_target_row": row,
        "role_material_sha256": _runtime_target_nonzero_digest(document.get("role_material_sha256"), label=f"{label} role material"),
        "role_runtime_image_ids": _runtime_target_image_ids(document.get("role_runtime_image_ids"), label=f"{label} runtime image IDs"),
        **digests, "binding_sha256": ZERO_SHA256,
    }
    expected["binding_sha256"] = _runtime_target_hash(
        "observer-runtime-target-binding", {key: item for key, item in expected.items() if key != "binding_sha256"}
    )
    if document != expected:
        raise _runtime_target_error(f"{label} digest differs")
    return expected


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise ConvergenceRoleObserverError(f"{label} must be a nonzero SHA-256")
    return value


def _release_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise ConvergenceRoleObserverError(f"{label} must be a 40-character lowercase SHA")
    return value


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ConvergenceRoleObserverError(f"{label} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as exc:
        raise ConvergenceRoleObserverError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != value or parsed.int == 0:
        raise ConvergenceRoleObserverError(f"{label} must be a nonzero canonical UUID")
    return value


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ConvergenceRoleObserverError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConvergenceRoleObserverError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConvergenceRoleObserverError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ipv4_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ConvergenceRoleObserverError(f"{label} must be an IPv4 address")
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ConvergenceRoleObserverError(f"{label} must be an IPv4 address") from exc
    if str(parsed) != value or parsed.is_unspecified or parsed.is_multicast:
        raise ConvergenceRoleObserverError(f"{label} must be a canonical IPv4 address")
    return value


def _utcnow() -> datetime:
    """Production clock seam; tests patch this private helper only."""

    return datetime.now(timezone.utc)


def canonical_paths(*, operation_id: str, release_sha: str, role: str) -> dict[str, Path]:
    _canonical_uuid(operation_id, label="operation_id")
    _release_sha(release_sha, label="release_sha")
    if role not in ROLES:
        raise ConvergenceRoleObserverError("observer role is invalid")
    release_root = PROJECT_ROOT_PREFIX / operation_id / "releases" / release_sha
    output_root = (
        SECRET_ROOT_PREFIX / operation_id / "convergence-observations" / role
    )
    return {
        "release_root": release_root,
        "worker_path": release_root / WORKER_RELATIVE,
        "output_root": output_root,
    }


def _absolute_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise ConvergenceRoleObserverError(f"{label} path is invalid")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ConvergenceRoleObserverError(f"{label} path is unsafe")
    return path


def _request_digest(document: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "request_sha256"}
    return _sha256(unsigned)


def validate_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a role-local request without contacting a runtime."""

    if (
        not isinstance(value, Mapping)
        or set(value) != REQUEST_FIELDS
        or value.get("schema") != REQUEST_SCHEMA
        or value.get("status") != "authorized-read-only-observation"
        or value.get("phase") != PHASE
        or value.get("operation") != OPERATION
    ):
        raise ConvergenceRoleObserverError("observer request fields differ")
    try:
        document = json.loads(
            _canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("observer request is not canonical JSON") from exc
    if document != dict(value):
        raise ConvergenceRoleObserverError("observer request canonical form differs")
    campaign_id = _canonical_uuid(document["campaign_id"], label="campaign_id")
    operation_id = _canonical_uuid(document["operation_id"], label="operation_id")
    if campaign_id == operation_id:
        raise ConvergenceRoleObserverError("campaign and operation IDs must differ")
    release_sha = _release_sha(document["release_sha"], label="release_sha")
    release_tree_sha = _release_sha(document["release_tree_sha"], label="release_tree_sha")
    del release_tree_sha
    role = document.get("role")
    if role not in ROLES:
        raise ConvergenceRoleObserverError("observer role is invalid")
    _ipv4_text(document.get("expected_host"), label="observer expected host")
    for field in ("manifest_sha256", "plan_sha256", "approval_sha256", "worker_sha256"):
        _nonzero_sha256(document[field], label=field)
    runtime_target_binding = document["runtime_target_binding_sha256"]
    if role in RUNTIME_SNAPSHOT_ROLES:
        _nonzero_sha256(
            runtime_target_binding,
            label="runtime_target_binding_sha256",
        )
    elif runtime_target_binding is not None:
        raise ConvergenceRoleObserverError(
            "Witness observer must carry a null runtime target binding"
        )
    if (
        type(document.get("max_rows_per_table")) is not int
        or not 1 <= document["max_rows_per_table"] <= MAX_ROWS_PER_TABLE
        or document.get("constraints") != EXPECTED_CONSTRAINTS
    ):
        raise ConvergenceRoleObserverError("observer limits or constraints differ")
    phase_started_at = _timestamp(document["phase_started_at"], label="phase_started_at")
    current = (now or _utcnow()).astimezone(timezone.utc)
    if phase_started_at > current + MAX_REQUEST_FUTURE_SKEW:
        raise ConvergenceRoleObserverError("observer request predates its durable phase start")
    expected = canonical_paths(
        operation_id=operation_id,
        release_sha=release_sha,
        role=role,
    )
    for field in ("release_root", "worker_path", "output_root"):
        if _absolute_path(document[field], label=field) != expected[field]:
            raise ConvergenceRoleObserverError(f"observer {field} is not canonical")
    if document["request_sha256"] != _request_digest(document):
        raise ConvergenceRoleObserverError("observer request digest differs")
    return document


def build_request(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    manifest_sha256: str,
    runtime_target_binding_sha256: str | None,
    plan_sha256: str,
    approval_sha256: str,
    role: str,
    expected_host: str,
    phase_started_at: datetime,
    worker_sha256: str,
    max_rows_per_table: int = 10_000,
) -> dict[str, Any]:
    """Build one exact role contract; this does not observe a runtime."""

    paths = canonical_paths(
        operation_id=operation_id,
        release_sha=release_sha,
        role=role,
    )
    document: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "status": "authorized-read-only-observation",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "manifest_sha256": manifest_sha256,
        "runtime_target_binding_sha256": runtime_target_binding_sha256,
        "plan_sha256": plan_sha256,
        "approval_sha256": approval_sha256,
        "phase": PHASE,
        "operation": OPERATION,
        "role": role,
        "expected_host": expected_host,
        "phase_started_at": _timestamp_text(phase_started_at),
        "release_root": os.fspath(paths["release_root"]),
        "worker_path": os.fspath(paths["worker_path"]),
        "worker_sha256": worker_sha256,
        "output_root": os.fspath(paths["output_root"]),
        "max_rows_per_table": max_rows_per_table,
        "constraints": dict(EXPECTED_CONSTRAINTS),
        "request_sha256": ZERO_SHA256,
    }
    document["request_sha256"] = _request_digest(document)
    return validate_request(document, now=phase_started_at)


def _assert_private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ConvergenceRoleObserverError(f"{label} is not a root-only directory")


def _assert_trusted_prefix_ancestors(prefix: Path, *, label: str) -> None:
    """Validate every absolute ancestor of a fixed production namespace.

    ``/tmp`` is accepted only as a root-owned sticky namespace so focused tests
    can use a root-owned private child below it.  All production prefixes live
    below ordinary root-controlled non-writable components such as ``/root``
    or ``/srv``.  Any symlink, non-root owner, or writable non-sticky ancestor
    fails before a request/release/output descriptor is opened.
    """

    if not prefix.is_absolute():
        raise ConvergenceRoleObserverError(f"{label} path is unsafe")
    current = Path("/")
    for part in prefix.parts[1:]:
        current /= part
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
        sticky_root_namespace = (
            metadata.st_uid == 0
            and bool(metadata.st_mode & stat.S_ISVTX)
        )
        if (
            current.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (stat.S_IMODE(metadata.st_mode) & 0o022 and not sticky_root_namespace)
        ):
            raise ConvergenceRoleObserverError(
                f"{label} has an unsafe ancestor component"
            )


def _assert_root_controlled_directory_chain(
    path: Path,
    *,
    boundary: Path,
    label: str,
    private: bool,
) -> None:
    """Reject symlink/non-root/writable components below a fixed root.

    A leaf-only ``stat`` leaves a raceable ancestor in the trust boundary.  The
    caller supplies a fixed lexical boundary (the production release or secret
    root), then every existing component through the requested path is opened
    only after no-follow metadata checks.  Release directories may be root 755
    but never group/world writable; secret directories are root-private.
    """

    if not path.is_absolute() or not boundary.is_absolute():
        raise ConvergenceRoleObserverError(f"{label} path is unsafe")
    _assert_trusted_prefix_ancestors(boundary, label=label)
    try:
        relative = path.relative_to(boundary)
    except ValueError as exc:
        raise ConvergenceRoleObserverError(f"{label} escapes its trusted root") from exc
    components = (boundary, *(boundary / Path(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)))
    for current in components:
        try:
            metadata = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
        unsafe_mode = 0o077 if private else 0o022
        if (
            current.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & unsafe_mode
        ):
            raise ConvergenceRoleObserverError(
                f"{label} has an unsafe ancestor component"
            )


def _assert_release_directory_chain(release_root: Path, *, label: str) -> None:
    _assert_root_controlled_directory_chain(
        release_root,
        boundary=PROJECT_ROOT_PREFIX,
        label=label,
        private=False,
    )
    _assert_private_directory(release_root, label=label)


def _inherited_descriptor(value: str | None, *, label: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[3-9][0-9]*", value) is None:
        raise ConvergenceRoleObserverError(f"{label} descriptor binding is invalid")
    return int(value)


def _assert_root_controlled_regular_descriptor(
    descriptor: int,
    *,
    label: str,
    private: bool,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} descriptor is unavailable") from exc
    unsafe_mode = 0o077 if private else 0o022
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & unsafe_mode
    ):
        raise ConvergenceRoleObserverError(f"{label} descriptor is not root-controlled")
    return metadata


def _assert_descriptor_matches_no_follow_path(
    descriptor: int,
    path: Path,
    *,
    label: str,
    directory: bool,
) -> None:
    try:
        metadata = os.fstat(descriptor)
        observed = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} path is unavailable") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        path.is_symlink()
        or not expected(observed.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (observed.st_dev, observed.st_ino)
    ):
        raise ConvergenceRoleObserverError(f"{label} descriptor differs from its canonical path")


def _release_root_identity_sha256(descriptor: int) -> str:
    """Hash held inode metadata, never a misleading release pathname string."""

    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ConvergenceRoleObserverError("immutable release root is unavailable") from exc
    return _sha256(
        {
            "schema": "production-shadow-convergence-held-release-root-identity-v1",
            "device": int(metadata.st_dev),
            "inode": int(metadata.st_ino),
            "mode": int(stat.S_IMODE(metadata.st_mode)),
            "uid": int(metadata.st_uid),
            "nlink": int(metadata.st_nlink),
        }
    )


def _require_launcher_descriptor_handoff() -> _ObserverLauncherContract:
    """Require the non-ambient FD capability before any observe request read."""

    release_root_descriptor = _inherited_descriptor(
        os.environ.get(LAUNCHER_RELEASE_ROOT_FD_ENV),
        label="observer launcher release root",
    )
    worker_descriptor = _inherited_descriptor(
        os.environ.get(LAUNCHER_WORKER_FD_ENV),
        label="observer launcher worker",
    )
    launcher_descriptor = _inherited_descriptor(
        os.environ.get(LAUNCHER_FD_ENV),
        label="observer launcher",
    )
    _assert_release_directory_descriptor(
        release_root_descriptor,
        label="observer launcher release root",
        private=True,
    )
    _assert_root_controlled_regular_descriptor(
        worker_descriptor,
        label="observer launcher worker",
        private=False,
    )
    _assert_root_controlled_regular_descriptor(
        launcher_descriptor,
        label="observer launcher",
        private=True,
    )
    return _ObserverLauncherContract(
        release_root_descriptor=release_root_descriptor,
        worker_descriptor=worker_descriptor,
        launcher_descriptor=launcher_descriptor,
    )


def _open_descriptor_numbers() -> set[int]:
    """Read this process's open descriptor table from the kernel."""

    try:
        entries = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise ConvergenceRoleObserverError(
            "observer descriptor hygiene boundary is unavailable"
        ) from exc
    descriptors: set[int] = set()
    for entry in entries:
        if not entry.isdecimal():
            raise ConvergenceRoleObserverError("observer descriptor hygiene boundary is invalid")
        descriptor = int(entry)
        try:
            os.fstat(descriptor)
        except OSError as exc:
            # Enumerating /proc/self/fd momentarily opens the directory itself.
            # It can appear in the list after it has already been closed.
            if exc.errno == getattr(os, "EBADF", 9):
                continue
            raise ConvergenceRoleObserverError(
                "observer descriptor hygiene boundary cannot inspect a descriptor"
            ) from exc
        descriptors.add(descriptor)
    return descriptors


def _close_unexpected_worker_descriptors(contract: _ObserverLauncherContract) -> None:
    """Close every inherited descriptor outside the fixed launcher contract.

    Environment scrubbing does not close descriptors inherited from a service
    manager or interactive parent.  The shell launcher performs the same
    cleanup before importing this worker; this second check makes an accidental
    launcher regression fail closed before the request or runtime config is
    read.
    """

    allowed = {
        0,
        1,
        2,
        contract.release_root_descriptor,
        contract.worker_descriptor,
        contract.launcher_descriptor,
    }
    if any(descriptor < 3 for descriptor in allowed - {0, 1, 2}):
        raise ConvergenceRoleObserverError("observer launcher descriptor is invalid")
    for descriptor in sorted(_open_descriptor_numbers() - allowed, reverse=True):
        try:
            os.close(descriptor)
        except OSError as exc:
            # A racing close is harmless only when the descriptor is already
            # gone; all other failures leave an ambient capability in scope.
            if exc.errno != getattr(os, "EBADF", 9):
                raise ConvergenceRoleObserverError(
                    "observer descriptor hygiene boundary cannot close an inherited descriptor"
                ) from exc
    unexpected = _open_descriptor_numbers() - allowed
    if unexpected:
        raise ConvergenceRoleObserverError(
            "observer descriptor hygiene boundary retained inherited descriptors"
        )


def _require_root_only_launcher_contract(
    request: Mapping[str, Any],
) -> _ObserverLauncherContract:
    """Require the fixed shell launcher FD handoff before live observation.

    The launcher itself uses ``/usr/bin/env -i`` and starts Python with
    ``-I -S`` before this file executes.  The worker receives three inherited
    descriptors rather than trusting an ambient boolean or a mutable pathname:
    the held release directory, this worker inode, and the launcher inode.
    Their Git-blob binding is checked by ``verify_exact_release`` below.
    """

    _require_isolated_observer_execution()
    document = validate_request(request)
    release_root = Path(document["release_root"])
    worker_path = Path(document["worker_path"])
    if worker_path.parent != release_root / "scripts":
        raise ConvergenceRoleObserverError("observer worker path escapes the release root")
    _assert_release_directory_chain(release_root, label="immutable release root")
    _assert_root_controlled_directory_chain(
        worker_path.parent,
        boundary=release_root,
        label="observer worker directory",
        private=False,
    )
    contract = _require_launcher_descriptor_handoff()
    _assert_descriptor_matches_no_follow_path(
        contract.release_root_descriptor,
        release_root,
        label="observer launcher release root",
        directory=True,
    )
    _assert_descriptor_matches_no_follow_path(
        contract.worker_descriptor,
        worker_path,
        label="observer launcher worker",
        directory=False,
    )
    try:
        executing = os.stat(__file__, follow_symlinks=True)
        worker_metadata = os.fstat(contract.worker_descriptor)
    except OSError as exc:
        raise ConvergenceRoleObserverError("observer worker execution path is unavailable") from exc
    if (executing.st_dev, executing.st_ino) != (worker_metadata.st_dev, worker_metadata.st_ino):
        raise ConvergenceRoleObserverError(
            "observer is not executing the launcher-held worker inode"
        )
    return contract


def verify_exact_release(
    request: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    executing_worker_path: Path | None = None,
) -> dict[str, Any]:
    """Verify held release, worker, and launcher Git blobs before observation."""

    del runner, executing_worker_path
    document = validate_request(request)
    contract = _require_root_only_launcher_contract(document)
    _verify_held_release_git_state(
        document,
        release_root_descriptor=contract.release_root_descriptor,
    )
    worker_sha256 = _verified_release_file_sha256(
        contract.worker_descriptor,
        label="observer launcher worker",
    )
    expected_worker_sha256 = _expected_release_file_sha256(
        document,
        relative_path=WORKER_RELATIVE,
        label="observer launcher worker",
        release_root_descriptor=contract.release_root_descriptor,
    )
    launcher_sha256 = _verified_release_file_sha256(
        contract.launcher_descriptor,
        label="observer launcher",
    )
    expected_launcher_sha256 = _expected_release_file_sha256(
        document,
        relative_path=LAUNCHER_RELATIVE,
        label="observer launcher",
        release_root_descriptor=contract.release_root_descriptor,
    )
    if (
        worker_sha256 != document["worker_sha256"]
        or worker_sha256 != expected_worker_sha256
        or launcher_sha256 != expected_launcher_sha256
    ):
        raise ConvergenceRoleObserverError(
            "observer launcher or worker differs from the exact release"
        )
    return {
        "release_root_sha256": _release_root_identity_sha256(
            contract.release_root_descriptor
        ),
        "head": document["release_sha"],
        "tree": document["release_tree_sha"],
        "source_tree_bound": True,
        "worker_sha256": worker_sha256,
    }


def _runtime_config_digest(document: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "config_sha256"}
    return _sha256(unsigned)


def _canonical_collector_runtime_directory(request: Mapping[str, Any]) -> Path:
    """Return the sole root-private role directory for observer inputs."""

    document = validate_request(request)
    return (
        SECRET_ROOT_PREFIX
        / str(document["operation_id"])
        / COLLECTOR_RUNTIME_CONFIG_RELATIVE.parent
        / str(document["role"])
    )


def _canonical_collector_runtime_config_path(request: Mapping[str, Any]) -> Path:
    return _canonical_collector_runtime_directory(request) / (
        COLLECTOR_RUNTIME_CONFIG_RELATIVE.name
    )


def _canonical_runtime_target_binding_path(request: Mapping[str, Any]) -> Path:
    return _canonical_collector_runtime_directory(request) / (
        COLLECTOR_RUNTIME_TARGET_BINDING_NAME
    )


def _canonical_runtime_target_set_path(request: Mapping[str, Any]) -> Path:
    return _canonical_collector_runtime_directory(request) / (
        COLLECTOR_RUNTIME_TARGET_SET_NAME
    )


def _canonical_compose_execution_plan_path(request: Mapping[str, Any]) -> Path:
    return _canonical_collector_runtime_directory(request) / COMPOSE_EXECUTION_PLAN_NAME


def _canonical_compose_execution_material_path(request: Mapping[str, Any]) -> Path:
    return _canonical_collector_runtime_directory(request) / COMPOSE_EXECUTION_MATERIAL_NAME


def _compose_role_compose_path(*, operation_id: str, role: str) -> str:
    return (
        f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/"
        f"{role}/compose-observer-execution.yml"
    )


def _compose_role_environment_path(*, operation_id: str, role: str) -> str:
    return (
        f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/"
        f"{role}/compose-observer-execution.env"
    )


def _compose_role_project_name(*, operation_id: str, role: str) -> str:
    return f"tb3p-{operation_id.replace('-', '')}-{role.replace('_', '-')}"


def _compose_network_name(*, operation_id: str, role: str) -> str:
    return f"{_compose_role_project_name(operation_id=operation_id, role=role)}_{role}"


def _compose_container_id_file(*, operation_id: str, role: str) -> str:
    return (
        f"{SECRET_ROOT_PREFIX}/{operation_id}/convergence-observer-runtime/"
        f"{role}/compose-observer.cid"
    )


def _compose_container_collector_path(*, operation_id: str, release_sha: str) -> str:
    return (
        f"{PROJECT_ROOT_PREFIX}/{operation_id}/releases/{release_sha}/"
        f"{CONTAINER_COLLECTOR_RELATIVE.as_posix()}"
    )


def _compose_container_collector_argv(
    *, campaign_id: str, operation_id: str, release_sha: str, source_manifest_path: str
) -> list[str]:
    return [
        "python",
        "-B",
        "-I",
        "-S",
        _compose_container_collector_path(
            operation_id=operation_id,
            release_sha=release_sha,
        ),
        "--campaign-id",
        campaign_id,
        "--release-sha",
        release_sha,
        "--source-manifest-path",
        source_manifest_path,
        "--plan-sha256",
    ]


def _compose_plan_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "plan_sha256"})


def _compose_material_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "material_sha256"})


def _compose_inspection_digest(
    *,
    operation_id: str,
    role: str,
    role_material_sha256: str,
    role_compose_sha256: str,
    role_environment_sha256: str,
) -> str:
    return _sha256(
        {
            "operation_id": operation_id,
            "role": role,
            "role_material_sha256": role_material_sha256,
            "role_compose_sha256": role_compose_sha256,
            "role_environment_sha256": role_environment_sha256,
        }
    )


def _compose_image_ids(value: Any) -> dict[str, str]:
    kinds = ("app", "postgres", "redis", "nginx")
    if not isinstance(value, Mapping) or set(value) != set(kinds):
        raise ConvergenceRoleObserverError("Compose execution image IDs differ")
    result = {kind: value[kind] for kind in kinds}
    if (
        any(
            not isinstance(item, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
            for item in result.values()
        )
        or len(set(result.values())) != len(result)
    ):
        raise ConvergenceRoleObserverError("Compose execution image IDs are invalid")
    return result


def _compose_mount(source: str) -> dict[str, Any]:
    return {
        "type": "bind",
        "source": source,
        "target": source,
        "read_only": True,
        "bind": {"create_host_path": False},
    }


def _load_compose_execution_inputs(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the root-only plan/material pair needed before Compose execution.

    This deliberately uses only the worker's fixed data validators.  It does
    not import a worktree module, execute a held Git blob, or fall back to the
    historical host-Python collector.
    """

    bound = validate_request(request)
    if bound["role"] not in RUNTIME_SNAPSHOT_ROLES:
        raise ConvergenceRoleObserverError("Witness has no Compose runtime observation")
    plan_path = _canonical_compose_execution_plan_path(bound)
    material_path = _canonical_compose_execution_material_path(bound)
    _assert_private_directory_chain(plan_path.parent, label="Compose execution input directory")
    plan = _read_canonical_root_json(
        plan_path,
        label="Compose execution plan",
        max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
    )
    material = _read_canonical_root_json(
        material_path,
        label="Compose execution material",
        max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
    )
    return (
        _validate_compose_execution_plan(plan, request=bound),
        _validate_compose_execution_material(material, request=bound, plan=plan),
    )


def _validate_compose_execution_plan(
    value: Any,
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    bound = validate_request(request)
    if not isinstance(value, Mapping) or set(value) != COMPOSE_EXECUTION_PLAN_FIELDS:
        raise ConvergenceRoleObserverError("Compose execution plan fields differ")
    document = dict(value)
    role = str(bound["role"])
    operation = str(bound["operation_id"])
    project_name = _compose_role_project_name(operation_id=operation, role=role)
    service = f"{role}_sync_observer"
    profile = f"{role.replace('_', '-')}-observe"
    for field in ("campaign_id", "operation_id", "release_sha", "manifest_sha256", "role"):
        if document.get(field) != bound.get(field):
            raise ConvergenceRoleObserverError(f"Compose execution plan {field} differs")
    if (
        document.get("schema") != COMPOSE_EXECUTION_PLAN_SCHEMA
        or document.get("status") != "planned-not-executed"
        or document.get("runtime_target_binding_sha256") != bound["runtime_target_binding_sha256"]
        or document.get("service") != service
        or document.get("profile") != profile
        or document.get("project_name") != project_name
        or document.get("internal_network") != role
        or document.get("network_name") != _compose_network_name(operation_id=operation, role=role)
        or document.get("container_id_file")
        != _compose_container_id_file(operation_id=operation, role=role)
        or document.get("role_compose_path") != _compose_role_compose_path(operation_id=operation, role=role)
        or document.get("role_environment_path") != _compose_role_environment_path(operation_id=operation, role=role)
        or document.get("collector_path")
        != _compose_container_collector_path(
            operation_id=operation,
            release_sha=str(bound["release_sha"]),
        )
        or document.get("collector_source_manifest_path")
        != f"{SECRET_ROOT_PREFIX}/{operation}/convergence-observer-runtime/{role}/collector-source-manifest.json"
        or document.get("timeout_seconds") != COMPOSE_EXECUTION_TIMEOUT_SECONDS
        or document.get("max_stdout_bytes") != COMPOSE_EXECUTION_MAX_STDOUT_BYTES
        or document.get("max_stderr_bytes") != COMPOSE_EXECUTION_MAX_STDERR_BYTES
        or document.get("production_mutation_forbidden") is not True
        or document.get("object_storage_contact_forbidden") is not True
    ):
        raise ConvergenceRoleObserverError("Compose execution plan identity differs")
    for field in (
        "canonical_compose_sha256", "role_compose_sha256", "role_environment_sha256",
        "collector_sha256", "collector_delegate_sha256", "collector_closure_sha256",
        "collector_source_manifest_sha256",
        "role_material_sha256", "role_material_inspection_sha256",
        "runtime_target_binding_sha256", "plan_sha256",
    ):
        _nonzero_sha256(document.get(field), label=f"Compose execution plan {field}")
    if document["role_material_inspection_sha256"] != _compose_inspection_digest(
        operation_id=operation,
        role=role,
        role_material_sha256=document["role_material_sha256"],
        role_compose_sha256=document["role_compose_sha256"],
        role_environment_sha256=document["role_environment_sha256"],
    ):
        raise ConvergenceRoleObserverError("Compose execution plan material inspection differs")
    image_ids = _compose_image_ids(document.get("runtime_image_ids"))
    release_root = f"{PROJECT_ROOT_PREFIX}/{operation}/releases/{bound['release_sha']}"
    input_root = f"{SECRET_ROOT_PREFIX}/{operation}/convergence-observer-runtime/{role}"
    if (
        document.get("release_mount") != _compose_mount(release_root)
        or document.get("runtime_input_mount") != _compose_mount(input_root)
    ):
        raise ConvergenceRoleObserverError("Compose execution plan mounts differ")
    expected_argv = [
        COMPOSE_DOCKER, "compose", "--project-name", project_name, "--env-file",
        _compose_role_environment_path(operation_id=operation, role=role), "--file",
        _compose_role_compose_path(operation_id=operation, role=role), "--profile", profile,
        "run", "--cidfile", _compose_container_id_file(operation_id=operation, role=role),
        "--rm", "--no-deps", service,
    ]
    expected_cleanup = [
        COMPOSE_DOCKER, "ps", "--all", "--quiet", "--filter",
        f"label=com.docker.compose.project={project_name}", "--filter",
        "label=com.docker.compose.oneoff=True",
    ]
    expected_config_probe = [
        COMPOSE_DOCKER, "compose", "--project-name", project_name, "--env-file",
        _compose_role_environment_path(operation_id=operation, role=role), "--file",
        _compose_role_compose_path(operation_id=operation, role=role), "--profile", profile,
        "config", "--format", "json",
    ]
    expected_collector_argv = _compose_container_collector_argv(
        campaign_id=str(bound["campaign_id"]),
        operation_id=operation,
        release_sha=str(bound["release_sha"]),
        source_manifest_path=str(document["collector_source_manifest_path"]),
    )
    if (
        document.get("compose_argv") != expected_argv
        or document.get("collector_argv") != expected_collector_argv
        or document.get("config_probe_argv") != expected_config_probe
        or document.get("cleanup_probe_argv") != expected_cleanup
    ):
        raise ConvergenceRoleObserverError("Compose execution argv differs")
    if document.get("plan_sha256") != _compose_plan_digest(document):
        raise ConvergenceRoleObserverError("Compose execution plan digest differs")
    _nonzero_sha256(
        document.get("resolved_observer_service_sha256"),
        label="Compose execution resolved observer service",
    )
    if document["collector_closure_sha256"] != _sha256(
        {
            "collector_sha256": document["collector_sha256"],
            "delegate_sha256": document["collector_delegate_sha256"],
            "source_manifest_sha256": document["collector_source_manifest_sha256"],
        }
    ):
        raise ConvergenceRoleObserverError("Compose execution collector closure differs")
    return {**document, "runtime_image_ids": image_ids}


def _validate_compose_execution_material(
    value: Any,
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    bound = validate_request(request)
    checked_plan = _validate_compose_execution_plan(plan, request=bound)
    if not isinstance(value, Mapping) or set(value) != COMPOSE_EXECUTION_MATERIAL_FIELDS:
        raise ConvergenceRoleObserverError("Compose execution material fields differ")
    document = dict(value)
    for field in (
        "campaign_id", "operation_id", "release_sha", "manifest_sha256", "role",
        "runtime_target_binding_sha256",
    ):
        if document.get(field) != bound.get(field):
            raise ConvergenceRoleObserverError(f"Compose execution material {field} differs")
    if (
        document.get("schema") != COMPOSE_EXECUTION_MATERIAL_SCHEMA
        or document.get("plan_sha256") != checked_plan["plan_sha256"]
    ):
        raise ConvergenceRoleObserverError("Compose execution material binding differs")
    _nonzero_sha256(
        document.get("role_material_archive_inspection_sha256"),
        label="Compose execution material archive inspection",
    )
    if document.get("collector_source_manifest_sha256") != checked_plan["collector_source_manifest_sha256"]:
        raise ConvergenceRoleObserverError("Compose execution material source manifest binding differs")
    _nonzero_sha256(document.get("material_sha256"), label="Compose execution material digest")
    if document["material_sha256"] != _compose_material_digest(document):
        raise ConvergenceRoleObserverError("Compose execution material digest differs")
    return document


def _reopen_compose_execution_anchors(
    request: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    material: Mapping[str, Any],
) -> None:
    """Reopen every execution input immediately before Docker can start."""

    bound = validate_request(request)
    checked_plan = _validate_compose_execution_plan(plan, request=bound)
    _validate_compose_execution_material(material, request=bound, plan=checked_plan)
    compose_path = Path(str(checked_plan["role_compose_path"]))
    environment_path = Path(str(checked_plan["role_environment_path"]))
    _assert_private_directory_chain(
        compose_path.parent,
        label="Compose execution immutable overlay directory",
    )
    _assert_private_directory_chain(
        environment_path.parent,
        label="Compose execution role environment directory",
    )
    try:
        compose_bytes = _read_root_only_bytes(
            compose_path,
            label="Compose execution rendered role file",
            max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
        )
        environment_bytes = _read_root_only_bytes(
            environment_path,
            label="Compose execution role environment file",
            max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
        )
    except _SecureLocalFileError as exc:
        raise ConvergenceRoleObserverError("Compose execution role files are unavailable") from exc
    if (
        hashlib.sha256(compose_bytes).hexdigest() != checked_plan["role_compose_sha256"]
        or hashlib.sha256(environment_bytes).hexdigest()
        != checked_plan["role_environment_sha256"]
    ):
        raise ConvergenceRoleObserverError("Compose execution role file digest differs")
    collector_path = Path(str(checked_plan["collector_path"]))
    try:
        collector_bytes = _read_root_only_bytes(
            collector_path,
            label="Compose execution container collector",
            max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
        )
    except _SecureLocalFileError as exc:
        raise ConvergenceRoleObserverError("Compose execution container collector is unavailable") from exc
    if hashlib.sha256(collector_bytes).hexdigest() != checked_plan["collector_sha256"]:
        raise ConvergenceRoleObserverError("Compose execution container collector digest differs")
    collector_delegate_path = collector_path.parent / "collect_three_site_staging_convergence_snapshot.py"
    try:
        collector_delegate_bytes = _read_root_only_bytes(
            collector_delegate_path,
            label="Compose execution container collector delegate",
            max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
        )
    except _SecureLocalFileError as exc:
        raise ConvergenceRoleObserverError("Compose execution container collector delegate is unavailable") from exc
    if hashlib.sha256(collector_delegate_bytes).hexdigest() != checked_plan["collector_delegate_sha256"]:
        raise ConvergenceRoleObserverError("Compose execution container collector delegate digest differs")
    source_manifest_path = Path(str(checked_plan["collector_source_manifest_path"]))
    try:
        source_manifest_bytes = _read_root_only_bytes(
            source_manifest_path,
            label="Compose execution collector source manifest",
            max_size=COLLECTOR_SOURCE_MANIFEST_MAX_BYTES,
        )
    except _SecureLocalFileError as exc:
        raise ConvergenceRoleObserverError("Compose execution collector source manifest is unavailable") from exc
    if hashlib.sha256(source_manifest_bytes).hexdigest() != checked_plan["collector_source_manifest_sha256"]:
        raise ConvergenceRoleObserverError("Compose execution collector source manifest digest differs")
    _reopen_collector_source_manifest(
        source_manifest_bytes,
        release_root=Path(f"{PROJECT_ROOT_PREFIX}/{bound['operation_id']}/releases/{bound['release_sha']}"),
        release_sha=str(bound["release_sha"]),
        release_tree_sha=str(bound["release_tree_sha"]),
    )
    binding = _load_runtime_target_inputs(bound)
    if (
        binding["binding_sha256"] != checked_plan["runtime_target_binding_sha256"]
        or binding["canonical_compose_sha256"]
        != checked_plan["canonical_compose_sha256"]
        or binding["role_material_sha256"] != checked_plan["role_material_sha256"]
        or binding["role_runtime_image_ids"] != checked_plan["runtime_image_ids"]
    ):
        raise ConvergenceRoleObserverError("Compose execution binding or target-set differs")


def _reopen_collector_source_manifest(
    payload: bytes, *, release_root: Path, release_sha: str, release_tree_sha: str
) -> None:
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("Compose execution collector source manifest is invalid") from exc
    if (
        not isinstance(document, Mapping)
        or set(document) != {"schema", "release_sha", "release_tree_sha", "files", "source_manifest_sha256"}
        or document.get("schema") != COLLECTOR_SOURCE_MANIFEST_SCHEMA
        or document.get("release_sha") != release_sha
        or document.get("release_tree_sha") != release_tree_sha
        or not isinstance(document.get("files"), Mapping)
        or document.get("source_manifest_sha256")
        != _sha256({key: value for key, value in document.items() if key != "source_manifest_sha256"})
    ):
        raise ConvergenceRoleObserverError("Compose execution collector source manifest differs")
    files = _validated_collector_source_manifest_files(document["files"])
    _assert_release_directory_chain(
        release_root,
        label="Compose execution collector source release root",
    )
    with _open_release_root_nofollow(
        release_root,
        label="Compose execution collector source release root",
    ) as release_root_descriptor:
        for relative in sorted(files):
            expected_sha256 = files[relative]
            content = _read_release_relative_from_open_root(
                release_root_descriptor,
                relative,
                max_size=COLLECTOR_SOURCE_MANIFEST_MAX_SOURCE_BYTES,
                allow_empty=relative in COLLECTOR_SOURCE_MANIFEST_REQUIRED_PATHS,
            )
            if hashlib.sha256(content).hexdigest() != expected_sha256:
                raise ConvergenceRoleObserverError("Compose execution collector source entry digest differs")


def _validated_collector_source_manifest_files(value: Any) -> dict[str, str]:
    """Mirror the container loader's closed, importable source namespace."""

    if (
        not isinstance(value, Mapping)
        or not COLLECTOR_SOURCE_MANIFEST_REQUIRED_PATHS.issubset(value)
        or not 1 <= len(value) <= COLLECTOR_SOURCE_MANIFEST_MAX_FILES
    ):
        raise ConvergenceRoleObserverError("Compose execution collector source manifest is incomplete")
    files: dict[str, str] = {}
    for relative, expected_sha256 in value.items():
        if (
            not isinstance(relative, str)
            or not relative.isascii()
            or not isinstance(expected_sha256, str)
        ):
            raise ConvergenceRoleObserverError("Compose execution collector source manifest path is invalid")
        try:
            path = PurePosixPath(relative)
        except TypeError as exc:
            raise ConvergenceRoleObserverError(
                "Compose execution collector source manifest path is invalid"
            ) from exc
        if (
            relative != path.as_posix()
            or path.is_absolute()
            or len(path.parts) < 2
            or any(part in {"", ".", "..", "__pycache__"} for part in path.parts)
            or path.suffix != ".py"
        ):
            raise ConvergenceRoleObserverError("Compose execution collector source manifest path is invalid")
        if relative.startswith("scripts/"):
            if relative not in COLLECTOR_SOURCE_MANIFEST_REQUIRED_PATHS:
                raise ConvergenceRoleObserverError("Compose execution collector source manifest path is invalid")
        elif path.parts[0] in COLLECTOR_SOURCE_MANIFEST_PROJECT_PACKAGES:
            module_parts = path.parts[:-1]
            stem = path.stem
            if (
                any(not part.isidentifier() or keyword.iskeyword(part) for part in module_parts)
                or (stem != "__init__" and (not stem.isidentifier() or keyword.iskeyword(stem)))
            ):
                raise ConvergenceRoleObserverError("Compose execution collector source manifest path is invalid")
        else:
            raise ConvergenceRoleObserverError("Compose execution collector source manifest path is invalid")
        _nonzero_sha256(expected_sha256, label="Compose execution collector source entry")
        files[relative] = expected_sha256
    return files


def _metadata_stable(
    before: os.stat_result,
    after: os.stat_result,
    *,
    include_size: bool,
) -> bool:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if include_size:
        fields += ("st_size",)
    return all(getattr(before, field) == getattr(after, field) for field in fields)


@contextmanager
def _open_release_root_nofollow(release_root: Path, *, label: str) -> Iterator[int]:
    """Hold one verified release-root FD for the entire manifest traversal."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(release_root, flags)
            before = _assert_release_directory_descriptor(
                descriptor,
                label=label,
                private=True,
            )
        except OSError as exc:
            raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
        yield descriptor
        try:
            after = _assert_release_directory_descriptor(
                descriptor,
                label=label,
                private=True,
            )
            named = release_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise ConvergenceRoleObserverError(f"{label} changed while read") from exc
        if (
            not _metadata_stable(before, after, include_size=True)
            or release_root.is_symlink()
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise ConvergenceRoleObserverError(f"{label} changed while read")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_release_relative_nofollow(
    release_root: Path, relative_path: str, *, max_size: int, allow_empty: bool = False
) -> bytes:
    """Read one release file through a held root FD without pathname fallback."""

    with _open_release_root_nofollow(release_root, label="release root") as descriptor:
        return _read_release_relative_from_open_root(
            descriptor,
            relative_path,
            max_size=max_size,
            allow_empty=allow_empty,
        )


def _read_release_relative_from_open_root(
    release_root_descriptor: int,
    relative_path: str,
    *,
    max_size: int,
    allow_empty: bool = False,
) -> bytes:
    """Read one leaf through openat and prove its whole ancestor chain is stable."""

    if (
        not isinstance(relative_path, str)
        or not relative_path
        or relative_path.startswith("/")
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise ConvergenceRoleObserverError("release-relative path is invalid")
    if max_size < 1:
        raise ConvergenceRoleObserverError("release-relative file size limit is invalid")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = release_root_descriptor
    opened_directories: list[int] = []
    directory_links: list[tuple[int, str, int, os.stat_result]] = []
    file_fd = -1
    try:
        _assert_release_directory_descriptor(
            release_root_descriptor,
            label="release root",
            private=True,
        )
        for component in relative_path.split("/")[:-1]:
            next_fd = os.open(
                component,
                directory_flags,
                dir_fd=current_fd,
            )
            info = _assert_release_directory_descriptor(
                next_fd,
                label="release path ancestor",
                private=False,
            )
            named = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
                os.close(next_fd)
                raise ConvergenceRoleObserverError("release path ancestor changed while read")
            directory_links.append((current_fd, component, next_fd, info))
            opened_directories.append(next_fd)
            current_fd = next_fd
        file_fd = os.open(
            relative_path.split("/")[-1],
            file_flags,
            dir_fd=current_fd,
        )
        before = _assert_root_controlled_regular_descriptor(
            file_fd,
            label="release-relative file",
            private=False,
        )
        if before.st_size > max_size or (before.st_size == 0 and not allow_empty):
            raise ConvergenceRoleObserverError("release-relative file is unsafe")
        payload = bytearray()
        while True:
            block = os.read(file_fd, min(64 * 1024, max_size + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
            if len(payload) > max_size:
                raise ConvergenceRoleObserverError("release-relative file is oversized")
        after = _assert_root_controlled_regular_descriptor(
            file_fd,
            label="release-relative file",
            private=False,
        )
        named_after = os.stat(
            relative_path.split("/")[-1],
            dir_fd=current_fd,
            follow_symlinks=False,
        )
        if (
            not _metadata_stable(before, after, include_size=True)
            or (before.st_dev, before.st_ino) != (named_after.st_dev, named_after.st_ino)
        ):
            raise ConvergenceRoleObserverError("release-relative file changed while read")
        for parent_fd, component, descriptor, directory_before in directory_links:
            directory_after = _assert_release_directory_descriptor(
                descriptor,
                label="release path ancestor",
                private=False,
            )
            named_directory = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not _metadata_stable(directory_before, directory_after, include_size=True)
                or (directory_before.st_dev, directory_before.st_ino)
                != (named_directory.st_dev, named_directory.st_ino)
            ):
                raise ConvergenceRoleObserverError("release path ancestor changed while read")
        return bytes(payload)
    except OSError as exc:
        raise ConvergenceRoleObserverError("release-relative file is unavailable") from exc
    finally:
        if file_fd >= 0:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for descriptor in reversed(opened_directories):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _compose_result(runner: Callable[..., subprocess.CompletedProcess[bytes]], argv: list[str], *, label: str) -> bytes:
    try:
        result = runner(
            argv,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConvergenceRoleObserverError(f"Compose {label} could not start") from exc
    if (
        not isinstance(result, subprocess.CompletedProcess)
        or result.returncode != 0
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stderr) > COMPOSE_EXECUTION_MAX_STDERR_BYTES
    ):
        raise ConvergenceRoleObserverError(f"Compose {label} was rejected")
    return result.stdout


def _validate_resolved_compose_overlay(payload: bytes, *, plan: Mapping[str, Any]) -> None:
    """Accept only the exact, secret-free observer service projection.

    ``docker compose config`` is executed solely to catch interpolation or
    Compose resolution drift before a one-shot container can be created.  Its
    complete output may contain runtime credentials, so it is never persisted.
    """

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("Compose resolved config is invalid") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
        raise ConvergenceRoleObserverError("Compose resolved config services differ")
    service = document["services"].get(plan["service"])
    fields = {
        "image", "pull_policy", "profiles", "restart", "command", "depends_on",
        "networks", "volumes", "env_file", "read_only", "cap_drop", "security_opt",
    }
    if not isinstance(service, Mapping) or set(service) != fields:
        raise ConvergenceRoleObserverError("Compose resolved observer service differs")
    if _sha256(dict(service)) != plan["resolved_observer_service_sha256"]:
        raise ConvergenceRoleObserverError("Compose resolved observer service digest differs")
    networks = document.get("networks")
    expected_network = {
        "labels": {
            "trading-bot.production.operation-id": plan["operation_id"],
        },
        "internal": True,
    }
    if not isinstance(networks, Mapping) or networks.get(plan["internal_network"]) != expected_network:
        raise ConvergenceRoleObserverError("Compose resolved observer network differs")


def _compose_cleanup_probe(
    plan: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    label: str,
) -> None:
    output = _compose_result(runner, list(plan["cleanup_probe_argv"]), label=label)
    if output.strip():
        raise ConvergenceRoleObserverError(f"Compose {label} found one-shot residue")


def _read_compose_container_id(path: Path) -> str:
    try:
        payload = _read_root_only_bytes(
            path,
            label="Compose observer container ID file",
            max_size=256,
        )
    except _SecureLocalFileError as exc:
        raise ConvergenceRoleObserverError("Compose observer container ID is unavailable") from exc
    try:
        value = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ConvergenceRoleObserverError("Compose observer container ID is invalid") from exc
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ConvergenceRoleObserverError("Compose observer container ID is invalid")
    return value


def _compose_inspection_proof(
    plan: Mapping[str, Any],
    *,
    container_id: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> dict[str, Any]:
    """Reduce live Docker inspect output to a small operation-bound proof."""

    container_output = _compose_result(
        runner,
        [COMPOSE_DOCKER, "inspect", container_id],
        label="container inspect",
    )
    network_output = _compose_result(
        runner,
        [COMPOSE_DOCKER, "network", "inspect", str(plan["network_name"])],
        label="network inspect",
    )
    try:
        container_items = json.loads(container_output.decode("utf-8"))
        network_items = json.loads(network_output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("Compose inspect output is invalid") from exc
    if (
        not isinstance(container_items, list)
        or len(container_items) != 1
        or not isinstance(container_items[0], Mapping)
        or not isinstance(network_items, list)
        or len(network_items) != 1
        or not isinstance(network_items[0], Mapping)
    ):
        raise ConvergenceRoleObserverError("Compose inspect output shape differs")
    container = container_items[0]
    network = network_items[0]
    labels = container.get("Config", {}).get("Labels") if isinstance(container.get("Config"), Mapping) else None
    network_labels = network.get("Labels")
    networks = (
        container.get("NetworkSettings", {}).get("Networks")
        if isinstance(container.get("NetworkSettings"), Mapping)
        else None
    )
    operation_label = "trading-bot.production.operation-id"
    if (
        not isinstance(labels, Mapping)
        or not isinstance(network_labels, Mapping)
        or not isinstance(networks, Mapping)
        or container.get("Id") != container_id
        or labels.get("com.docker.compose.project") != plan["project_name"]
        or labels.get("com.docker.compose.service") != plan["service"]
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get(operation_label) != plan["operation_id"]
        or container.get("Image") != plan["runtime_image_ids"]["app"]
        or not isinstance(container.get("Config"), Mapping)
        or container["Config"].get("Image") != plan["runtime_image_ids"]["app"]
        or network.get("Name") != plan["network_name"]
        or network.get("Internal") is not True
        or network_labels.get(operation_label) != plan["operation_id"]
        or plan["network_name"] not in networks
        or not isinstance(network.get("Id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(network["Id"])) is None
    ):
        raise ConvergenceRoleObserverError("Compose inspect identity differs")
    return {
        "execution_plan_sha256": plan["plan_sha256"],
        "receipt_sha256": _sha256(
            {
                "schema": COMPOSE_EXECUTION_RECEIPT_SCHEMA,
                "plan_sha256": plan["plan_sha256"],
                "container_id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
                "network_id_sha256": hashlib.sha256(str(network["Id"]).encode("ascii")).hexdigest(),
                "operation_id": plan["operation_id"],
                "project_name": plan["project_name"],
                "service": plan["service"],
                "network_name": plan["network_name"],
            }
        ),
        "container_id_sha256": hashlib.sha256(container_id.encode("ascii")).hexdigest(),
        "network_id_sha256": hashlib.sha256(str(network["Id"]).encode("ascii")).hexdigest(),
        "cleanup_verified": True,
    }


def _execute_compose_runtime_observer(
    request: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    """Run only the fixed Compose plan; every cleanup/inspect failure blocks."""

    plan, material = _load_compose_execution_inputs(request)
    _reopen_compose_execution_anchors(request, plan=plan, material=material)
    container_id_path = Path(str(plan["container_id_file"]))
    if container_id_path.exists():
        raise ConvergenceRoleObserverError("Compose observer has stale container-ID residue")
    _compose_cleanup_probe(plan, runner=runner, label="preflight cleanup")
    _validate_resolved_compose_overlay(
        _compose_result(
            runner,
            list(plan["config_probe_argv"]),
            label="resolved config",
        ),
        plan=plan,
    )
    # ``--cidfile`` is inside the root-only material directory.  The bounded
    # Compose argv was validated above and is the sole command that can collect
    # a runtime snapshot; the historical host-Python collector is never used.
    compose_argv = [
        *list(plan["compose_argv"]),
        *list(plan["collector_argv"]),
        str(request["plan_sha256"]),
        "--max-rows-per-table",
        str(request["max_rows_per_table"]),
    ]
    try:
        process = subprocess.Popen(
            compose_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            cwd=f"{PROJECT_ROOT_PREFIX}/{plan['operation_id']}/releases/{plan['release_sha']}",
            env=SAFE_ENV,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConvergenceRoleObserverError("Compose observer could not start") from exc
    try:
        deadline = time.monotonic() + COMPOSE_EXECUTION_TIMEOUT_SECONDS
        container_id: str | None = None
        while time.monotonic() < deadline:
            if container_id_path.exists():
                container_id = _read_compose_container_id(container_id_path)
                break
            if process.poll() is not None:
                break
            time.sleep(0.025)
        if container_id is None:
            raise ConvergenceRoleObserverError("Compose observer did not expose a live container ID")
        proof = _validate_compose_execution_proof(
            _compose_inspection_proof(plan, container_id=container_id, runner=runner),
            request=request,
            expected_execution_plan_sha256=plan["plan_sha256"],
        )
        stdout, stderr = process.communicate(timeout=COMPOSE_EXECUTION_TIMEOUT_SECONDS)
        if process.returncode != 0 or len(stdout) > COMPOSE_EXECUTION_MAX_STDOUT_BYTES or len(stderr) > COMPOSE_EXECUTION_MAX_STDERR_BYTES:
            raise ConvergenceRoleObserverError("Compose observer process was rejected")
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise
    finally:
        # A cidfile is executor scratch, not campaign data.  It must not be
        # retained after the one-shot process and its proof have completed.
        try:
            if container_id_path.exists():
                container_id_path.unlink()
        except OSError as exc:
            raise ConvergenceRoleObserverError("Compose observer cidfile cleanup failed") from exc
    _compose_cleanup_probe(plan, runner=runner, label="postflight cleanup")
    try:
        snapshot = _strict_json_object(stdout, label="Compose observer stdout")
    except ConvergenceRoleObserverError:
        raise
    return snapshot, proof


def _validate_compose_execution_proof(
    value: Any,
    *,
    request: Mapping[str, Any],
    expected_execution_plan_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "execution_plan_sha256", "receipt_sha256", "container_id_sha256",
        "network_id_sha256", "cleanup_verified",
    }:
        raise ConvergenceRoleObserverError("Compose execution proof fields differ")
    document = dict(value)
    for field in (
        "execution_plan_sha256", "receipt_sha256", "container_id_sha256", "network_id_sha256",
    ):
        _nonzero_sha256(document.get(field), label=f"Compose execution proof {field}")
    if (
        expected_execution_plan_sha256 is not None
        and document["execution_plan_sha256"] != expected_execution_plan_sha256
    ):
        raise ConvergenceRoleObserverError("Compose execution proof plan differs from installed plan")
    if document.get("cleanup_verified") is not True:
        raise ConvergenceRoleObserverError("Compose execution cleanup proof differs")
    return document


def _private_test_compose_execution_proof(request: Mapping[str, Any]) -> dict[str, Any]:
    """Keep old semantic tests isolated from the production executor path."""

    seed = _sha256({"request_sha256": request["request_sha256"], "mode": "test-seam"})
    return {
        "execution_plan_sha256": _sha256({"seed": seed, "kind": "plan"}),
        "receipt_sha256": _sha256({"seed": seed, "kind": "receipt"}),
        "container_id_sha256": _sha256({"seed": seed, "kind": "container"}),
        "network_id_sha256": _sha256({"seed": seed, "kind": "network"}),
        "cleanup_verified": True,
    }


def _assert_private_directory_chain(path: Path, *, label: str) -> None:
    """Reject a writable ancestor that could swap a trusted config directory."""

    _assert_root_controlled_directory_chain(
        path,
        boundary=SECRET_ROOT_PREFIX,
        label=label,
        private=True,
    )


def _ensure_private_secret_directory_chain(path: Path, *, label: str) -> Path:
    """Create only missing root-private components below the existing secret root."""

    if not path.is_absolute():
        raise ConvergenceRoleObserverError(f"{label} path is unsafe")
    try:
        relative = path.relative_to(SECRET_ROOT_PREFIX)
    except ValueError as exc:
        raise ConvergenceRoleObserverError(f"{label} escapes the secret root") from exc
    _assert_trusted_prefix_ancestors(SECRET_ROOT_PREFIX, label=label)
    _assert_private_directory(SECRET_ROOT_PREFIX, label=label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(SECRET_ROOT_PREFIX, flags)
        _assert_release_directory_descriptor(
            descriptor,
            label=label,
            private=True,
        )
        for part in relative.parts:
            if part in {"", ".", ".."}:
                raise ConvergenceRoleObserverError(f"{label} path is unsafe")
            try:
                os.mkdir(part, OUTPUT_DIRECTORY_MODE, dir_fd=descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise ConvergenceRoleObserverError(f"{label} cannot be created") from exc
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
            os.close(descriptor)
            descriptor = child
            _assert_release_directory_descriptor(descriptor, label=label, private=True)
        return path
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _config_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConvergenceRoleObserverError(f"collector runtime {label} is invalid")
    if len(value.encode("utf-8")) > MAX_COLLECTOR_RUNTIME_ENV_VALUE_BYTES:
        raise ConvergenceRoleObserverError(f"collector runtime {label} is oversized")
    return value


def _read_canonical_root_json(
    path: Path,
    *,
    label: str,
    max_size: int,
) -> dict[str, Any]:
    """Read one root-only canonical JSON input without a path race."""

    try:
        payload = _read_root_only_bytes(path, label=label, max_size=max_size)
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (_SecureLocalFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document):
        raise ConvergenceRoleObserverError(f"{label} is not canonical JSON")
    return document


def _load_runtime_target_inputs(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen the installed target closure before a collector can read DB."""

    bound = validate_request(request)
    binding_path = _canonical_runtime_target_binding_path(bound)
    target_set_path = _canonical_runtime_target_set_path(bound)
    _assert_private_directory_chain(
        binding_path.parent,
        label="collector runtime target input directory",
    )
    binding_document = _read_canonical_root_json(
        binding_path,
        label="collector runtime target binding",
        max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
    )
    target_payload = _read_root_only_bytes(
        target_set_path,
        label="collector runtime target set",
        max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
    )
    try:
        binding = _validate_observer_runtime_target_binding(
            binding_document,
            campaign_id=bound["campaign_id"],
            operation_id=bound["operation_id"],
            release_sha=bound["release_sha"],
            manifest_sha256=bound["manifest_sha256"],
            role=bound["role"],
            label="collector runtime target binding",
        )
        if binding["binding_sha256"] != bound["runtime_target_binding_sha256"]:
            raise ValueError("binding digest differs from request")
        target_set = _validate_runtime_target_payload_descriptor(
            target_payload,
            binding["convergence_runtime_targets"],
            operation_id=bound["operation_id"],
            release_sha=bound["release_sha"],
            canonical_compose_sha256=binding["canonical_compose_sha256"],
            label="collector runtime target set",
        )
    except Exception as exc:
        raise ConvergenceRoleObserverError(
            "collector runtime target inputs differ from the exact request"
        ) from exc
    if target_set["roles"][bound["role"]] != binding["runtime_target_row"]:
        raise ConvergenceRoleObserverError(
            "collector runtime target row differs from the installed target set"
        )
    return binding


def _validate_collector_runtime_config(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    runtime_target_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the root-owned, request-bound minimal collector environment."""

    bound = validate_request(request)
    if (
        not isinstance(value, Mapping)
        or set(value) != COLLECTOR_RUNTIME_CONFIG_FIELDS
        or value.get("schema") != COLLECTOR_RUNTIME_CONFIG_SCHEMA
    ):
        raise ConvergenceRoleObserverError("collector runtime config fields differ")
    document = dict(value)
    for field in (
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "request_sha256",
        "runtime_target_binding_sha256",
    ):
        if document.get(field) != bound.get(field):
            raise ConvergenceRoleObserverError(
                f"collector runtime config {field} differs from request"
            )
    if document.get("config_sha256") != _runtime_config_digest(document):
        raise ConvergenceRoleObserverError("collector runtime config digest differs")
    environment = document.get("environment")
    if not isinstance(environment, Mapping) or set(environment) != COLLECTOR_RUNTIME_ENV_FIELDS:
        raise ConvergenceRoleObserverError("collector runtime environment fields differ")
    checked = {
        name: _config_text(item, label=name)
        for name, item in environment.items()
    }
    expected_server_mode = "foreign" if bound["role"] == "bot_fi" else "iran"
    expected_authority = "foreign" if bound["role"] == "bot_fi" else "webapp"
    if (
        checked["TZ"] != "UTC"
        or checked["ENVIRONMENT"] != "production"
        or checked["TOPOLOGY_SCHEMA_VERSION"] != "three-site-dr-v1"
        or checked["THREE_SITE_DR_ENABLED"] != "true"
        or checked["DR_EVENT_PROTOCOL_ENABLED"] != "true"
        or checked["DR_EVENT_PROTOCOL_STRICT"] != "true"
        or checked["RELEASE_SHA"] != bound["release_sha"]
        or checked["SERVER_MODE"] != expected_server_mode
        or checked["LOGICAL_AUTHORITY"] != expected_authority
        or checked["PHYSICAL_SITE"] != bound["role"]
        or re.fullmatch(r"[1-9][0-9]*", checked["DR_PRODUCER_EPOCH"]) is None
        or not Path(checked["DR_BLOB_ROOT"]).is_absolute()
        or not checked["DATABASE_URL"].startswith("postgresql+asyncpg://")
        or not checked["SYNC_DATABASE_URL"].startswith("postgresql")
        or not checked["REDIS_URL"].startswith(("redis://", "rediss://"))
        or not checked["FRONTEND_URL"].startswith(("http://", "https://"))
    ):
        raise ConvergenceRoleObserverError("collector runtime config identity differs")
    if document["runtime_target_binding_sha256"] != runtime_target_binding.get(
        "binding_sha256"
    ):
        raise ConvergenceRoleObserverError(
            "collector runtime config binding differs from installed binding"
        )
    try:
        derived = _derive_runtime_target_binding(
            checked,
            role=bound["role"],
            release_sha=bound["release_sha"],
        )
    except Exception as exc:
        raise ConvergenceRoleObserverError(
            "collector runtime config cannot derive its target binding"
        ) from exc
    if any(runtime_target_binding.get(key) != value for key, value in derived.items()):
        raise ConvergenceRoleObserverError(
            "collector runtime config differs from the installed target binding"
        )
    return document


def _collector_environment(
    request: Mapping[str, Any],
    *,
    release_root_descriptor: int,
) -> dict[str, str]:
    """Load only one canonical root-owned config; never inherit caller env."""

    path = _canonical_collector_runtime_config_path(request)
    _assert_private_directory_chain(path.parent, label="collector runtime config directory")
    document = _read_canonical_root_json(
        path,
        label="collector runtime config",
        max_size=MAX_COLLECTOR_RUNTIME_CONFIG_BYTES,
    )
    binding = _load_runtime_target_inputs(request)
    checked = _validate_collector_runtime_config(
        document,
        request=request,
        runtime_target_binding=binding,
    )
    environment = checked["environment"]
    if not isinstance(environment, Mapping):  # Defensive after the typed validator above.
        raise ConvergenceRoleObserverError("collector runtime environment is invalid")
    return {**SAFE_ENV, **{str(name): str(item) for name, item in environment.items()}}


def _assert_release_directory_descriptor(
    descriptor: int,
    *,
    label: str,
    private: bool,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
    unsafe_mode = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & unsafe_mode
    ):
        raise ConvergenceRoleObserverError(f"{label} is not root-controlled")
    return metadata


def _verified_release_file_sha256(descriptor: int, *, label: str) -> str:
    """Hash one already-open no-follow release file and retain its descriptor."""

    try:
        before = os.fstat(descriptor)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != 0
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size < 1
        or before.st_size > MAX_JSON_BYTES
    ):
        raise ConvergenceRoleObserverError(f"{label} is not a stable release file")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            raise ConvergenceRoleObserverError(f"{label} changed while being read")
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} could not be read") from exc
    return digest.hexdigest()


def _run_held_release_git(
    request: Mapping[str, Any],
    *,
    release_root_descriptor: int,
    arguments: list[str],
    label: str,
    max_stdout_bytes: int,
) -> bytes:
    """Run a fixed Git readback rooted at the inherited release directory FD."""

    document = validate_request(request)
    _require_fixed_git_object_command(arguments, release_sha=str(document["release_sha"]))
    _assert_release_directory_descriptor(
        release_root_descriptor,
        label="immutable release root",
        private=True,
    )
    if os.name != "posix" or not Path("/proc/self/fd").is_dir():
        raise ConvergenceRoleObserverError("descriptor-bound Git readback is unavailable")
    try:
        result = subprocess.run(
            [
                GIT,
                *GIT_STRICT_OPTIONS,
                "--no-replace-objects",
                "-C",
                f"/proc/self/fd/{release_root_descriptor}",
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=SAFE_ENV,
            close_fds=True,
            pass_fds=(release_root_descriptor,),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConvergenceRoleObserverError(f"{label} Git readback is unavailable") from exc
    if (
        result.returncode != 0
        or len(result.stdout) > max_stdout_bytes
        or len(result.stderr) > 64 * 1024
    ):
        raise ConvergenceRoleObserverError(f"{label} Git readback is invalid")
    return result.stdout


def _require_fixed_git_object_command(arguments: list[str], *, release_sha: str) -> None:
    """Forbid future callers from turning this trusted helper into a worktree read.

    This helper intentionally permits only commit/tree resolution and direct
    blob-object reads.  In particular it cannot be used for ``status``,
    ``diff``, ``remote``, config inspection, or any transport operation.
    """

    if (
        len(arguments) == 3
        and arguments[:2] == ["rev-parse", "--verify"]
        and arguments[2] in {f"{release_sha}^{{commit}}", f"{release_sha}^{{tree}}"}
    ):
        return
    if (
        len(arguments) == 3
        and arguments[:2] == ["cat-file", "blob"]
        and isinstance(arguments[2], str)
        and arguments[2].startswith(f"{release_sha}:")
    ):
        relative = arguments[2].split(":", maxsplit=1)[1]
        try:
            path = PurePosixPath(relative)
        except TypeError as exc:
            raise ConvergenceRoleObserverError("held release Git object path is invalid") from exc
        if (
            relative
            and not path.is_absolute()
            and path.parts
            and all(part not in {"", ".", ".."} for part in path.parts)
        ):
            return
    raise ConvergenceRoleObserverError("held release Git command is not a fixed object read")


def _held_release_git_text(
    request: Mapping[str, Any],
    *,
    release_root_descriptor: int,
    arguments: list[str],
    label: str,
) -> str:
    payload = _run_held_release_git(
        request,
        release_root_descriptor=release_root_descriptor,
        arguments=arguments,
        label=label,
        max_stdout_bytes=64 * 1024,
    )
    try:
        return payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ConvergenceRoleObserverError(f"{label} Git readback is not ASCII") from exc


def _verify_held_release_git_state(
    request: Mapping[str, Any],
    *,
    release_root_descriptor: int,
) -> None:
    """Verify only fixed Git commit/tree objects before collector startup.

    Worktree-inspection commands are deliberately absent.  They can evaluate
    local attributes and filters from the worktree/configuration, so they
    cannot participate in a release trust proof.  The collector subsequently
    executes project code from matching immutable Git blobs rather than from
    worktree paths.
    """

    document = validate_request(request)
    commit = _held_release_git_text(
        document,
        release_root_descriptor=release_root_descriptor,
        arguments=["rev-parse", "--verify", f"{document['release_sha']}^{{commit}}"],
        label="held exact release commit",
    )
    tree = _held_release_git_text(
        document,
        release_root_descriptor=release_root_descriptor,
        arguments=["rev-parse", "--verify", f"{document['release_sha']}^{{tree}}"],
        label="held exact release tree",
    )
    if commit != document["release_sha"] or tree != document["release_tree_sha"]:
        raise ConvergenceRoleObserverError("held release commit/tree object differs from request")


def _expected_release_file_sha256(
    request: Mapping[str, Any],
    *,
    relative_path: Path,
    label: str,
    release_root_descriptor: int,
) -> str:
    """Hash the file object recorded in the held exact Git release directory."""

    document = validate_request(request)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ConvergenceRoleObserverError(f"{label} relative path is invalid")
    payload = _run_held_release_git(
        document,
        release_root_descriptor=release_root_descriptor,
        arguments=["cat-file", "blob", f"{document['release_sha']}:{relative_path.as_posix()}"],
        label=label,
        max_stdout_bytes=MAX_JSON_BYTES,
    )
    if not payload:
        raise ConvergenceRoleObserverError(f"{label} Git object is invalid")
    return hashlib.sha256(payload).hexdigest()


def _reject_release_dotenv(release_root_descriptor: int) -> None:
    """Pydantic's legacy settings class would otherwise load cwd/.env."""

    try:
        os.stat(".env", dir_fd=release_root_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConvergenceRoleObserverError("verified release .env check failed") from exc
    raise ConvergenceRoleObserverError("verified release must not contain a .env file")


@dataclass(frozen=True)
class _VerifiedCollectorExecution:
    collector_descriptor: int
    release_root_descriptor: int
    collector_proc_path: str
    release_cwd: str


@contextmanager
def _open_verified_runtime_collector(
    request: Mapping[str, Any],
) -> Iterator[_VerifiedCollectorExecution]:
    """Hold the exact collector inode and release cwd until child exec.

    The child receives only inherited descriptors and executes
    ``/proc/self/fd/<collector>``.  It therefore cannot follow a release path
    that is renamed or replaced after verification.
    """

    document = validate_request(request)
    if os.name != "posix" or not Path("/proc/self/fd").is_dir():
        raise ConvergenceRoleObserverError("descriptor-bound collector execution is unavailable")
    _assert_release_directory_chain(
        Path(str(document["release_root"])),
        label="immutable release root",
    )
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    release_root_descriptor = -1
    scripts_descriptor = -1
    collector_descriptor = -1
    try:
        try:
            release_root_descriptor = os.open(
                Path(str(document["release_root"])), directory_flags
            )
            _assert_release_directory_descriptor(
                release_root_descriptor,
                label="immutable release root",
                private=True,
            )
            _verify_held_release_git_state(
                document,
                release_root_descriptor=release_root_descriptor,
            )
            scripts_descriptor = os.open(
                "scripts",
                directory_flags,
                dir_fd=release_root_descriptor,
            )
            _assert_release_directory_descriptor(
                scripts_descriptor,
                label="release scripts directory",
                private=False,
            )
            collector_descriptor = os.open(
                RUNTIME_COLLECTOR_RELATIVE.name,
                file_flags,
                dir_fd=scripts_descriptor,
            )
        except OSError as exc:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector is unavailable"
            ) from exc
        actual_sha256 = _verified_release_file_sha256(
            collector_descriptor,
            label="release-bound runtime collector",
        )
        expected_sha256 = _expected_release_file_sha256(
            document,
            relative_path=RUNTIME_COLLECTOR_RELATIVE,
            label="release-bound runtime collector",
            release_root_descriptor=release_root_descriptor,
        )
        if actual_sha256 != expected_sha256:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector differs from the exact release"
            )
        _reject_release_dotenv(release_root_descriptor)
        yield _VerifiedCollectorExecution(
            collector_descriptor=collector_descriptor,
            release_root_descriptor=release_root_descriptor,
            collector_proc_path=f"/proc/self/fd/{collector_descriptor}",
            release_cwd=f"/proc/self/fd/{release_root_descriptor}",
        )
    finally:
        if collector_descriptor >= 0:
            os.close(collector_descriptor)
        if scripts_descriptor >= 0:
            os.close(scripts_descriptor)
        if release_root_descriptor >= 0:
            os.close(release_root_descriptor)


def _trusted_executable(value: str | Path, *, label: str) -> Path:
    """Resolve one absolute root-owned executable without PATH lookup."""

    candidate = Path(value)
    if not candidate.is_absolute():
        raise ConvergenceRoleObserverError(f"{label} path is invalid")
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConvergenceRoleObserverError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink < 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise ConvergenceRoleObserverError(f"{label} is unsafe")
    return resolved


def _trusted_python_interpreter() -> Path:
    """Resolve the current interpreter once and reject mutable executables."""

    return _trusted_executable(sys.executable, label="isolated collector interpreter")


def _strict_json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise ConvergenceRoleObserverError(f"{label} output size is invalid")
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError(f"{label} output is not strict JSON") from exc
    if not isinstance(document, dict):
        raise ConvergenceRoleObserverError(f"{label} output must be an object")
    return document


def _collector_child_environment(
    request: Mapping[str, Any],
    *,
    release_root_descriptor: int,
    collector_descriptor: int,
) -> dict[str, str]:
    """Bind the child import root to the inherited held release directory FD."""

    if release_root_descriptor < 3 or collector_descriptor < 3:
        raise ConvergenceRoleObserverError("held collector descriptor is invalid")
    environment = _collector_environment(
        request,
        release_root_descriptor=release_root_descriptor,
    )
    environment[COLLECTOR_RELEASE_ROOT_FD_ENV] = str(release_root_descriptor)
    environment[COLLECTOR_FD_ENV] = str(collector_descriptor)
    return environment


async def _read_collector_stream_bounded(
    stream: asyncio.StreamReader | None,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    """Read one child pipe incrementally, never buffering beyond its contract."""

    if stream is None or max_bytes < 1:
        raise ConvergenceRoleObserverError(f"release-bound runtime collector {label} is unavailable")
    payload = bytearray()
    while True:
        remaining = max_bytes + 1 - len(payload)
        if remaining < 1:
            raise _CollectorStreamLimitError(
                f"release-bound runtime collector {label} exceeds its approved size"
            )
        block = await stream.read(min(MAX_COLLECTOR_STREAM_CHUNK_BYTES, remaining))
        if not block:
            return bytes(payload)
        payload.extend(block)
        if len(payload) > max_bytes:
            raise _CollectorStreamLimitError(
                f"release-bound runtime collector {label} exceeds its approved size"
            )


async def _terminate_and_reap_collector(
    process: Any,
    *,
    tasks: tuple[asyncio.Task[Any], ...],
    containment: _CollectorContainmentBoundary | None = None,
) -> None:
    """Kill the collector session and bound every cleanup wait.

    The collector is always started in a new session.  Killing only the direct
    Python process can leave a helper/grandchild holding its pipes or release
    descriptors indefinitely, so a valid PID is treated as its process-group
    leader.  Mock/test process objects without a PID retain the direct-kill
    fallback, but production cleanup never silently waits without a bound.
    """

    cleanup_error: Exception | None = None
    pid = getattr(process, "pid", None)
    try:
        if (
            containment is not None
            and containment.direct_pid == pid
            and containment.direct_pidfd is not None
        ):
            # The direct collector is still identified by a live pidfd, so it
            # cannot be confused with a recycled PID.  Kill the original
            # session while its leader is still present; a descendant that
            # called setsid is handled by the subreaper drain below.
            if getattr(process, "returncode", None) is None:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                signal.pidfd_send_signal(containment.direct_pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif type(pid) is int and pid > 1:
            # Even after the direct child exits, a helper may still own the
            # session pipes.  A group kill is therefore required on every
            # cleanup path, not only while ``returncode`` is unset.
            os.killpg(pid, signal.SIGKILL)
        elif getattr(process, "returncode", None) is None:
            process.kill()
    except ProcessLookupError:
        # A concurrently exited process group cannot leave a live member.
        pass
    except (AttributeError, OSError, subprocess.SubprocessError) as exc:
        cleanup_error = exc
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=COLLECTOR_REAP_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        cleanup_error = exc
    try:
        await asyncio.wait_for(
            asyncio.shield(process.wait()),
            timeout=COLLECTOR_REAP_TIMEOUT_SECONDS,
        )
    except (AttributeError, OSError, TypeError, subprocess.SubprocessError, TimeoutError) as exc:
        cleanup_error = exc
    if cleanup_error is not None:
        raise _CollectorCleanupError(
            "release-bound runtime collector cleanup could not prove zero live residue"
        ) from cleanup_error


async def _capture_collector_output_bounded(
    process: Any,
    *,
    containment: _CollectorContainmentBoundary | None = None,
) -> tuple[bytes, bytes, int]:
    """Collect child output with bounded readers and deterministic cleanup."""

    try:
        wait_task = asyncio.create_task(process.wait())
    except (AttributeError, TypeError) as exc:
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector wait handle is unavailable"
        ) from exc
    stdout_task = asyncio.create_task(
        _read_collector_stream_bounded(
            getattr(process, "stdout", None),
            label="stdout",
            max_bytes=MAX_JSON_BYTES,
        )
    )
    stderr_task = asyncio.create_task(
        _read_collector_stream_bounded(
            getattr(process, "stderr", None),
            label="stderr",
            max_bytes=MAX_COLLECTOR_STDERR_BYTES,
        )
    )
    tasks = (stdout_task, stderr_task, wait_task)
    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=COLLECTOR_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        try:
            await _terminate_and_reap_collector(
                process,
                tasks=tasks,
                containment=containment,
            )
        except _CollectorCleanupError as cleanup_exc:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector timed out with unbounded residue"
            ) from cleanup_exc
        raise ConvergenceRoleObserverError("release-bound runtime collector timed out") from exc
    except asyncio.CancelledError:
        try:
            await _terminate_and_reap_collector(
                process,
                tasks=tasks,
                containment=containment,
            )
        except _CollectorCleanupError as cleanup_exc:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector cancellation left unbounded residue"
            ) from cleanup_exc
        raise
    except _CollectorStreamLimitError as exc:
        try:
            await _terminate_and_reap_collector(
                process,
                tasks=tasks,
                containment=containment,
            )
        except _CollectorCleanupError as cleanup_exc:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector exceeded a bound with live residue"
            ) from cleanup_exc
        raise ConvergenceRoleObserverError("release-bound runtime collector was rejected") from exc
    except Exception as exc:
        try:
            await _terminate_and_reap_collector(
                process,
                tasks=tasks,
                containment=containment,
            )
        except _CollectorCleanupError as cleanup_exc:
            raise ConvergenceRoleObserverError(
                "release-bound runtime collector failed with unbounded residue"
            ) from cleanup_exc
        raise ConvergenceRoleObserverError("release-bound runtime collector was rejected") from exc
    if type(returncode) is not int:
        raise ConvergenceRoleObserverError("release-bound runtime collector return code is invalid")
    return stdout, stderr, returncode


async def _collect_runtime_snapshot_from_verified_release(
    request: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Run the fixed collector in a new isolated Python interpreter.

    ``-I -S`` discards environment/user site state and disables automatic site
    processing before the collector starts.  The collector receives held root
    and script FDs, validates their exact Git blobs, then adds only its fixed
    root-controlled dependency directories and the held release import root.
    This prevents a same-named controller module, site hook, replaced release
    pathname, or ambient module cache from becoming production evidence.
    """

    interpreter = _trusted_python_interpreter()
    boundary = _open_collector_containment_boundary()
    result: tuple[bytes, bytes, int] | None = None
    primary_error: BaseException | None = None
    try:
        with _open_verified_runtime_collector(request) as execution:
            argv = [
                os.fspath(interpreter),
                "-I",
                "-S",
                execution.collector_proc_path,
                "--campaign-id",
                str(request["campaign_id"]),
                "--release-sha",
                str(request["release_sha"]),
                "--plan-sha256",
                str(request["plan_sha256"]),
                "--max-rows-per-table",
                str(request["max_rows_per_table"]),
            ]
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=execution.release_cwd,
                    close_fds=True,
                    pass_fds=(
                        execution.collector_descriptor,
                        execution.release_root_descriptor,
                    ),
                    env=_collector_child_environment(
                        request,
                        release_root_descriptor=execution.release_root_descriptor,
                        collector_descriptor=execution.collector_descriptor,
                    ),
                    start_new_session=True,
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise ConvergenceRoleObserverError(
                    "release-bound runtime collector could not start"
                ) from exc
            _register_collector_pidfd(boundary, process)
            result = await _capture_collector_output_bounded(
                process,
                containment=boundary,
            )
    except BaseException as exc:
        primary_error = exc

    cleanup_error: BaseException | None = None
    residue_detected = False
    zero_residue_proven = False
    try:
        residue_detected = await _drain_collector_child_residue(boundary)
        zero_residue_proven = True
    except BaseException as exc:
        cleanup_error = exc
    try:
        _close_collector_containment_boundary(
            boundary,
            restore_subreaper=zero_residue_proven,
        )
    except BaseException as exc:
        cleanup_error = cleanup_error or exc

    if cleanup_error is not None:
        if (
            not zero_residue_proven
            or _collector_containment_remains_enabled(boundary)
        ):
            _fail_stop_after_unproven_collector_cleanup()
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector cleanup could not prove zero live residue"
        ) from cleanup_error
    if primary_error is not None:
        raise primary_error.with_traceback(primary_error.__traceback__)
    if residue_detected:
        raise ConvergenceRoleObserverError(
            "release-bound runtime collector left detached descendant residue"
        )
    if result is None:
        raise ConvergenceRoleObserverError("release-bound runtime collector result is unavailable")
    stdout, stderr, returncode = result
    if (
        returncode != 0
        or len(stdout) > MAX_JSON_BYTES
        or len(stderr) > MAX_COLLECTOR_STDERR_BYTES
    ):
        raise ConvergenceRoleObserverError("release-bound runtime collector was rejected")
    return _strict_json_object(stdout, label="release-bound runtime collector")


def _parity_payload_sha256(value: Any) -> str:
    """The release-bound worker's local equivalent of the parity fingerprint.

    The previous implementation imported a staging evidence module and
    ``core.sync_parity`` after release verification.  Either import could
    reuse a preloaded module from outside the verified release.  This compact
    semantic validator is part of this hash-bound worker instead; it receives
    only JSON returned by the isolated collector.
    """

    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConvergenceRoleObserverError("runtime parity payload is not canonical") from exc
    return hashlib.sha256(payload).hexdigest()


def _business_snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """Validate and fingerprint a redacted deep parity snapshot locally."""

    tables = snapshot.get("tables") if isinstance(snapshot, Mapping) else None
    if not isinstance(tables, Mapping) or not tables:
        raise ConvergenceRoleObserverError("runtime parity snapshot has no tables")
    normalized: list[dict[str, Any]] = []
    for table_name in sorted(str(name) for name in tables):
        table = tables.get(table_name)
        if not isinstance(table, Mapping) or bool(table.get("truncated")):
            raise ConvergenceRoleObserverError("runtime parity table is incomplete")
        records = table.get("records")
        if not isinstance(records, list):
            raise ConvergenceRoleObserverError("runtime parity records are invalid")
        if type(table.get("row_count")) is not int or table["row_count"] != len(records):
            raise ConvergenceRoleObserverError("runtime parity table row count is invalid")
        if type(table.get("duplicate_identity_count")) is not int or table["duplicate_identity_count"] != 0:
            raise ConvergenceRoleObserverError("runtime parity has duplicate identities")
        entries: list[dict[str, str]] = []
        full_entries: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ConvergenceRoleObserverError("runtime parity record is invalid")
            identity_hash = _validate_hash(record.get("identity_hash"), label="parity identity")
            business_hash = _validate_hash(record.get("business_hash"), label="parity business")
            local_only_hash = _validate_hash(record.get("local_only_hash"), label="parity local-only")
            volatile_hash = _validate_hash(record.get("volatile_hash"), label="parity volatile")
            entries.append({"identity_hash": identity_hash, "business_hash": business_hash})
            full_entries.append(
                {
                    "identity_hash": identity_hash,
                    "business_hash": business_hash,
                    "local_only_hash": local_only_hash,
                    "volatile_hash": volatile_hash,
                }
            )
        entries.sort(key=lambda item: item["identity_hash"])
        if len({item["identity_hash"] for item in entries}) != len(entries):
            raise ConvergenceRoleObserverError("runtime parity has duplicate identities")
        if (
            table.get("records_hash") != _parity_payload_sha256(full_entries)
            or table.get("business_records_hash") != _parity_payload_sha256(entries)
        ):
            raise ConvergenceRoleObserverError("runtime parity record fingerprints differ")
        normalized.append({"table": table_name, "records": entries})
    return _parity_payload_sha256(normalized)


def _runtime_hash(value: Any, *, label: str, zero_allowed: bool = False) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ConvergenceRoleObserverError(f"{label} hash is invalid")
    if not zero_allowed and value == ZERO_SHA256:
        raise ConvergenceRoleObserverError(f"{label} hash must not be zero")
    return value


def _nonnegative(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ConvergenceRoleObserverError(f"{label} must be a non-negative integer")
    return value


def _validate_release_bound_runtime_snapshot(
    raw: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the isolated collector's full JSON without legacy imports."""

    if not isinstance(raw, Mapping):
        raise ConvergenceRoleObserverError("runtime snapshot is not an object")
    role = str(request["role"])
    expected_fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "site", "observed_at",
        "producer_epoch", "source_streams", "destination_streams",
        "unresolved_conflict_count", "database_snapshot", "blob_records",
    }
    if (
        set(raw) != expected_fields
        or raw.get("schema") != "three-site-staging-convergence-site-snapshot-v1"
        or raw.get("campaign_id") != request["campaign_id"]
        or raw.get("release_sha") != request["release_sha"]
        or raw.get("plan_sha256") != request["plan_sha256"]
        or raw.get("site") != role
        or type(raw.get("producer_epoch")) is not int
        or raw["producer_epoch"] < 1
        or not isinstance(raw.get("source_streams"), list)
        or not isinstance(raw.get("destination_streams"), list)
        or not isinstance(raw.get("database_snapshot"), Mapping)
        or not isinstance(raw.get("blob_records"), list)
    ):
        raise ConvergenceRoleObserverError("runtime snapshot identity or schema differs")
    captured_at = _timestamp(raw["observed_at"], label="runtime snapshot capture time")
    _nonnegative(raw["unresolved_conflict_count"], label="runtime conflict count")
    expected_peers = set(RUNTIME_SNAPSHOT_ROLES) - {role}
    source_streams: dict[str, dict[str, Any]] = {}
    for stream in raw["source_streams"]:
        if not isinstance(stream, Mapping) or set(stream) != {
            "destination_site", "source_sequence", "source_transaction_hash"
        }:
            raise ConvergenceRoleObserverError("runtime source stream fields differ")
        destination = stream.get("destination_site")
        sequence = _nonnegative(stream.get("source_sequence"), label="runtime source sequence")
        transaction = _runtime_hash(
            stream.get("source_transaction_hash"),
            label="runtime source transaction",
            zero_allowed=True,
        )
        if (
            destination not in expected_peers
            or destination in source_streams
            or (sequence == 0) != (transaction == ZERO_SHA256)
        ):
            raise ConvergenceRoleObserverError("runtime source stream does not prove a canonical tail")
        source_streams[str(destination)] = {
            "source_sequence": sequence,
            "source_transaction_hash": transaction,
        }
    if set(source_streams) != expected_peers:
        raise ConvergenceRoleObserverError("runtime source stream coverage differs")
    destination_streams: dict[tuple[str, int], dict[str, Any]] = {}
    for stream in raw["destination_streams"]:
        if not isinstance(stream, Mapping) or set(stream) != {
            "origin_site", "producer_epoch", "received_sequence", "applied_sequence",
            "received_transaction_hash", "applied_transaction_hash",
        }:
            raise ConvergenceRoleObserverError("runtime destination stream fields differ")
        origin = stream.get("origin_site")
        epoch = stream.get("producer_epoch")
        received = _nonnegative(stream.get("received_sequence"), label="runtime received sequence")
        applied = _nonnegative(stream.get("applied_sequence"), label="runtime applied sequence")
        received_hash = _runtime_hash(
            stream.get("received_transaction_hash"),
            label="runtime received transaction",
            zero_allowed=True,
        )
        applied_hash = _runtime_hash(
            stream.get("applied_transaction_hash"),
            label="runtime applied transaction",
            zero_allowed=True,
        )
        key = (origin, epoch)
        if (
            origin not in expected_peers
            or type(epoch) is not int
            or epoch < 1
            or key in destination_streams
            or applied > received
            or (received == 0) != (received_hash == ZERO_SHA256)
            or (applied == 0) != (applied_hash == ZERO_SHA256)
        ):
            raise ConvergenceRoleObserverError("runtime destination stream is inconsistent")
        destination_streams[(str(origin), epoch)] = {
            "received_sequence": received,
            "applied_sequence": applied,
            "received_transaction_hash": received_hash,
            "applied_transaction_hash": applied_hash,
        }
    database = raw["database_snapshot"]
    if database.get("mode") != "deep" or not isinstance(database.get("tables"), Mapping):
        raise ConvergenceRoleObserverError("runtime database snapshot is not deep")
    _business_snapshot_fingerprint(database)
    blob_records: dict[str, dict[str, Any]] = {}
    for record in raw["blob_records"]:
        fields = {
            "content_hash", "size_bytes", "object_version_id", "object_ciphertext_hash",
            "object_ciphertext_size", "encryption_key_id", "encryption_algorithm",
            "local_content_hash", "local_size_bytes",
        }
        if not isinstance(record, Mapping) or set(record) != fields:
            raise ConvergenceRoleObserverError("runtime blob record fields differ")
        content_hash = _runtime_hash(record.get("content_hash"), label="runtime blob content")
        ciphertext_hash = _runtime_hash(
            record.get("object_ciphertext_hash"), label="runtime blob ciphertext"
        )
        del ciphertext_hash
        if (
            content_hash in blob_records
            or _nonnegative(record.get("size_bytes"), label="runtime blob size")
            != _nonnegative(record.get("local_size_bytes"), label="runtime blob local size")
            or _runtime_hash(record.get("local_content_hash"), label="runtime blob local content")
            != content_hash
            or _nonnegative(record.get("object_ciphertext_size"), label="runtime blob ciphertext size") < 1
            or not isinstance(record.get("object_version_id"), str)
            or not record["object_version_id"].strip()
            or not isinstance(record.get("encryption_key_id"), str)
            or not record["encryption_key_id"].strip()
            or not isinstance(record.get("encryption_algorithm"), str)
            or not record["encryption_algorithm"].strip()
        ):
            raise ConvergenceRoleObserverError("runtime blob record failed local read-back validation")
        blob_records[content_hash] = dict(record)
    if role == "bot_fi" and blob_records:
        raise ConvergenceRoleObserverError("Bot-FI must not claim a WebApp blob replica")
    return {
        "captured_at": captured_at,
        "producer_epoch": int(raw["producer_epoch"]),
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": int(raw["unresolved_conflict_count"]),
        "database_snapshot": database,
        "blob_records": blob_records,
    }


def _validate_hash(value: Any, *, label: str, zero_allowed: bool = False) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ConvergenceRoleObserverError(f"{label} hash is invalid")
    if not zero_allowed and value == ZERO_SHA256:
        raise ConvergenceRoleObserverError(f"{label} hash must not be zero")
    return value


def _summarize_runtime_snapshot(
    raw: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce a local runtime read to non-secret data needed by the controller."""

    # ``_validate_release_bound_runtime_snapshot`` is intentionally defined in
    # this verified worker.  Do not reintroduce an import of the old staging
    # validator or ``core.sync_parity`` here: a preloaded same-name module
    # would otherwise be outside the exact release that just passed Git
    # verification.
    validated = _validate_release_bound_runtime_snapshot(raw, request=request)
    captured_at = validated["captured_at"]
    if not isinstance(captured_at, datetime):
        raise ConvergenceRoleObserverError("runtime snapshot capture time is invalid")
    database = validated["database_snapshot"]
    tables = database.get("tables") if isinstance(database, Mapping) else None
    if not isinstance(tables, Mapping) or not tables:
        raise ConvergenceRoleObserverError("runtime database summary is unavailable")
    table_names = sorted(str(name) for name in tables)
    row_count = 0
    for name in table_names:
        table = tables.get(name)
        if not isinstance(table, Mapping) or type(table.get("row_count")) is not int:
            raise ConvergenceRoleObserverError("runtime database table summary is invalid")
        row_count += int(table["row_count"])
    redacted_tables: dict[str, Any] = {}
    for table_name in table_names:
        table = tables[table_name]
        if not isinstance(table, Mapping):
            raise ConvergenceRoleObserverError("runtime database table is invalid")
        if (
            table.get("table") != table_name
            or table.get("duplicate_identity_hashes") != []
        ):
            raise ConvergenceRoleObserverError("runtime database table identity differs")
        records = table.get("records")
        if not isinstance(records, list):
            raise ConvergenceRoleObserverError("runtime database records are invalid")
        redacted_records: list[dict[str, str]] = []
        for record in records:
            if not isinstance(record, Mapping) or set(record) - {
                "identity_hash", "identity_fields", "business_hash", "local_only_hash",
                "volatile_hash", "identity_label",
            }:
                raise ConvergenceRoleObserverError("runtime parity record has unexpected fields")
            redacted = {
                field: _validate_hash(record.get(field), label=f"parity {field}")
                for field in ("identity_hash", "business_hash", "local_only_hash", "volatile_hash")
            }
            redacted_records.append(redacted)
        redacted_table = {
            "table": table_name,
            "row_count": int(table["row_count"]),
            "truncated": bool(table.get("truncated")),
            "duplicate_identity_count": int(table.get("duplicate_identity_count") or 0),
            "duplicate_identity_hashes": list(table.get("duplicate_identity_hashes") or []),
            "records_hash": _validate_hash(table.get("records_hash"), label="parity records"),
            "business_records_hash": _validate_hash(
                table.get("business_records_hash"),
                label="parity business records",
            ),
            "records": redacted_records,
        }
        redacted_tables[table_name] = redacted_table
    redacted_parity_snapshot = {
        "status": "ok",
        "schema_version": int(database.get("schema_version") or 0),
        "mode": "deep",
        "table_count": len(redacted_tables),
        "max_rows_per_table": int(database.get("max_rows_per_table") or 0),
        "tables": redacted_tables,
    }
    if (
        redacted_parity_snapshot["schema_version"] < 1
        or redacted_parity_snapshot["max_rows_per_table"] < 1
    ):
        raise ConvergenceRoleObserverError("runtime parity snapshot version or limit is invalid")
    database_summary = {
        "table_set_sha256": _sha256(table_names),
        "business_fingerprint_sha256": _business_snapshot_fingerprint(database),
        "row_count": row_count,
        "table_count": len(table_names),
        "redacted_snapshot_sha256": _sha256(redacted_parity_snapshot),
    }
    database_summary["database_state_sha256"] = _sha256(database_summary)
    source_streams = []
    for destination, stream in sorted(validated["source_streams"].items()):
        source_streams.append(
            {
                "destination_site": destination,
                "source_sequence": int(stream["source_sequence"]),
                "source_transaction_hash": _validate_hash(
                    stream["source_transaction_hash"],
                    label="source transaction",
                    zero_allowed=True,
                ),
            }
        )
    destination_streams = []
    for (origin, epoch), stream in sorted(validated["destination_streams"].items()):
        destination_streams.append(
            {
                "origin_site": origin,
                "producer_epoch": int(epoch),
                "received_sequence": int(stream["received_sequence"]),
                "applied_sequence": int(stream["applied_sequence"]),
                "received_transaction_hash": _validate_hash(
                    stream["received_transaction_hash"],
                    label="received transaction",
                    zero_allowed=True,
                ),
                "applied_transaction_hash": _validate_hash(
                    stream["applied_transaction_hash"],
                    label="applied transaction",
                    zero_allowed=True,
                ),
            }
        )
    dr_summary = {
        "producer_epoch": int(validated["producer_epoch"]),
        "source_streams": source_streams,
        "destination_streams": destination_streams,
        "unresolved_conflict_count": int(validated["unresolved_conflict_count"]),
    }
    dr_summary["dr_state_sha256"] = _sha256(dr_summary)
    return {
        "captured_at": _timestamp_text(captured_at),
        "database": database_summary,
        "redacted_parity_snapshot": redacted_parity_snapshot,
        "dr": dr_summary,
    }


def _attestation_digest(document: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "attestation_sha256"}
    return _sha256(unsigned)


def _host_identity_proof_digest(document: Mapping[str, Any]) -> str:
    return _sha256(
        {
            key: value
            for key, value in document.items()
            if key != "host_identity_proof_sha256"
        }
    )


def validate_host_identity_proof(
    value: Any,
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a local kernel-address proof bound to one observer request."""

    bound = validate_request(request, now=now)
    if (
        not isinstance(value, Mapping)
        or set(value) != HOST_IDENTITY_PROOF_FIELDS
        or value.get("schema") != HOST_IDENTITY_PROOF_SCHEMA
        or value.get("address_family") != "inet"
        or value.get("collector") != "kernel-ip-json"
    ):
        raise ConvergenceRoleObserverError("local host identity proof fields differ")
    document = dict(value)
    expected_host = _ipv4_text(document.get("expected_host"), label="proof expected host")
    observed_host = _ipv4_text(document.get("observed_host"), label="proof observed host")
    if expected_host != bound["expected_host"] or observed_host != expected_host:
        raise ConvergenceRoleObserverError("local host identity proof does not bind the expected host")
    interface = document.get("interface")
    if (
        not isinstance(interface, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", interface) is None
    ):
        raise ConvergenceRoleObserverError("local host identity proof interface is invalid")
    observed_at = _timestamp(document.get("observed_at"), label="host identity proof time")
    phase_started_at = _timestamp(bound["phase_started_at"], label="phase_started_at")
    current = (now or _utcnow()).astimezone(timezone.utc)
    if observed_at < phase_started_at:
        raise ConvergenceRoleObserverError("local host identity proof predates phase start")
    if observed_at > current + MAX_OBSERVATION_FUTURE_SKEW:
        raise ConvergenceRoleObserverError("local host identity proof is future dated")
    if current - observed_at > MAX_OBSERVATION_AGE:
        raise ConvergenceRoleObserverError("local host identity proof is stale")
    if document.get("host_identity_proof_sha256") != _host_identity_proof_digest(document):
        raise ConvergenceRoleObserverError("local host identity proof digest differs")
    return document


def _collect_local_host_identity_proof(
    request: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, Any]:
    """Read the kernel's local IPv4 addresses without contacting a network."""

    expected_host = _ipv4_text(request.get("expected_host"), label="observer expected host")
    ip_binary = _trusted_executable(IP, label="local host identity probe")
    try:
        result = runner(
            [os.fspath(ip_binary), "-j", "-4", "addr", "show"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConvergenceRoleObserverError("local host identity probe failed") from exc
    if (
        result.returncode != 0
        or len(result.stdout) > 64 * 1024
        or len(result.stderr) > 64 * 1024
    ):
        raise ConvergenceRoleObserverError("local host identity probe was rejected")
    try:
        interfaces = json.loads(result.stdout.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("local host identity probe is not strict JSON") from exc
    if not isinstance(interfaces, list):
        raise ConvergenceRoleObserverError("local host identity probe output is invalid")
    matches: set[str] = set()
    for interface in interfaces:
        if not isinstance(interface, Mapping):
            raise ConvergenceRoleObserverError("local host identity interface is invalid")
        name = interface.get("ifname")
        addresses = interface.get("addr_info")
        if not isinstance(name, str) or not isinstance(addresses, list):
            raise ConvergenceRoleObserverError("local host identity interface fields are invalid")
        for address in addresses:
            if not isinstance(address, Mapping):
                raise ConvergenceRoleObserverError("local host identity address is invalid")
            if address.get("family") == "inet" and address.get("local") == expected_host:
                matches.add(name)
    if len(matches) != 1:
        raise ConvergenceRoleObserverError(
            "expected host IPv4 address is not uniquely assigned to this local host"
        )
    observed_at = _utcnow()
    proof: dict[str, Any] = {
        "schema": HOST_IDENTITY_PROOF_SCHEMA,
        "expected_host": expected_host,
        "observed_host": expected_host,
        "address_family": "inet",
        "interface": next(iter(matches)),
        "collector": "kernel-ip-json",
        "observed_at": _timestamp_text(observed_at),
        "host_identity_proof_sha256": ZERO_SHA256,
    }
    proof["host_identity_proof_sha256"] = _host_identity_proof_digest(proof)
    return validate_host_identity_proof(proof, request=request, now=observed_at)


async def _observe_with_private_test_seams(
    request: Mapping[str, Any],
    *,
    runtime_snapshot_collector: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None,
    host_identity_proof_collector: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    executing_worker_path: Path | None,
) -> dict[str, Any]:
    """Shared implementation; injection exists only for module-private tests."""

    document = validate_request(request, now=_utcnow())
    if os.geteuid() != 0:
        raise ConvergenceRoleObserverError("role observation must run as root")
    release_identity = verify_exact_release(
        document,
        runner=runner,
        executing_worker_path=executing_worker_path,
    )
    # This proof is intentionally collected before any runtime collector.  A
    # role running on a different host cannot emit database/DR output first
    # and attach a host identity afterward.
    host_identity_proof = (
        host_identity_proof_collector(document)
        if host_identity_proof_collector is not None
        else _collect_local_host_identity_proof(document, runner=runner)
    )
    validate_host_identity_proof(host_identity_proof, request=document, now=_utcnow())
    role = str(document["role"])
    if role in RUNTIME_SNAPSHOT_ROLES:
        if runtime_snapshot_collector is None:
            raw_snapshot, compose_execution = _execute_compose_runtime_observer(
                document,
                runner=runner,
            )
        else:
            raw_snapshot = await runtime_snapshot_collector(document)
            compose_execution = _private_test_compose_execution_proof(document)
        runtime_snapshot: dict[str, Any] | None = _summarize_runtime_snapshot(
            raw_snapshot,
            request=document,
        )
        compose_execution = _validate_compose_execution_proof(
            compose_execution,
            request=document,
        )
        available = ["database_parity", "dr_convergence"]
    else:
        runtime_snapshot = None
        compose_execution = None
        available = []
    unavailable = {
        label: reason
        for label, reason in UNAVAILABLE_REASONS.items()
        if label not in available
    }
    observed_at = _utcnow()
    phase_started_at = _timestamp(document["phase_started_at"], label="phase_started_at")
    if observed_at < phase_started_at:
        raise ConvergenceRoleObserverError("role observation predates phase start")
    attestation: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "status": "observed",
        "campaign_id": document["campaign_id"],
        "operation_id": document["operation_id"],
        "release_sha": document["release_sha"],
        "release_tree_sha": document["release_tree_sha"],
        "manifest_sha256": document["manifest_sha256"],
        "runtime_target_binding_sha256": document["runtime_target_binding_sha256"],
        "plan_sha256": document["plan_sha256"],
        "approval_sha256": document["approval_sha256"],
        "phase": PHASE,
        "operation": OPERATION,
        "role": role,
        "expected_host": document["expected_host"],
        "phase_started_at": document["phase_started_at"],
        "request_sha256": document["request_sha256"],
        "worker_sha256": document["worker_sha256"],
        "host_identity_proof": host_identity_proof,
        "observed_at": _timestamp_text(observed_at),
        "release_identity": release_identity,
        "runtime_snapshot": runtime_snapshot,
        "compose_execution": compose_execution,
        "available_observations": available,
        "unavailable_observations": unavailable,
        "redaction": {
            "contains_credentials": False,
            "contains_raw_database_values": False,
            "contains_file_paths": False,
            "contains_object_keys": False,
            "contains_presigned_urls": False,
        },
        "production_mutated": False,
        "worker_transport_contacted": False,
        "object_storage_contacted": False,
        "attestation_sha256": ZERO_SHA256,
    }
    attestation["attestation_sha256"] = _attestation_digest(attestation)
    validate_attestation(attestation, request=document, now=observed_at)
    return attestation


async def observe(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Collect one role-local observation after explicit runtime authorization.

    Production callers cannot provide a collector, command runner, or worker
    path.  The only collector path is the fixed, isolated script below the
    exact release verified above.
    """

    _require_isolated_observer_execution()
    contract = _require_root_only_launcher_contract(request)
    _close_unexpected_worker_descriptors(contract)
    return await _observe_with_private_test_seams(
        request,
        runtime_snapshot_collector=None,
        host_identity_proof_collector=None,
        runner=subprocess.run,
        executing_worker_path=None,
    )


async def _observe_for_test(
    request: Mapping[str, Any],
    *,
    snapshot_collector: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]] | None = None,
    host_identity_proof_collector: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    executing_worker_path: Path | None = None,
) -> dict[str, Any]:
    """Private test seam; production CLI and public API never call this."""

    return await _observe_with_private_test_seams(
        request,
        runtime_snapshot_collector=snapshot_collector,
        host_identity_proof_collector=host_identity_proof_collector,
        runner=runner,
        executing_worker_path=executing_worker_path,
    )


def validate_attestation(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the redacted worker output against its exact request."""

    bound = validate_request(request, now=now)
    if (
        not isinstance(value, Mapping)
        or set(value) != ATTESTATION_FIELDS
        or value.get("schema") != ATTESTATION_SCHEMA
        or value.get("status") != "observed"
    ):
        raise ConvergenceRoleObserverError("role attestation fields differ")
    document = dict(value)
    for field in (
        "campaign_id", "operation_id", "release_sha", "release_tree_sha",
        "manifest_sha256", "runtime_target_binding_sha256", "plan_sha256", "approval_sha256", "phase",
        "operation", "role", "expected_host", "phase_started_at", "request_sha256",
        "worker_sha256",
    ):
        if document.get(field) != bound.get(field):
            raise ConvergenceRoleObserverError(f"role attestation {field} differs from request")
    observed_at = _timestamp(document["observed_at"], label="role observation time")
    if observed_at < _timestamp(bound["phase_started_at"], label="phase_started_at"):
        raise ConvergenceRoleObserverError("role attestation predates phase start")
    current = (now or _utcnow()).astimezone(timezone.utc)
    if observed_at > current + MAX_OBSERVATION_FUTURE_SKEW:
        raise ConvergenceRoleObserverError("role attestation is future dated")
    if current - observed_at > MAX_OBSERVATION_AGE:
        raise ConvergenceRoleObserverError("role attestation is stale")
    host_identity_proof = validate_host_identity_proof(
        document.get("host_identity_proof"),
        request=bound,
        now=current,
    )
    host_proof_at = _timestamp(
        host_identity_proof["observed_at"],
        label="host identity proof time",
    )
    if (
        host_proof_at > observed_at
        or observed_at - host_proof_at > MAX_HOST_PROOF_TO_ATTESTATION_SKEW
    ):
        raise ConvergenceRoleObserverError("host identity proof-to-attestation skew is invalid")
    if document.get("production_mutated") is not False or document.get("worker_transport_contacted") is not False or document.get("object_storage_contacted") is not False:
        raise ConvergenceRoleObserverError("role attestation reports an out-of-scope action")
    if document.get("redaction") != {
        "contains_credentials": False,
        "contains_raw_database_values": False,
        "contains_file_paths": False,
        "contains_object_keys": False,
        "contains_presigned_urls": False,
    }:
        raise ConvergenceRoleObserverError("role attestation redaction declaration differs")
    release_identity = document.get("release_identity")
    if not isinstance(release_identity, Mapping) or set(release_identity) != {
        "release_root_sha256", "head", "tree", "source_tree_bound", "worker_sha256"
    }:
        raise ConvergenceRoleObserverError("role release identity fields differ")
    if (
        release_identity.get("head") != bound["release_sha"]
        or release_identity.get("tree") != bound["release_tree_sha"]
        or release_identity.get("source_tree_bound") is not True
        or release_identity.get("worker_sha256") != bound["worker_sha256"]
    ):
        raise ConvergenceRoleObserverError("role release identity differs")
    _nonzero_sha256(release_identity.get("release_root_sha256"), label="release root")
    available = document.get("available_observations")
    if not isinstance(available, list) or any(not isinstance(item, str) for item in available):
        raise ConvergenceRoleObserverError("role availability list is invalid")
    expected_available = ["database_parity", "dr_convergence"] if bound["role"] in RUNTIME_SNAPSHOT_ROLES else []
    if available != expected_available:
        raise ConvergenceRoleObserverError("role availability differs from implemented collectors")
    unavailable = document.get("unavailable_observations")
    if not isinstance(unavailable, Mapping) or set(unavailable) != set(UNAVAILABLE_REASONS):
        raise ConvergenceRoleObserverError("role unavailable observation set differs")
    if any(unavailable.get(label) != reason for label, reason in UNAVAILABLE_REASONS.items() if label not in available):
        raise ConvergenceRoleObserverError("role unavailable observation reason differs")
    snapshot = document.get("runtime_snapshot")
    if bound["role"] in RUNTIME_SNAPSHOT_ROLES:
        _validate_compose_execution_proof(
            document.get("compose_execution"),
            request=bound,
        )
        if not isinstance(snapshot, Mapping) or set(snapshot) != {
            "captured_at", "database", "redacted_parity_snapshot", "dr"
        }:
            raise ConvergenceRoleObserverError("runtime observation summary fields differ")
        captured_at = _timestamp(snapshot["captured_at"], label="runtime capture time")
        if captured_at < _timestamp(bound["phase_started_at"], label="phase_started_at"):
            raise ConvergenceRoleObserverError("runtime observation predates phase start")
        if captured_at > current + MAX_OBSERVATION_FUTURE_SKEW:
            raise ConvergenceRoleObserverError("runtime observation is future dated")
        if current - captured_at > MAX_OBSERVATION_AGE:
            raise ConvergenceRoleObserverError("runtime observation is stale")
        if observed_at < captured_at or observed_at - captured_at > MAX_CAPTURE_TO_ATTESTATION_SKEW:
            raise ConvergenceRoleObserverError("runtime capture-to-attestation skew is invalid")
        database = snapshot["database"]
        if not isinstance(database, Mapping) or set(database) != {
            "table_set_sha256", "business_fingerprint_sha256", "row_count", "table_count",
            "redacted_snapshot_sha256", "database_state_sha256"
        }:
            raise ConvergenceRoleObserverError("database runtime summary fields differ")
        for field in (
            "table_set_sha256", "business_fingerprint_sha256", "redacted_snapshot_sha256",
            "database_state_sha256",
        ):
            _nonzero_sha256(database.get(field), label=f"database {field}")
        if (
            type(database.get("row_count")) is not int
            or database["row_count"] < 0
            or type(database.get("table_count")) is not int
            or database["table_count"] < 1
        ):
            raise ConvergenceRoleObserverError("database runtime summary values differ")
        expected_database = {key: value for key, value in database.items() if key != "database_state_sha256"}
        if database["database_state_sha256"] != _sha256(expected_database):
            raise ConvergenceRoleObserverError("database runtime summary digest differs")
        redacted_parity_snapshot = snapshot["redacted_parity_snapshot"]
        if (
            not isinstance(redacted_parity_snapshot, Mapping)
            or database["redacted_snapshot_sha256"] != _sha256(redacted_parity_snapshot)
        ):
            raise ConvergenceRoleObserverError("redacted parity snapshot binding differs")
        dr = snapshot["dr"]
        if not isinstance(dr, Mapping) or set(dr) != {
            "producer_epoch", "source_streams", "destination_streams", "unresolved_conflict_count", "dr_state_sha256"
        }:
            raise ConvergenceRoleObserverError("DR runtime summary fields differ")
        if type(dr.get("producer_epoch")) is not int or dr["producer_epoch"] < 1:
            raise ConvergenceRoleObserverError("DR producer epoch is invalid")
        if not isinstance(dr.get("source_streams"), list) or not isinstance(dr.get("destination_streams"), list):
            raise ConvergenceRoleObserverError("DR runtime streams are invalid")
        if type(dr.get("unresolved_conflict_count")) is not int or dr["unresolved_conflict_count"] < 0:
            raise ConvergenceRoleObserverError("DR conflict count is invalid")
        expected_dr = {key: item for key, item in dr.items() if key != "dr_state_sha256"}
        if dr.get("dr_state_sha256") != _sha256(expected_dr):
            raise ConvergenceRoleObserverError("DR runtime summary digest differs")
        _nonzero_sha256(dr.get("dr_state_sha256"), label="DR state")
    elif snapshot is not None or document.get("compose_execution") is not None:
        raise ConvergenceRoleObserverError("Witness observer must not claim a runtime database snapshot")
    if document.get("attestation_sha256") != _attestation_digest(document):
        raise ConvergenceRoleObserverError("role attestation digest differs")
    return document


def _private_output_root(request: Mapping[str, Any]) -> Path:
    root = Path(str(request["output_root"]))
    _ensure_private_secret_directory_chain(root, label="role observation output root")
    _assert_private_directory_chain(root, label="role observation output root")
    return root


def publish_attestation(
    attestation: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> tuple[Path, str]:
    """Create one immutable root-only local artifact, never replacing a prior one."""

    _require_isolated_observer_execution()
    bound_request = validate_request(request, now=_utcnow())
    document = validate_attestation(attestation, request=bound_request, now=_utcnow())
    # Publishing is not an escape hatch around the launcher/release proof.
    # Rechecking also binds direct API use to the same held worker inode.
    verify_exact_release(bound_request)
    root = _private_output_root(bound_request)
    payload = _canonical_json(document) + b"\n"
    digest = _sha256(payload)
    # The document digest excludes its digest field while the file digest
    # includes it; both bindings are intentionally retained.
    path = root / f"{document['role']}.{digest}.json"
    try:
        _write_root_only_new_bytes(
            path,
            payload,
            label="role convergence observation",
            mode=OUTPUT_FILE_MODE,
            max_size=MAX_JSON_BYTES,
        )
        outcome = "created"
    except _SecureLocalFileError as exc:
        try:
            existing = _read_root_only_bytes(
                path,
                label="existing role convergence observation",
                max_size=MAX_JSON_BYTES,
            )
        except _SecureLocalFileError as read_exc:
            raise ConvergenceRoleObserverError("role observation cannot be published safely") from read_exc
        if existing != payload:
            raise ConvergenceRoleObserverError("existing role observation differs and will not be replaced") from exc
        outcome = "reused"
    return path, outcome


def build_plan(request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a no-contact plan.  It never imports the runtime collector."""

    identity: dict[str, Any]
    if request is None:
        identity = {
            "campaign_id": None,
            "operation_id": None,
            "release_sha": None,
            "role": None,
            "runtime_target_binding_sha256": None,
            "request_sha256": None,
        }
    else:
        document = validate_request(request)
        identity = {
            "campaign_id": document["campaign_id"],
            "operation_id": document["operation_id"],
            "release_sha": document["release_sha"],
            "role": document["role"],
            "runtime_target_binding_sha256": document["runtime_target_binding_sha256"],
            "request_sha256": document["request_sha256"],
        }
    body = {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "phase": PHASE,
        "operation": OPERATION,
        **identity,
        "default_action": "plan",
        "observe_requires_explicit_execute_read_only": True,
        "runtime_snapshot_collector": "fixed-isolated-exact-release-read-only",
        "observer_launcher": "fixed-root-only-env-i-python-I-S-held-fd-git-bound",
        "local_expected_host_ip_proof_required": True,
        "supported_observations": ["database_parity", "dr_convergence"],
        "unavailable_observations": dict(UNAVAILABLE_REASONS),
        "worker_ssh_io": False,
        "worker_object_storage_io": False,
        "worker_peer_network_io": False,
        "worker_production_mutation": False,
        "direct_fi_to_ir_transfer": False,
    }
    return {**body, "plan_sha256": _sha256(body)}


def _load_request(path: Path) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink():
        raise ConvergenceRoleObserverError("role convergence observer request path is unsafe")
    try:
        path.relative_to(SECRET_ROOT_PREFIX)
    except ValueError as exc:
        raise ConvergenceRoleObserverError(
            "role convergence observer request escapes the secret root"
        ) from exc
    _assert_private_directory_chain(
        path.parent,
        label="role convergence observer request directory",
    )
    try:
        payload = _read_root_only_bytes(
            path,
            label="role convergence observer request",
            max_size=MAX_JSON_BYTES,
        )
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (_SecureLocalFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRoleObserverError("role convergence observer request is unavailable") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise ConvergenceRoleObserverError("role convergence observer request is not canonical")
    return validate_request(document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "observe"), nargs="?", default="plan")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--execute-read-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        _require_isolated_observer_execution()
        if args.action == "observe":
            # Reject a direct worker invocation before it can even consume a
            # request pathname.  The full request/path/Git binding follows in
            # ``observe`` and again before publication.
            contract = _require_launcher_descriptor_handoff()
            _close_unexpected_worker_descriptors(contract)
        request = _load_request(args.request) if args.request is not None else None
        if args.action == "plan":
            print(_canonical_json(build_plan(request)).decode("ascii"))
            return 0
        if request is None or not args.execute_read_only:
            raise ConvergenceRoleObserverError("observe requires a request and --execute-read-only")
        attestation = asyncio.run(observe(request))
        publish_attestation(attestation, request=request)
        # This is the only control-channel payload: a redacted attestation,
        # never a path, secret, URL, business value, or raw snapshot.
        print(_canonical_json(attestation).decode("ascii"))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
