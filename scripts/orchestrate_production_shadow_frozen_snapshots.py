#!/usr/bin/env python3
"""Freeze both Finland legacy writers and collect frozen-final snapshots.

The controller is plan-only unless ``--apply`` and the exact confirmation are
provided.  Apply mode holds the Nginx coordinator live lease for the entire
operation.  Bot-FI is invoked locally; WebApp-FI is reachable only through the
pinned SSH/SCP endpoint on port 37067.  Host material and collected artifacts
are published create-only.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    orchestrate_production_shadow_finland_source_snapshots as BASE,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402
from scripts import production_shadow_finland_stage as FINLAND_STAGE  # noqa: E402
from scripts import production_shadow_legacy_writer_freeze as FREEZE  # noqa: E402
from scripts import production_shadow_nginx_generation as NGINX_GENERATION  # noqa: E402


PLAN_SCHEMA = "production-shadow-frozen-snapshot-plan-v1"
RESULT_SCHEMA = "production-shadow-frozen-snapshot-orchestrator-v2"
HOST_REQUEST_SCHEMA = "production-shadow-frozen-snapshot-host-request-v1"
HOST_RESULT_SCHEMA = "production-shadow-frozen-snapshot-host-result-v1"
HOST_CURRENT_VERIFY_SCHEMA = (
    "production-shadow-current-frozen-host-verification-v1"
)
HOST_PREPARE_SCHEMA = "production-shadow-frozen-snapshot-host-prepare-v1"
JOURNAL_SCHEMA = "production-shadow-frozen-snapshot-journal-v1"
JOURNAL_EVENT_SCHEMA = "production-shadow-frozen-snapshot-journal-event-v1"
OUTCOME_SCHEMA = "production-shadow-frozen-snapshot-outcome-v1"
PUBLIC_PHASE_HANDOFF_SCHEMA = (
    "production-shadow-frozen-snapshot-public-phase-handoff-v1"
)
PUBLIC_PHASE = "stop_legacy_writers"
RESULTS_DIRECTORY = Path("results")
RESULT_PREFIX = "frozen-snapshot-result"

ROLES = ("bot_fi", "webapp_fi")
ROLE_PATHS = {"bot_fi": "bot-fi", "webapp_fi": "webapp-fi"}
ROLE_HOSTS = {
    "bot_fi": BASE.BOT_FI_HOST,
    "webapp_fi": BASE.WEBAPP_FI_HOST,
}
ROLE_TRANSPORTS = {
    "bot_fi": "local-controller",
    "webapp_fi": "trusted-ssh-scp",
}

PYTHON = BASE.PYTHON
GIT = BASE.GIT
SSH = BASE.SSH
SCP = BASE.SCP
WEBAPP_FI_PORT = 37067
KNOWN_HOSTS = BASE.KNOWN_HOSTS
DEFAULT_SSH_IDENTITY = BASE.DEFAULT_SSH_IDENTITY
PROJECT_ROOT_PREFIX = BASE.PROJECT_ROOT_PREFIX
SECRET_ROOT_PREFIX = BASE.SECRET_ROOT_PREFIX
SOURCE_OUTPUT_ROOT = BASE.SOURCE_OUTPUT_ROOT

AGENT_RELATIVE = Path(
    "scripts/orchestrate_production_shadow_frozen_snapshots.py"
)
PRODUCER_RELATIVE = Path(
    "scripts/produce_production_shadow_source_snapshot.py"
)
FREEZE_WORKER_RELATIVE = Path(
    "scripts/production_shadow_legacy_writer_freeze.py"
)
LEASE_WORKER_RELATIVE = Path(
    "scripts/orchestrate_production_shadow_nginx_generations.py"
)
BINDING_FILENAME = "source-snapshot-binding-frozen-final.json"
JOURNAL_FILENAME = "finland-frozen-final-source-snapshot-journal.json"
LOCK_FILENAME = "finland-frozen-final-source-snapshot-controller.lock"
OUTCOME_FILENAME = "finland-frozen-final-source-snapshot-outcome.json"
COLLECTION_DIRECTORY = Path("source-snapshots/frozen-final")
CONFIRMATION_PREFIX = "FREEZE-AND-COLLECT-PRODUCTION-SHADOW-FINAL-SNAPSHOTS"

SNAPSHOT_FILENAMES = (
    SOURCE.MANIFEST_FILE,
    SOURCE.ARTIFACT_FILES["database-backup"],
    SOURCE.ARTIFACT_FILES["uploads-archive"],
    SOURCE.ARTIFACT_FILES["audit-archive"],
)
MATERIAL_KEYS = ("binding", "state_receipt", "lease_claim")
HOST_ACTIONS = (
    "prepare-material",
    "install-material",
    "freeze",
    "verify",
    "verify-current",
    "snapshot",
)
ROLE_PHASES = (
    "pending",
    "material-installed",
    "frozen",
    "verified",
    "snapshotted",
    "collected",
)

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = SOURCE.MAX_ARTIFACT_BYTES
MAX_COMMAND_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_COMMAND_ERROR_BYTES = 256 * 1024
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
PROCESS_GROUP_TERM_SECONDS = 5.0
PROCESS_POLL_SECONDS = 0.05
PROCESS_TREE_QUIESCENCE_SECONDS = 0.1
MAX_PROCESS_SNAPSHOT_MEMBERS = 131_072
MAX_PROCESS_TREE_MEMBERS = 4_096
PR_SET_CHILD_SUBREAPER = 36
ZERO_SHA256 = "0" * 64
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
REMOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./:=+%-]+$")
_BOUNDED_COMMAND_LOCK = threading.Lock()

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
    "PYTHONDONTWRITEBYTECODE": "1",
}

HOST_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "binding_path",
        "binding_sha256",
        "state_receipt_path",
        "state_receipt_sha256",
        "lease_claim_path",
        "lease_claim_sha256",
        "nginx_aggregate_sha256",
        "nginx_manifest_path",
        "nginx_manifest_sha256",
        "nginx_archive_path",
        "nginx_archive_sha256",
        "freeze_evidence_path",
        "output_root",
        "pull_policy",
        "release_file_sha256",
    }
)
RELEASE_FILE_KEYS = frozenset(
    {"agent", "producer", "freeze_worker", "lease_worker"}
)
HOST_PREPARE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "operation_id",
        "release_sha",
        "role",
        "lease_claim_sha256",
        "need_transfer",
        "reconciled",
        "docker_contacted",
        "production_mutated",
    }
)
HOST_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "mode",
        "binding_sha256",
        "state_receipt_sha256",
        "lease_claim_sha256",
        "lease_claim_epoch",
        "freeze_evidence_sha256",
        "files",
        "pull_policy",
        "source_mutated",
        "current_mutated",
        "source_stopped_or_restarted",
        "redis_restored",
    }
)
FILE_RESULT_FIELDS = frozenset({"sha256", "bytes"})
HOST_CURRENT_VERIFY_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "mode",
        "binding_sha256",
        "state_receipt_sha256",
        "readback_challenge_sha256",
        "issued_at_epoch",
        "expires_at_epoch",
        "captured_at_epoch",
        "lease_claim_sha256",
        "lease_claim_epoch",
        "previous_live_lease_claim_sha256",
        "freeze_evidence_live_lease_claim_sha256",
        "freeze_evidence_sha256",
        "role_freeze_generation_sha256",
        "freeze_generation_sha256",
        "source_container_ids",
        "writer_container_ids",
        "journal_sha256",
        "legacy_writer_process_count",
        "writer_database_client_count",
        "file_mutator_process_count",
        "database_container_running",
        "redis_container_running",
        "pull_policy",
        "source_stopped_or_restarted",
        "source_mutated",
        "current_mutated",
        "service_mutated",
        "container_mutated",
        "volume_mutated",
        "data_mutated",
        "production_mutated",
    }
)


class FrozenSnapshotOrchestratorError(RuntimeError):
    """The two-host frozen-final source snapshot operation failed closed."""


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
Checkpoint = Callable[[str], None]
LeaseVerify = Callable[[], Mapping[str, Any]]
AuthorizationCheck = Callable[[], None]


class FrozenSnapshotCancellation(FrozenSnapshotOrchestratorError):
    """The controller connection or host process authority was lost."""


@dataclass(frozen=True)
class PublicCutoverContext:
    """Verified root-only inputs for the first public cutover transition."""

    manifest: Mapping[str, Any]
    manifest_sha256: str
    plan_sha256: str
    approval_path: Path
    approval_sha256: str
    approval_policy_path: Path
    approval_policy_sha256: str


def _anonymous_read_pipe_identity(
    descriptor: int,
    *,
    label: str,
) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor < 0:
        raise FrozenSnapshotOrchestratorError(
            f"{label} descriptor is invalid"
        )
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} pipe is unavailable"
        ) from exc
    if (
        not stat.S_ISFIFO(metadata.st_mode)
        or flags & os.O_ACCMODE != os.O_RDONLY
        or target != f"pipe:[{metadata.st_ino}]"
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} must be an anonymous read-only pipe"
        )
    try:
        entries = tuple(Path("/proc/self/fd").iterdir())
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} descriptor closure cannot be inspected"
        ) from exc
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name, 10) == descriptor:
            continue
        candidate = int(entry.name, 10)
        try:
            observed = os.fstat(candidate)
            observed_flags = fcntl.fcntl(candidate, fcntl.F_GETFL)
        except OSError:
            continue
        if (
            (observed.st_dev, observed.st_ino)
            == (metadata.st_dev, metadata.st_ino)
            and observed_flags & os.O_ACCMODE
            in {os.O_WRONLY, os.O_RDWR}
        ):
            raise FrozenSnapshotOrchestratorError(
                f"{label} writer end is held by the host worker"
            )
    return metadata.st_dev, metadata.st_ino


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent_pid: int
    process_group: int
    start_time: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.start_time


def _process_identity(pid: int) -> ProcessIdentity | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            return None
        return ProcessIdentity(
            pid=pid,
            parent_pid=int(fields[1], 10),
            process_group=int(fields[2], 10),
            start_time=int(fields[19], 10),
            state=fields[0],
        )
    except (OSError, UnicodeError, ValueError):
        return None


def _process_snapshot() -> dict[int, ProcessIdentity]:
    observed: dict[int, ProcessIdentity] = {}
    scanned = 0
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            scanned += 1
            if scanned > MAX_PROCESS_SNAPSHOT_MEMBERS:
                raise FrozenSnapshotOrchestratorError(
                    "subprocess inventory exceeds its process bound"
                )
            identity = _process_identity(int(entry.name, 10))
            if identity is not None:
                observed[identity.pid] = identity
    except FrozenSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "subprocess ownership inventory is unavailable"
        ) from exc
    return observed


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise FrozenSnapshotOrchestratorError(
            f"child subreaper setup failed with errno {error}"
        )


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_pid == owner
    )


def _owned_processes(
    root_identity: ProcessIdentity,
    *,
    baseline_children: frozenset[tuple[int, int]],
    tracked: set[ProcessIdentity] | None = None,
) -> set[ProcessIdentity]:
    snapshot = _process_snapshot()
    owned_ids: set[int] = set()
    observed_root = snapshot.get(root_identity.pid)
    if (
        observed_root is not None
        and observed_root.start_time == root_identity.start_time
    ):
        owned_ids.add(root_identity.pid)
    if tracked is not None:
        for identity in tracked:
            current = snapshot.get(identity.pid)
            if (
                current is not None
                and current.start_time == identity.start_time
            ):
                owned_ids.add(identity.pid)
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.pid != root_identity.pid
            and identity.parent_pid == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.pid)
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.pid not in owned_ids
                and identity.parent_pid in owned_ids
            ):
                owned_ids.add(identity.pid)
                changed = True
    owned = {
        identity
        for pid, identity in snapshot.items()
        if pid in owned_ids
    }
    if tracked is not None:
        if len(tracked | owned) > MAX_PROCESS_TREE_MEMBERS:
            raise FrozenSnapshotOrchestratorError(
                "subprocess tree exceeds its process bound"
            )
        tracked.update(owned)
    return owned


def _identity_exists(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
    )


def _identity_is_live(identity: ProcessIdentity) -> bool:
    current = _process_identity(identity.pid)
    return (
        current is not None
        and current.start_time == identity.start_time
        and current.state != "Z"
    )


def _reap_owned_zombies(
    tracked: set[ProcessIdentity],
    *,
    root_pid: int,
) -> None:
    for identity in tuple(tracked):
        if identity.pid == root_pid:
            continue
        current = _process_identity(identity.pid)
        if (
            current is None
            or current.start_time != identity.start_time
            or current.state != "Z"
        ):
            continue
        try:
            waited, _status = os.waitpid(identity.pid, os.WNOHANG)
        except ChildProcessError:
            continue
        except OSError as exc:
            raise FrozenSnapshotOrchestratorError(
                "owned subprocess zombie could not be reaped"
            ) from exc
        if waited not in {0, identity.pid}:
            raise FrozenSnapshotOrchestratorError(
                "owned subprocess reap returned an unexpected PID"
            )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    current = _process_identity(identity.pid)
    if current is None or current.start_time != identity.start_time:
        return
    try:
        descriptor = os.pidfd_open(identity.pid, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "identity-bound subprocess handle cannot be opened"
        ) from exc
    try:
        refreshed = _process_identity(identity.pid)
        if refreshed is None or refreshed.start_time != identity.start_time:
            return
        _signal_process_handle(descriptor, signum)
    except ProcessLookupError:
        return
    finally:
        os.close(descriptor)


def _signal_process_handle(descriptor: int, signum: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "identity-bound subprocess signal failed"
        ) from exc


def _signal_owned_process(
    identity: ProcessIdentity,
    signum: int,
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
) -> None:
    if identity.key == root_identity.key and root_descriptor is not None:
        _signal_process_handle(root_descriptor, signum)
        return
    _signal_process_identity(identity, signum)


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    tracked: set[ProcessIdentity],
    *,
    root_identity: ProcessIdentity,
    root_descriptor: int | None,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    root_group = root_identity.process_group

    def request_shutdown() -> None:
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        _reap_owned_zombies(tracked, root_pid=process.pid)
        for identity in tracked:
            _signal_owned_process(
                identity,
                (
                    signal.SIGKILL
                    if identity.process_group != root_group
                    else signal.SIGTERM
                ),
                root_identity=root_identity,
                root_descriptor=root_descriptor,
            )

    request_shutdown()
    deadline = time.monotonic() + PROCESS_GROUP_TERM_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        request_shutdown()
        if process.poll() is not None and not any(
            _identity_is_live(identity) for identity in tracked
        ):
            break
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, deadline - time.monotonic()),
            )
        )
    _owned_processes(
        root_identity,
        baseline_children=baseline_children,
        tracked=tracked,
    )
    for identity in tracked:
        _signal_owned_process(
            identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
    try:
        process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_owned_process(
            root_identity,
            signal.SIGKILL,
            root_identity=root_identity,
            root_descriptor=root_descriptor,
        )
        try:
            process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise FrozenSnapshotOrchestratorError(
                "subprocess root survived identity-bound cleanup"
            ) from exc
    absence_deadline = (
        time.monotonic()
        + PROCESS_GROUP_TERM_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _owned_processes(
            root_identity,
            baseline_children=baseline_children,
            tracked=tracked,
        )
        _reap_owned_zombies(tracked, root_pid=process.pid)
        live = {
            identity for identity in tracked if _identity_is_live(identity)
        }
        residue = {
            identity for identity in tracked if _identity_exists(identity)
        }
        if live or residue:
            stable_since = None
            for identity in live:
                _signal_owned_process(
                    identity,
                    signal.SIGKILL,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                )
        elif stable_since is None:
            stable_since = time.monotonic()
        elif (
            time.monotonic() - stable_since
            >= PROCESS_TREE_QUIESCENCE_SECONDS
        ):
            return
        time.sleep(
            min(
                PROCESS_POLL_SECONDS,
                max(0.0, absence_deadline - time.monotonic()),
            )
        )
    _reap_owned_zombies(tracked, root_pid=process.pid)
    if any(_identity_exists(identity) for identity in tracked):
        raise FrozenSnapshotOrchestratorError(
            "subprocess process tree survived forced cleanup"
        )


def _bounded_command_locked(
    arguments: Sequence[str],
    *,
    timeout: float,
    stdin: int | None = subprocess.DEVNULL,
    pass_fds: Sequence[int] = (),
) -> BoundedCommandResult:
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise FrozenSnapshotOrchestratorError(
            "controller command timeout is invalid"
        )
    process: subprocess.Popen[bytes] | None = None
    root_identity: ProcessIdentity | None = None
    root_descriptor: int | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    tracked: set[ProcessIdentity] = set()
    deadline = time.monotonic() + timeout
    cleaned = False
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    try:
        process = subprocess.Popen(  # noqa: S603
            list(arguments),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=SAFE_ENV,
            close_fds=True,
            pass_fds=tuple(pass_fds),
            start_new_session=True,
        )
        root_descriptor = os.pidfd_open(process.pid, 0)
        root_identity = _process_identity(process.pid)
        if root_identity is None:
            raise FrozenSnapshotOrchestratorError(
                "controller command root identity is unavailable"
            )
        tracked.add(root_identity)
        if process.stdout is None or process.stderr is None:
            raise FrozenSnapshotOrchestratorError(
                "controller command pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            _owned_processes(
                root_identity,
                baseline_children=baseline_children,
                tracked=tracked,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FrozenSnapshotOrchestratorError(
                    "controller command timed out"
                )
            events = selector.select(
                min(PROCESS_POLL_SECONDS, remaining)
            )
            if not events:
                if process.poll() is not None and not cleaned:
                    _terminate_process_tree(
                        process,
                        tracked,
                        root_identity=root_identity,
                        root_descriptor=root_descriptor,
                        baseline_children=baseline_children,
                    )
                    cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                label = str(key.data)
                buffer = buffers[label]
                limit = (
                    MAX_COMMAND_OUTPUT_BYTES
                    if label == "stdout"
                    else MAX_COMMAND_ERROR_BYTES
                )
                if len(buffer) + len(chunk) > limit:
                    raise FrozenSnapshotOrchestratorError(
                        f"controller command {label} exceeded its byte limit"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not cleaned:
                _terminate_process_tree(
                    process,
                    tracked,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                    baseline_children=baseline_children,
                )
                cleaned = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FrozenSnapshotOrchestratorError(
                "controller command timed out"
            )
        returncode = process.wait(timeout=remaining)
        return BoundedCommandResult(
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    except FrozenSnapshotOrchestratorError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrozenSnapshotOrchestratorError(
            "controller command execution failed"
        ) from exc
    finally:
        original_error = sys.exception()
        cleanup_errors: list[BaseException] = []
        if process is not None and not cleaned:
            try:
                if root_identity is None:
                    if root_descriptor is None:
                        root_identity = _process_identity(process.pid)
                    else:
                        _signal_process_handle(
                            root_descriptor,
                            signal.SIGKILL,
                        )
                        try:
                            process.wait(
                                timeout=PROCESS_GROUP_TERM_SECONDS
                            )
                        except subprocess.TimeoutExpired as exc:
                            raise FrozenSnapshotOrchestratorError(
                                "unidentified controller command survived "
                                "forced cleanup"
                            ) from exc
                        root_identity = ProcessIdentity(
                            pid=process.pid,
                            parent_pid=os.getpid(),
                            process_group=process.pid,
                            start_time=-1,
                            state="?",
                        )
                    if root_identity is None:
                        raise FrozenSnapshotOrchestratorError(
                            "controller command root could not be bound "
                            "for cleanup"
                        )
                _terminate_process_tree(
                    process,
                    tracked,
                    root_identity=root_identity,
                    root_descriptor=root_descriptor,
                    baseline_children=baseline_children,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            selector.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except BaseException as exc:
                    cleanup_errors.append(exc)
        if root_descriptor is not None:
            try:
                os.close(root_descriptor)
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            cleanup_error = cleanup_errors[0]
            if original_error is not None:
                raise original_error from cleanup_error
            raise cleanup_error


def _bounded_command(
    arguments: Sequence[str],
    *,
    timeout: float,
    stdin: int | None = subprocess.DEVNULL,
    pass_fds: Sequence[int] = (),
) -> BoundedCommandResult:
    with _BOUNDED_COMMAND_LOCK:
        return _bounded_command_locked(
            arguments,
            timeout=timeout,
            stdin=stdin,
            pass_fds=pass_fds,
        )


class ControllerLivenessGuard:
    """Keep host mutations bound to the controller-owned pipe."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (
        signal.SIGHUP,
        signal.SIGTERM,
        signal.SIGINT,
        _WAKE_SIGNAL,
    )

    def __init__(self, control_fd: int) -> None:
        _anonymous_read_pipe_identity(
            control_fd,
            label="controller liveness",
        )
        if threading.current_thread() is not threading.main_thread():
            raise FrozenSnapshotOrchestratorError(
                "host action must run in the main thread"
            )
        try:
            self._fd = os.dup(control_fd)
            os.set_inheritable(self._fd, False)
            os.set_blocking(self._fd, False)
        except OSError as exc:
            raise FrozenSnapshotOrchestratorError(
                "controller liveness pipe cannot be secured"
            ) from exc
        self._cancelled = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller liveness was lost"
        self._exception_delivered = False
        self._closed = False
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

    @property
    def control_fd(self) -> int:
        return self._fd

    def _cancel(self, reason: str, *, wake_main: bool) -> None:
        if self._cancelled.is_set():
            return
        self._reason = reason
        self._cancelled.set()
        if wake_main:
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    pass

    def _sample(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            if not selector.select(0):
                return
            try:
                payload = os.read(self._fd, 1)
            except BlockingIOError:
                return
        finally:
            selector.close()
        reason = (
            "controller liveness pipe reached EOF"
            if payload == b""
            else "controller liveness pipe carried forbidden data"
        )
        self._cancel(reason, wake_main=False)
        self.check()

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if self._exception_delivered:
            return
        self._exception_delivered = True
        if signum == self._WAKE_SIGNAL:
            reason = self._reason
        else:
            reason = f"frozen snapshot host received signal {signum}"
            self._cancel(reason, wake_main=False)
        raise FrozenSnapshotCancellation(reason)

    def _monitor_control(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(PROCESS_POLL_SECONDS):
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        return
                    payload = b""
                reason = (
                    "controller liveness pipe reached EOF"
                    if payload == b""
                    else "controller liveness pipe carried forbidden data"
                )
                self._cancel(reason, wake_main=True)
                return
        finally:
            selector.close()

    def __enter__(self) -> ControllerLivenessGuard:
        try:
            for signum in self._HANDLED_SIGNALS:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            self._sample()
            self._monitor = threading.Thread(
                target=self._monitor_control,
                name="frozen-snapshot-controller-liveness",
                daemon=True,
            )
            self._monitor.start()
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def check(self) -> None:
        if self._cancelled.is_set() and not self._exception_delivered:
            self._exception_delivered = True
            raise FrozenSnapshotCancellation(self._reason)

    def _restore(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._exception_delivered = True
        self._stopping.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        try:
            os.close(self._fd)
        except OSError:
            pass
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def __exit__(
        self,
        error_type: Any,
        _value: Any,
        _traceback: Any,
    ) -> None:
        deliver_after_restore = (
            self._cancelled.is_set()
            and error_type is None
            and not self._exception_delivered
        )
        reason = self._reason
        self._restore()
        if deliver_after_restore:
            raise FrozenSnapshotCancellation(reason)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrozenSnapshotOrchestratorError(
            "value is not canonical JSON"
        ) from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant: {token}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise FrozenSnapshotOrchestratorError(
            f"{label} must contain one JSON object"
        )
    return value


def _canonical_uuid4(value: Any) -> str:
    try:
        return BASE._canonical_uuid4(value)
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "operation id is invalid"
        ) from exc


def _release_sha(value: Any, *, label: str = "release SHA") -> str:
    if (
        not isinstance(value, str)
        or SHA40_RE.fullmatch(value) is None
        or value == "0" * 40
    ):
        raise FrozenSnapshotOrchestratorError(f"{label} is invalid")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _bounded_bytes(value: Any, *, label: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} is outside its size bound"
        )
    return value


def _hash_payload(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def confirmation_phrase(
    operation_id: str,
    release_sha: str,
    *,
    nginx_aggregate_sha256: str,
    state_receipt_sha256: str,
    binding_sha256: Mapping[str, str],
) -> str:
    operation_id = _canonical_uuid4(operation_id)
    release_sha = _release_sha(release_sha)
    aggregate = _nonzero_sha256(
        nginx_aggregate_sha256,
        label="Nginx aggregate SHA-256",
    )
    receipt = _nonzero_sha256(
        state_receipt_sha256,
        label="legacy-frozen receipt SHA-256",
    )
    if set(binding_sha256) != set(ROLES):
        raise FrozenSnapshotOrchestratorError(
            "confirmation binding closure differs"
        )
    role_hashes = [
        _nonzero_sha256(
            binding_sha256[role],
            label=f"{role} binding SHA-256",
        )
        for role in ROLES
    ]
    return (
        f"{CONFIRMATION_PREFIX}:{operation_id}:{release_sha}:{aggregate}:"
        f"{receipt}:{role_hashes[0]}:{role_hashes[1]}"
    )


def canonical_paths(
    operation_id: str,
    release_sha: str,
    *,
    state_receipt_sha256: str | None = None,
    lease_claim_sha256: str | None = None,
) -> dict[str, Any]:
    operation_id = _canonical_uuid4(operation_id)
    release_sha = _release_sha(release_sha)
    project_root = PROJECT_ROOT_PREFIX / operation_id
    release_root = project_root / "releases" / release_sha
    operation_secret = SECRET_ROOT_PREFIX / operation_id
    controller_root = operation_secret / "controller"
    collection_root = controller_root / COLLECTION_DIRECTORY
    nginx_secret = operation_secret / "nginx-coordinator"
    roles: dict[str, dict[str, Path]] = {}
    for role in ROLES:
        role_secret = operation_secret / ROLE_PATHS[role]
        binding = role_secret / BINDING_FILENAME
        snapshot = (
            SOURCE_OUTPUT_ROOT / operation_id / role / "frozen-final"
        )
        nginx_root = (
            NGINX_GENERATION.DEFAULT_OPERATION_BASE
            / operation_id
            / ROLE_PATHS[role]
        )
        roles[role] = {
            "secret_root": role_secret,
            "binding": binding,
            "snapshot": snapshot,
            "manifest": snapshot / SOURCE.MANIFEST_FILE,
            "collection": collection_root / role,
            "freeze_evidence": (
                operation_secret
                / FREEZE.STATE_DIRECTORY_NAME
                / role
                / FREEZE.EVIDENCE_FILENAME
            ),
            "nginx_manifest": nginx_root / "manifest.json",
            "nginx_archive": nginx_root / "archive.tar",
        }
    result: dict[str, Any] = {
        "operation_id": operation_id,
        "release_sha": release_sha,
        "project_root": project_root,
        "release_root": release_root,
        "agent": release_root / AGENT_RELATIVE,
        "producer": release_root / PRODUCER_RELATIVE,
        "freeze_worker": release_root / FREEZE_WORKER_RELATIVE,
        "lease_worker": release_root / LEASE_WORKER_RELATIVE,
        "operation_secret": operation_secret,
        "controller_root": controller_root,
        "collection_root": collection_root,
        "results": controller_root / RESULTS_DIRECTORY,
        "journal": controller_root / JOURNAL_FILENAME,
        "lock": controller_root / LOCK_FILENAME,
        "outcome": controller_root / OUTCOME_FILENAME,
        "nginx_secret": nginx_secret,
        "roles": roles,
    }
    if state_receipt_sha256 is not None:
        receipt = _nonzero_sha256(
            state_receipt_sha256,
            label="legacy-frozen receipt SHA-256",
        )
        result["state_receipt"] = (
            nginx_secret / "receipts" / f"legacy-frozen-{receipt}.json"
        )
    if lease_claim_sha256 is not None:
        claim = _nonzero_sha256(
            lease_claim_sha256,
            label="live lease claim SHA-256",
        )
        result["lease_claim"] = (
            nginx_secret / "live-leases" / "claims" / f"{claim}.json"
        )
    return result


def _public_journal_bindings(context: PublicCutoverContext) -> dict[str, str]:
    manifest = context.manifest
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan_sha256,
        "campaign_id": str(manifest["campaign_id"]),
        "operation_id": str(manifest["operation_id"]),
        "release_sha": str(manifest["release_sha"]),
        "legacy_release_sha": str(manifest["legacy_release_sha"]),
    }


def _root_private_digest(path: Path, *, label: str) -> str:
    return _hash_payload(
        _read_file(
            path,
            label=label,
            required_uid=0,
            expected_mode=0o600,
            maximum=16 * 1024 * 1024,
        )
    )


def _load_public_cutover_context(
    *,
    manifest_path: Path | None,
    approval_path: Path | None,
    approval_policy_path: Path | None,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
) -> PublicCutoverContext:
    """Load the exact authorization closure before any live freeze action."""

    if (
        manifest_path is None
        or approval_path is None
        or approval_policy_path is None
    ):
        raise FrozenSnapshotOrchestratorError(
            "apply requires the root-only manifest, approval, and approval policy"
        )
    for path, label in (
        (manifest_path, "cutover manifest"),
        (approval_path, "cutover approval"),
        (approval_policy_path, "cutover approval policy"),
    ):
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or ".." in path.parts
            or Path(os.path.abspath(path)) != path
        ):
            raise FrozenSnapshotOrchestratorError(
                f"{label} path is not canonical absolute"
            )
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            manifest_path
        )
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=manifest_path,
        )
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            manifest,
            approval_path=approval_path,
            approval_policy_path=approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenSnapshotOrchestratorError(
            "cutover manifest, plan, or authorization is invalid or expired"
        ) from exc
    approval_sha256 = _root_private_digest(
        approval_path,
        label="production cutover approval",
    )
    approval_policy_sha256 = _root_private_digest(
        approval_policy_path,
        label="production human approval policy",
    )
    expected_identity = {
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
    }
    if (
        any(manifest.get(key) != value for key, value in expected_identity.items())
        or manifest["artifacts"].get("cutover_approval_sha256")
        != approval_sha256
        or manifest["artifacts"].get("human_approval_policy_sha256")
        != approval_policy_sha256
        or manifest["artifacts"].get("nginx_freeze_generation_sha256")
        != inputs.aggregate["generation_sha256"]["legacy-frozen"]
        or any(
            manifest["topology"][role]["host"] != ROLE_HOSTS[role]
            or manifest["topology"][role]["role"] != role
            for role in ROLES
        )
        or any(
            bindings[role].legacy_release_sha
            != manifest["legacy_release_sha"]
            or bindings[role].controller_manifest_sha256 != manifest_sha256
            or bindings[role].approval_sha256 != approval_sha256
            for role in ROLES
        )
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen-final inputs differ from the public cutover closure"
        )
    plan_sha256 = _nonzero_sha256(
        plan.get("plan_sha256"),
        label="public cutover plan SHA-256",
    )
    return PublicCutoverContext(
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan_sha256,
        approval_path=approval_path,
        approval_sha256=approval_sha256,
        approval_policy_path=approval_policy_path,
        approval_policy_sha256=approval_policy_sha256,
    )


def _verify_public_cutover_authorization(
    context: PublicCutoverContext,
) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            dict(context.manifest),
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenSnapshotOrchestratorError(
            "production approval is invalid or expired"
        ) from exc


def _public_phase_prefix() -> list[str]:
    return list(CONTROLLER.PHASES[: CONTROLLER.PHASES.index(PUBLIC_PHASE)])


def _validate_public_phase_state(
    state: Mapping[str, Any],
    *,
    context: PublicCutoverContext,
    status: str,
) -> dict[str, Any]:
    prefix = _public_phase_prefix()
    expected_started = PUBLIC_PHASE if status == "phase_started" else None
    if (
        any(
            state.get(key) != value
            for key, value in _public_journal_bindings(context).items()
        )
        or state.get("status") != status
        or state.get("completed_phases") != prefix
        or state.get("started_phase") != expected_started
        or state.get("rollback_eligible") is not True
        or state.get("first_business_write_allowed") is not False
        or not isinstance(state.get("events"), list)
        or not state["events"]
        or not isinstance(state.get("state_sha256"), str)
        or CONTROLLER.SHA256_RE.fullmatch(state["state_sha256"]) is None
        or state["state_sha256"] == ZERO_SHA256
        or not isinstance(state.get("event_tail_sha256"), str)
        or CONTROLLER.SHA256_RE.fullmatch(state["event_tail_sha256"]) is None
        or state["event_tail_sha256"] == ZERO_SHA256
    ):
        raise FrozenSnapshotOrchestratorError(
            "public cutover journal is outside the exact pre-freeze corridor"
        )
    if status == "phase_started":
        last = state["events"][-1]
        if (
            not isinstance(last, dict)
            or last.get("kind") != "phase_started"
            or last.get("phase") != PUBLIC_PHASE
            or last.get("event_hash") != state["event_tail_sha256"]
        ):
            raise FrozenSnapshotOrchestratorError(
                "public cutover journal start transition differs"
            )
    return dict(state)


def _public_phase_handoff(
    *,
    context: PublicCutoverContext,
    inputs: NGINX.CoordinatorInputs,
    state: Mapping[str, Any],
    status: str,
    prior_intent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = _validate_public_phase_state(
        state,
        context=context,
        status="active" if status == "intent" else "phase_started",
    )
    started = status == "started"
    if started:
        if prior_intent is None:
            raise FrozenSnapshotOrchestratorError(
                "public phase start lacks a durable prestart intent"
            )
        prestart_state_sha256 = prior_intent.get(
            "prestart_journal_state_sha256"
        )
        prestart_event_tail_sha256 = prior_intent.get(
            "prestart_journal_event_tail_sha256"
        )
        prestart_event_count = prior_intent.get("prestart_journal_event_count")
    else:
        prestart_state_sha256 = state["state_sha256"]
        prestart_event_tail_sha256 = state["event_tail_sha256"]
        prestart_event_count = len(state["events"])
    return {
        "schema": PUBLIC_PHASE_HANDOFF_SCHEMA,
        "status": status,
        "phase": PUBLIC_PHASE,
        **_public_journal_bindings(context),
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "approval_sha256": context.approval_sha256,
        "approval_policy_sha256": context.approval_policy_sha256,
        "prestart_journal_state_sha256": prestart_state_sha256,
        "prestart_journal_event_tail_sha256": prestart_event_tail_sha256,
        "prestart_journal_event_count": prestart_event_count,
        "started_journal_state_sha256": (
            state["state_sha256"] if started else None
        ),
        "started_journal_event_tail_sha256": (
            state["event_tail_sha256"] if started else None
        ),
        "started_journal_event_count": len(state["events"]) if started else None,
    }


def _material_paths(
    request: Mapping[str, Any],
) -> dict[str, tuple[Path, Path, str]]:
    result: dict[str, tuple[Path, Path, str]] = {}
    rows = {
        "binding": (
            Path(str(request["binding_path"])),
            str(request["binding_sha256"]),
        ),
        "state_receipt": (
            Path(str(request["state_receipt_path"])),
            str(request["state_receipt_sha256"]),
        ),
        "lease_claim": (
            Path(str(request["lease_claim_path"])),
            str(request["lease_claim_sha256"]),
        ),
    }
    for key, (final, digest) in rows.items():
        partial = final.with_name(f".{final.name}.{digest}.transfer")
        result[key] = (final, partial, digest)
    return result


def _assert_absolute_safe_path(path: Path, *, label: str) -> None:
    try:
        BASE._assert_absolute_safe_path(path, label=label)
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _assert_directory(
    path: Path,
    *,
    label: str,
    required_uid: int = 0,
    private: bool = True,
) -> None:
    try:
        BASE._assert_directory(
            path,
            label=label,
            required_uid=required_uid,
            private=private,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _fsync_directory(path: Path) -> None:
    try:
        BASE._fsync_directory(path)
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _ensure_private_child(
    path: Path,
    *,
    parent: Path,
    label: str,
    required_uid: int = 0,
) -> str:
    try:
        return BASE._ensure_private_child(
            path,
            parent=parent,
            label=label,
            required_uid=required_uid,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _hash_file(
    path: Path,
    *,
    label: str,
    required_uid: int = 0,
    expected_mode: int = 0o600,
    maximum: int = MAX_ARTIFACT_BYTES,
    allow_two_links: bool = False,
) -> tuple[str, int]:
    try:
        return BASE._hash_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=expected_mode,
            maximum=maximum,
            allow_two_links=allow_two_links,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _read_file(
    path: Path,
    *,
    label: str,
    required_uid: int = 0,
    expected_mode: int = 0o600,
    maximum: int = MAX_JSON_BYTES,
) -> bytes:
    try:
        return BASE._read_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=expected_mode,
            maximum=maximum,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _binding(
    path: Path,
    *,
    operation_id: str,
    release_sha: str,
    role: str,
) -> SOURCE.SnapshotBinding:
    try:
        binding = SOURCE.load_binding(path)
    except SOURCE.SourceSnapshotError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{role} frozen-final source binding is invalid"
        ) from exc
    if (
        binding.operation_id != operation_id
        or binding.release_sha != release_sha
        or binding.role != role
        or binding.mode != "frozen-final"
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{role} binding is not the requested frozen-final binding"
        )
    return binding


def load_bindings(
    *,
    operation_id: str,
    release_sha: str,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
) -> dict[str, SOURCE.SnapshotBinding]:
    bindings = {
        "bot_fi": _binding(
            bot_fi_binding,
            operation_id=operation_id,
            release_sha=release_sha,
            role="bot_fi",
        ),
        "webapp_fi": _binding(
            webapp_fi_binding,
            operation_id=operation_id,
            release_sha=release_sha,
            role="webapp_fi",
        ),
    }
    first = bindings["bot_fi"]
    second = bindings["webapp_fi"]
    for field in (
        "legacy_release_sha",
        "controller_manifest_sha256",
        "approval_sha256",
    ):
        if getattr(first, field) != getattr(second, field):
            raise FrozenSnapshotOrchestratorError(
                "frozen-final bindings do not share one controller closure"
            )
    return bindings


def encode_host_request(document: Mapping[str, Any]) -> str:
    raw = canonical_json(document)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_host_request(encoded: str) -> dict[str, Any]:
    if (
        not isinstance(encoded, str)
        or not 1 <= len(encoded) <= 128 * 1024
        or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None
    ):
        raise FrozenSnapshotOrchestratorError(
            "host request encoding is invalid"
        )
    try:
        raw = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except ValueError as exc:
        raise FrozenSnapshotOrchestratorError(
            "host request encoding is invalid"
        ) from exc
    document = _strict_json(raw, label="host request")
    if raw != canonical_json(document) or set(document) != HOST_REQUEST_FIELDS:
        raise FrozenSnapshotOrchestratorError(
            "host request is not exact canonical JSON"
        )
    if (
        document["schema"] != HOST_REQUEST_SCHEMA
        or document["action"] not in HOST_ACTIONS
        or document["role"] not in ROLES
        or document["expected_host"] != ROLE_HOSTS[document["role"]]
        or document["pull_policy"] != "never"
        or document["output_root"] != str(SOURCE_OUTPUT_ROOT)
        or not isinstance(document["release_file_sha256"], dict)
        or set(document["release_file_sha256"]) != RELEASE_FILE_KEYS
    ):
        raise FrozenSnapshotOrchestratorError(
            "host request contract is invalid"
        )
    operation_id = _canonical_uuid4(document["operation_id"])
    release_sha = _release_sha(document["release_sha"])
    _release_sha(document["release_tree_sha"], label="release tree SHA")
    aggregate_sha256 = _nonzero_sha256(
        document["nginx_aggregate_sha256"],
        label="Nginx aggregate SHA-256",
    )
    del aggregate_sha256
    for key, digest in document["release_file_sha256"].items():
        _nonzero_sha256(digest, label=f"{key} release file SHA-256")
    for key in (
        "binding_sha256",
        "state_receipt_sha256",
        "lease_claim_sha256",
        "nginx_manifest_sha256",
        "nginx_archive_sha256",
    ):
        _nonzero_sha256(document[key], label=key)
    paths = canonical_paths(
        operation_id,
        release_sha,
        state_receipt_sha256=str(document["state_receipt_sha256"]),
        lease_claim_sha256=str(document["lease_claim_sha256"]),
    )
    role_paths = paths["roles"][str(document["role"])]
    expected = {
        "binding_path": role_paths["binding"],
        "state_receipt_path": paths["state_receipt"],
        "lease_claim_path": paths["lease_claim"],
        "nginx_manifest_path": role_paths["nginx_manifest"],
        "nginx_archive_path": role_paths["nginx_archive"],
        "freeze_evidence_path": role_paths["freeze_evidence"],
    }
    if any(document[field] != str(path) for field, path in expected.items()):
        raise FrozenSnapshotOrchestratorError(
            "host request contains a noncanonical path"
        )
    return document


def _release_file_paths(
    operation_id: str,
    release_sha: str,
) -> dict[str, Path]:
    paths = canonical_paths(operation_id, release_sha)
    return {
        "agent": paths["agent"],
        "producer": paths["producer"],
        "freeze_worker": paths["freeze_worker"],
        "lease_worker": paths["lease_worker"],
    }


def _release_file_hashes(
    operation_id: str,
    release_sha: str,
    *,
    required_uid: int,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, path in _release_file_paths(
        operation_id,
        release_sha,
    ).items():
        digest, _size = _hash_release_file(
            path,
            label=f"release-owned {key}",
            required_uid=required_uid,
        )
        result[key] = digest
    return result


def _hash_release_file(
    path: Path,
    *,
    label: str,
    required_uid: int,
) -> tuple[str, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} is unavailable"
        ) from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != required_uid
        or metadata.st_gid != 0
        or metadata.st_nlink != 1
        or mode not in {0o644, 0o755}
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} ownership or mode is unsafe"
        )
    return _hash_file(
        path,
        label=label,
        required_uid=required_uid,
        expected_mode=mode,
        maximum=MAX_JSON_BYTES,
    )


def build_host_request(
    *,
    action: str,
    inputs: NGINX.CoordinatorInputs,
    role: str,
    binding_sha256: str,
    state_receipt_sha256: str,
    lease_claim_sha256: str,
    release_file_sha256: Mapping[str, str],
) -> dict[str, Any]:
    if action not in HOST_ACTIONS or role not in ROLES:
        raise FrozenSnapshotOrchestratorError(
            "host request action or role is invalid"
        )
    paths = canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        state_receipt_sha256=state_receipt_sha256,
        lease_claim_sha256=lease_claim_sha256,
    )
    role_paths = paths["roles"][role]
    role_material = inputs.roles[role]
    document = {
        "schema": HOST_REQUEST_SCHEMA,
        "action": action,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "role": role,
        "expected_host": ROLE_HOSTS[role],
        "binding_path": str(role_paths["binding"]),
        "binding_sha256": binding_sha256,
        "state_receipt_path": str(paths["state_receipt"]),
        "state_receipt_sha256": state_receipt_sha256,
        "lease_claim_path": str(paths["lease_claim"]),
        "lease_claim_sha256": lease_claim_sha256,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "nginx_manifest_path": str(role_paths["nginx_manifest"]),
        "nginx_manifest_sha256": role_material.manifest_sha256,
        "nginx_archive_path": str(role_paths["nginx_archive"]),
        "nginx_archive_sha256": role_material.manifest["archive"]["sha256"],
        "freeze_evidence_path": str(role_paths["freeze_evidence"]),
        "output_root": str(SOURCE_OUTPUT_ROOT),
        "pull_policy": "never",
        "release_file_sha256": dict(release_file_sha256),
    }
    decode_host_request(encode_host_request(document))
    return document


def _safe_remote_token(value: str) -> None:
    if (
        not value
        or REMOTE_TOKEN_RE.fullmatch(value) is None
        or any(
            character in value
            for character in ("$", "`", ";", "&", "|", "<", ">")
        )
    ):
        raise FrozenSnapshotOrchestratorError(
            "remote command token is unsafe"
        )


def _remote_command(arguments: Sequence[str]) -> str:
    if not arguments:
        raise FrozenSnapshotOrchestratorError(
            "remote command argv is empty"
        )
    for value in arguments:
        _safe_remote_token(value)
    command = " ".join(arguments)
    if any(character in command for character in ("\n", "\r", "\x00")):
        raise FrozenSnapshotOrchestratorError(
            "remote command contains a line break"
        )
    return command


def _ssh_options(
    ssh_identity: Path,
    *,
    known_hosts: Path,
) -> list[str]:
    _assert_absolute_safe_path(ssh_identity, label="SSH identity")
    _assert_absolute_safe_path(known_hosts, label="known-hosts")
    return [
        "-p",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "LogLevel=ERROR",
    ]


def ssh_arguments(
    ssh_identity: Path,
    *,
    known_hosts: Path = KNOWN_HOSTS,
    remote_arguments: Sequence[str],
) -> list[str]:
    return [
        SSH,
        "-T",
        *_ssh_options(ssh_identity, known_hosts=known_hosts),
        f"root@{BASE.WEBAPP_FI_HOST}",
        _remote_command(remote_arguments),
    ]


def _validate_scp_host_path(
    path: Path,
    *,
    operation_id: str,
    direction: str,
) -> None:
    _assert_absolute_safe_path(path, label=f"SCP {direction} path")
    allowed_roots = (
        SECRET_ROOT_PREFIX / operation_id,
        SOURCE_OUTPUT_ROOT / operation_id,
    )
    if not any(
        path == root or root in path.parents for root in allowed_roots
    ):
        raise FrozenSnapshotOrchestratorError(
            f"SCP {direction} path is outside the operation"
        )
    if ":" in str(path):
        raise FrozenSnapshotOrchestratorError(
            f"SCP {direction} path contains a remote separator"
        )


def scp_upload_arguments(
    ssh_identity: Path,
    *,
    known_hosts: Path = KNOWN_HOSTS,
    source: Path,
    remote_destination: Path,
    operation_id: str,
) -> list[str]:
    _assert_absolute_safe_path(source, label="SCP upload source")
    _validate_scp_host_path(
        remote_destination,
        operation_id=operation_id,
        direction="upload destination",
    )
    try:
        relative = remote_destination.relative_to(
            SECRET_ROOT_PREFIX / operation_id
        )
    except ValueError as exc:
        raise FrozenSnapshotOrchestratorError(
            "SCP upload destination is outside the operation secret root"
        ) from exc
    parts = relative.parts
    name = remote_destination.name
    binding_name_match = re.fullmatch(
        rf"\.{re.escape(BINDING_FILENAME)}\.([0-9a-f]{{64}})"
        r"\.transfer",
        name,
    )
    binding_match = (
        len(parts) == 2
        and parts[0] == ROLE_PATHS["webapp_fi"]
        and binding_name_match is not None
        and binding_name_match.group(1) != ZERO_SHA256
    )
    receipt_match = re.fullmatch(
        r"\.legacy-frozen-([0-9a-f]{64})\.json\."
        r"([0-9a-f]{64})\.transfer",
        name,
    )
    receipt_canonical = (
        len(parts) == 3
        and parts[:2] == ("nginx-coordinator", "receipts")
        and receipt_match is not None
        and receipt_match.group(1) == receipt_match.group(2)
        and receipt_match.group(1) != ZERO_SHA256
    )
    claim_match = re.fullmatch(
        r"\.([0-9a-f]{64})\.json\.([0-9a-f]{64})\.transfer",
        name,
    )
    claim_canonical = (
        len(parts) == 4
        and parts[:3]
        == ("nginx-coordinator", "live-leases", "claims")
        and claim_match is not None
        and claim_match.group(1) == claim_match.group(2)
        and claim_match.group(1) != ZERO_SHA256
    )
    if (
        ":" in str(source)
        or not (binding_match or receipt_canonical or claim_canonical)
    ):
        raise FrozenSnapshotOrchestratorError(
            "SCP upload path is not a canonical material partial"
        )
    return [
        SCP,
        "-q",
        "-p",
        "-P",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "LogLevel=ERROR",
        "--",
        str(source),
        f"root@{BASE.WEBAPP_FI_HOST}:{remote_destination}",
    ]


def scp_download_arguments(
    ssh_identity: Path,
    *,
    known_hosts: Path = KNOWN_HOSTS,
    remote_source: Path,
    destination: Path,
    operation_id: str,
) -> list[str]:
    _validate_scp_host_path(
        remote_source,
        operation_id=operation_id,
        direction="download source",
    )
    _validate_scp_host_path(
        destination,
        operation_id=operation_id,
        direction="download destination",
    )
    relative = remote_source.relative_to(SOURCE_OUTPUT_ROOT / operation_id)
    destination_relative = destination.relative_to(
        SECRET_ROOT_PREFIX / operation_id
    )
    if (
        len(relative.parts) != 3
        or relative.parts[:2] != ("webapp_fi", "frozen-final")
        or relative.parts[2] not in SNAPSHOT_FILENAMES
        or destination_relative.parts[:4]
        != (
            "controller",
            "source-snapshots",
            "frozen-final",
            "webapp_fi",
        )
        or len(destination_relative.parts) != 5
        or destination.name != f".{remote_source.name}.transfer"
    ):
        raise FrozenSnapshotOrchestratorError(
            "SCP download path is not canonical"
        )
    return [
        SCP,
        "-q",
        "-p",
        "-P",
        str(WEBAPP_FI_PORT),
        "-i",
        str(ssh_identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "LogLevel=ERROR",
        "--",
        f"root@{BASE.WEBAPP_FI_HOST}:{remote_source}",
        str(destination),
    ]


def _run_command(
    arguments: Sequence[str],
    *,
    runner: Runner | None,
    timeout: int,
    allowed: frozenset[str],
    stdin: int | None = subprocess.DEVNULL,
    pass_fds: Sequence[int] = (),
) -> bytes:
    if (
        not arguments
        or arguments[0] not in allowed
        or any(not isinstance(value, str) or not value for value in arguments)
    ):
        raise FrozenSnapshotOrchestratorError(
            "controller command argv is outside its allowlist"
        )
    if runner is None:
        completed = _bounded_command(
            arguments,
            timeout=timeout,
            stdin=stdin,
            pass_fds=pass_fds,
        )
    else:
        try:
            completed = runner(
                list(arguments),
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
                env=SAFE_ENV,
                close_fds=True,
                pass_fds=tuple(pass_fds),
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrozenSnapshotOrchestratorError(
                f"command is unavailable: {Path(arguments[0]).name}"
            ) from exc
    if (
        len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_ERROR_BYTES
    ):
        raise FrozenSnapshotOrchestratorError(
            "command output exceeded its bound"
        )
    if completed.returncode != 0:
        raise FrozenSnapshotOrchestratorError(
            f"command failed closed: {Path(arguments[0]).name}"
        )
    return completed.stdout


def _parse_command_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not 1 <= len(raw) <= MAX_JSON_BYTES + 1:
        raise FrozenSnapshotOrchestratorError(
            f"{label} output is empty or oversized"
        )
    stripped = raw.strip()
    document = _strict_json(stripped, label=label)
    if stripped != canonical_json(document):
        raise FrozenSnapshotOrchestratorError(
            f"{label} output is not canonical JSON"
        )
    return document


def _assert_ssh_material(
    ssh_identity: Path,
    *,
    known_hosts: Path,
    required_uid: int,
) -> None:
    for path, label in (
        (ssh_identity, "SSH identity"),
        (known_hosts, "known-hosts"),
    ):
        _hash_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_JSON_BYTES,
        )


def _validate_exact_release(
    request: Mapping[str, Any],
    *,
    agent_path: Path,
    runner: Runner | None,
    required_uid: int,
) -> None:
    release_root = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )["release_root"]
    expected_paths = _release_file_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )
    if agent_path != expected_paths["agent"]:
        raise FrozenSnapshotOrchestratorError(
            "host agent is not running from the exact operation release"
        )
    _assert_directory(
        release_root,
        label="operation release root",
        required_uid=required_uid,
        private=True,
    )
    for key, path in expected_paths.items():
        digest, _size = _hash_release_file(
            path,
            label=f"release-owned {key}",
            required_uid=required_uid,
        )
        if digest != request["release_file_sha256"][key]:
            raise FrozenSnapshotOrchestratorError(
                f"release-owned {key} digest differs"
            )
    commands = (
        (
            [GIT, "-C", str(release_root), "rev-parse", "--show-toplevel"],
            str(release_root),
        ),
        (
            [GIT, "-C", str(release_root), "rev-parse", "HEAD"],
            str(request["release_sha"]),
        ),
        (
            [
                GIT,
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            "",
        ),
    )
    for arguments, expected in commands:
        observed = _run_command(
            arguments,
            runner=runner,
            timeout=60,
            allowed=frozenset({GIT}),
        ).decode("ascii", errors="strict").strip()
        if observed != expected:
            raise FrozenSnapshotOrchestratorError(
                "operation release is not exact, detached, and clean"
            )
    detached_arguments = [
        GIT,
        "-C",
        str(release_root),
        "symbolic-ref",
        "-q",
        "HEAD",
    ]
    if runner is None:
        detached = _bounded_command(detached_arguments, timeout=60)
    else:
        try:
            detached = runner(
                detached_arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=60,
                env=SAFE_ENV,
                close_fds=True,
                pass_fds=(),
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrozenSnapshotOrchestratorError(
                "operation release detached-state check failed"
            ) from exc
    if (
        detached.returncode != 1
        or detached.stdout
        or len(detached.stderr) > MAX_COMMAND_ERROR_BYTES
    ):
        raise FrozenSnapshotOrchestratorError(
            "operation release is not detached"
        )


def _ensure_host_directories(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> None:
    paths = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
        state_receipt_sha256=str(request["state_receipt_sha256"]),
        lease_claim_sha256=str(request["lease_claim_sha256"]),
    )
    operation_secret = paths["operation_secret"]
    _assert_directory(
        operation_secret,
        label="operation secret root",
        required_uid=required_uid,
        private=True,
    )
    role_root = paths["roles"][str(request["role"])]["secret_root"]
    _ensure_private_child(
        role_root,
        parent=operation_secret,
        label="host role secret root",
        required_uid=required_uid,
    )
    nginx_root = paths["nginx_secret"]
    _ensure_private_child(
        nginx_root,
        parent=operation_secret,
        label="host Nginx coordinator root",
        required_uid=required_uid,
    )
    receipts = nginx_root / "receipts"
    _ensure_private_child(
        receipts,
        parent=nginx_root,
        label="host Nginx receipt root",
        required_uid=required_uid,
    )
    leases = nginx_root / "live-leases"
    _ensure_private_child(
        leases,
        parent=nginx_root,
        label="host live lease root",
        required_uid=required_uid,
    )
    claims = leases / "claims"
    _ensure_private_child(
        claims,
        parent=leases,
        label="host live lease claim root",
        required_uid=required_uid,
    )


def _file_state(
    path: Path,
    *,
    digest: str,
    label: str,
    required_uid: int,
    allow_two_links: bool = False,
) -> tuple[bool, bool, os.stat_result | None]:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False, False, None
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} cannot be inspected"
        ) from exc
    try:
        observed, _size = _hash_file(
            path,
            label=label,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=MAX_JSON_BYTES,
            allow_two_links=allow_two_links,
        )
        metadata = path.stat(follow_symlinks=False)
    except FrozenSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} is foreign or unsafe"
        ) from exc
    return True, observed == digest, metadata


def _reconcile_material(
    final: Path,
    partial: Path,
    *,
    digest: str,
    label: str,
    required_uid: int,
) -> tuple[bool, bool, bool]:
    final_exists, final_matches, final_meta = _file_state(
        final,
        digest=digest,
        label=f"{label} final",
        required_uid=required_uid,
        allow_two_links=True,
    )
    if final_exists and not final_matches:
        raise FrozenSnapshotOrchestratorError(
            f"existing {label} differs"
        )
    partial_exists, partial_matches, partial_meta = _file_state(
        partial,
        digest=digest,
        label=f"{label} transfer",
        required_uid=required_uid,
        allow_two_links=True,
    )
    if partial_exists and not partial_matches:
        raise FrozenSnapshotOrchestratorError(
            f"existing {label} transfer differs"
        )
    reconciled = False
    if final_exists and partial_exists:
        if (
            final_meta is None
            or partial_meta is None
            or final_meta.st_dev != partial_meta.st_dev
            or final_meta.st_ino != partial_meta.st_ino
            or final_meta.st_nlink != 2
            or partial_meta.st_nlink != 2
        ):
            raise FrozenSnapshotOrchestratorError(
                f"{label} publication identity is ambiguous"
            )
        try:
            partial.unlink()
            _fsync_directory(partial.parent)
        except OSError as exc:
            raise FrozenSnapshotOrchestratorError(
                f"{label} publication residue cannot be reconciled"
            ) from exc
        partial_exists = False
        reconciled = True
        final_exists, final_matches, _metadata = _file_state(
            final,
            digest=digest,
            label=f"{label} final",
            required_uid=required_uid,
        )
    elif final_exists and final_meta is not None and final_meta.st_nlink != 1:
        raise FrozenSnapshotOrchestratorError(
            f"{label} final has a foreign hard link"
        )
    elif (
        partial_exists
        and partial_meta is not None
        and partial_meta.st_nlink != 1
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} transfer has a foreign hard link"
        )
    return final_exists and final_matches, partial_exists, reconciled


def _prepare_host_material(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> dict[str, Any]:
    _ensure_host_directories(request, required_uid=required_uid)
    missing: list[str] = []
    reconciled: list[str] = []
    for key, (final, partial, digest) in _material_paths(request).items():
        final_exists, partial_exists, did_reconcile = _reconcile_material(
            final,
            partial,
            digest=digest,
            label=key.replace("_", " "),
            required_uid=required_uid,
        )
        if not final_exists and not partial_exists:
            missing.append(key)
        if did_reconcile:
            reconciled.append(key)
    return {
        "schema": HOST_PREPARE_SCHEMA,
        "status": "prepared",
        "action": "prepare-material",
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "role": request["role"],
        "lease_claim_sha256": request["lease_claim_sha256"],
        "need_transfer": missing,
        "reconciled": reconciled,
        "docker_contacted": False,
        "production_mutated": False,
    }


def _promote_material_file(
    final: Path,
    partial: Path,
    *,
    digest: str,
    label: str,
    required_uid: int,
) -> str:
    final_exists, partial_exists, _reconciled = _reconcile_material(
        final,
        partial,
        digest=digest,
        label=label,
        required_uid=required_uid,
    )
    if final_exists:
        return "reused"
    if not partial_exists:
        raise FrozenSnapshotOrchestratorError(
            f"{label} transfer is absent"
        )
    try:
        os.link(partial, final, follow_symlinks=False)
        _fsync_directory(final.parent)
    except FileExistsError:
        pass
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} could not be published create-only"
        ) from exc
    final_exists, final_matches, final_meta = _file_state(
        final,
        digest=digest,
        label=f"{label} final",
        required_uid=required_uid,
        allow_two_links=True,
    )
    partial_exists, partial_matches, partial_meta = _file_state(
        partial,
        digest=digest,
        label=f"{label} transfer",
        required_uid=required_uid,
        allow_two_links=True,
    )
    if (
        not final_exists
        or not final_matches
        or not partial_exists
        or not partial_matches
        or final_meta is None
        or partial_meta is None
        or final_meta.st_dev != partial_meta.st_dev
        or final_meta.st_ino != partial_meta.st_ino
        or final_meta.st_nlink != 2
        or partial_meta.st_nlink != 2
    ):
        raise FrozenSnapshotOrchestratorError(
            f"{label} create-only publication differs"
        )
    try:
        partial.unlink()
        _fsync_directory(final.parent)
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{label} transfer cleanup failed"
        ) from exc
    _file_state(
        final,
        digest=digest,
        label=f"{label} final",
        required_uid=required_uid,
    )
    return "created"


def _install_host_material(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> dict[str, str]:
    _ensure_host_directories(request, required_uid=required_uid)
    publications = {}
    for key, (final, partial, digest) in _material_paths(request).items():
        publications[key] = _promote_material_file(
            final,
            partial,
            digest=digest,
            label=key.replace("_", " "),
            required_uid=required_uid,
        )
    binding = _binding(
        Path(str(request["binding_path"])),
        operation_id=str(request["operation_id"]),
        release_sha=str(request["release_sha"]),
        role=str(request["role"]),
    )
    if binding.canonical_sha256 != request["binding_sha256"]:
        raise FrozenSnapshotOrchestratorError(
            "installed frozen-final binding digest differs"
        )
    _load_live_lease_material(request)
    return publications


def _load_live_lease_material(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        claim, observed = NGINX.load_live_lease_claim_material(
            Path(str(request["lease_claim_path"])),
            state_receipt_path=Path(str(request["state_receipt_path"])),
            expected_claim_sha256=str(request["lease_claim_sha256"]),
            expected_state_receipt_sha256=str(
                request["state_receipt_sha256"]
            ),
            operation_id=str(request["operation_id"]),
            release_sha=str(request["release_sha"]),
            release_tree_sha=str(request["release_tree_sha"]),
            aggregate_sha256=str(request["nginx_aggregate_sha256"]),
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "copied Nginx live lease material is invalid"
        ) from exc
    if observed != request["lease_claim_sha256"]:
        raise FrozenSnapshotOrchestratorError(
            "copied Nginx live lease claim digest differs"
        )
    return claim


def _validate_installed_nginx_material(
    request: Mapping[str, Any],
    *,
    required_uid: int,
) -> None:
    for field, digest_field, label, maximum in (
        (
            "nginx_manifest_path",
            "nginx_manifest_sha256",
            "installed Nginx manifest",
            MAX_JSON_BYTES,
        ),
        (
            "nginx_archive_path",
            "nginx_archive_sha256",
            "installed Nginx archive",
            NGINX_GENERATION.MAX_ARCHIVE_BYTES,
        ),
    ):
        observed, _size = _hash_file(
            Path(str(request[field])),
            label=label,
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        )
        if observed != request[digest_field]:
            raise FrozenSnapshotOrchestratorError(
                f"{label} digest differs"
            )


def _freeze_kwargs(
    request: Mapping[str, Any],
    *,
    action: str,
    binding: SOURCE.SnapshotBinding,
) -> dict[str, Any]:
    confirm: str | None = None
    if action == "freeze":
        confirm = FREEZE.confirmation_phrase(
            "freeze",
            binding,
            nginx_aggregate_sha256=str(
                request["nginx_aggregate_sha256"]
            ),
            nginx_manifest_sha256=str(
                request["nginx_manifest_sha256"]
            ),
            coordinated_state_receipt_sha256=str(
                request["state_receipt_sha256"]
            ),
            live_lease_claim_sha256=str(
                request["lease_claim_sha256"]
            ),
        )
    return {
        "binding_path": Path(str(request["binding_path"])),
        "action": action,
        "release_tree_sha": str(request["release_tree_sha"]),
        "nginx_aggregate_sha256": str(
            request["nginx_aggregate_sha256"]
        ),
        "nginx_manifest": Path(str(request["nginx_manifest_path"])),
        "nginx_manifest_sha256": str(
            request["nginx_manifest_sha256"]
        ),
        "nginx_archive": Path(str(request["nginx_archive_path"])),
        "coordinated_state_receipt": Path(
            str(request["state_receipt_path"])
        ),
        "coordinated_state_receipt_sha256": str(
            request["state_receipt_sha256"]
        ),
        "live_lease_claim": Path(str(request["lease_claim_path"])),
        "live_lease_claim_sha256": str(
            request["lease_claim_sha256"]
        ),
        "apply": True,
        "confirm": confirm,
    }


def _call_freeze_worker(
    request: Mapping[str, Any],
    *,
    action: str,
    binding: SOURCE.SnapshotBinding,
    control_fd: int,
    runner: Runner | None,
    paths: Mapping[str, Any],
) -> dict[str, Any]:
    kwargs = _freeze_kwargs(request, action=action, binding=binding)
    arguments = [
        PYTHON,
        "-B",
        str(paths["freeze_worker"]),
        "--binding",
        str(kwargs["binding_path"]),
        "--release-tree-sha",
        str(kwargs["release_tree_sha"]),
        "--nginx-aggregate-sha256",
        str(kwargs["nginx_aggregate_sha256"]),
        "--nginx-manifest",
        str(kwargs["nginx_manifest"]),
        "--nginx-manifest-sha256",
        str(kwargs["nginx_manifest_sha256"]),
        "--nginx-archive",
        str(kwargs["nginx_archive"]),
        "--coordinated-state-receipt",
        str(kwargs["coordinated_state_receipt"]),
        "--coordinated-state-receipt-sha256",
        str(kwargs["coordinated_state_receipt_sha256"]),
        "--live-lease-claim",
        str(kwargs["live_lease_claim"]),
        "--live-lease-claim-sha256",
        str(kwargs["live_lease_claim_sha256"]),
        "--action",
        action,
        "--apply",
        "--control-fd",
        str(control_fd),
    ]
    if kwargs["confirm"] is not None:
        arguments.extend(["--confirm", str(kwargs["confirm"])])
    result = _parse_command_json(
        _run_command(
            arguments,
            runner=runner,
            timeout=2 * 60 * 60,
            allowed=frozenset({PYTHON}),
            pass_fds=(control_fd,),
        ),
        label=f"legacy writer {action} worker",
    )
    expected_statuses = {
        "freeze": {"frozen", "already-frozen"},
        "verify": {"verified-frozen"},
        "verify-current": {"verified-current-frozen"},
    }
    expected_schema = (
        FREEZE.CURRENT_VERIFY_RESULT_SCHEMA
        if action == "verify-current"
        else FREEZE.RESULT_SCHEMA
    )
    if (
        not isinstance(result, dict)
        or result.get("schema") != expected_schema
        or result.get("action") != action
        or result.get("status") not in expected_statuses[action]
        or result.get("operation_id") != request["operation_id"]
        or result.get("release_sha") != request["release_sha"]
        or result.get("role") != request["role"]
        or result.get("binding_sha256") != request["binding_sha256"]
        or result.get("coordinated_state_receipt_sha256")
        != request["state_receipt_sha256"]
        or result.get("nginx_aggregate_sha256")
        != request["nginx_aggregate_sha256"]
        or result.get("nginx_manifest_sha256")
        != request["nginx_manifest_sha256"]
        or result.get("database_container_running") is not True
        or result.get("redis_container_running") is not True
        or result.get("legacy_writer_process_count") != 0
        or result.get("writer_database_client_count") != 0
        or result.get("file_mutator_process_count") != 0
        or (
            action == "verify-current"
            and result.get("production_mutated") is not False
        )
        or type(result.get("production_mutated")) is not bool
    ):
        raise FrozenSnapshotOrchestratorError(
            f"legacy writer {action} result binding differs"
        )
    claim_binding = result.get("live_lease_claim_sha256")
    if claim_binding != request["lease_claim_sha256"]:
        raise FrozenSnapshotOrchestratorError(
            f"legacy writer {action} lease binding differs"
        )
    if (
        type(result.get("live_lease_claim_epoch")) is not int
        or result["live_lease_claim_epoch"] < 1
    ):
        raise FrozenSnapshotOrchestratorError(
            f"legacy writer {action} lease epoch differs"
        )
    return result


def _snapshot_inventory(
    request: Mapping[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    freeze_sha256: str,
    required_uid: int,
) -> dict[str, dict[str, Any]]:
    paths = SOURCE.output_paths(SOURCE_OUTPUT_ROOT, binding)
    try:
        manifest = SOURCE.verify_completed_output(
            paths,
            binding,
            freeze_sha256=freeze_sha256,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final snapshot output is invalid"
        ) from exc
    files: dict[str, dict[str, Any]] = {}
    for name in SNAPSHOT_FILENAMES:
        maximum = (
            MAX_JSON_BYTES
            if name == SOURCE.MANIFEST_FILE
            else MAX_ARTIFACT_BYTES
        )
        digest, size = _hash_file(
            paths.final / name,
            label=f"frozen-final {name}",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        )
        files[name] = {"sha256": digest, "bytes": size}
    return files


def _host_action_result(
    request: Mapping[str, Any],
    *,
    action: str,
    freeze_sha256: str,
    files: Mapping[str, Any] | None = None,
    production_mutated: bool,
    lease_claim_epoch: int,
) -> dict[str, Any]:
    return {
        "schema": HOST_RESULT_SCHEMA,
        "status": {
            "install-material": "material-installed",
            "freeze": "frozen",
            "verify": "verified-frozen",
            "snapshot": "snapshot-created",
        }[action],
        "action": action,
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "role": request["role"],
        "mode": "frozen-final",
        "binding_sha256": request["binding_sha256"],
        "state_receipt_sha256": request["state_receipt_sha256"],
        "lease_claim_sha256": request["lease_claim_sha256"],
        "lease_claim_epoch": lease_claim_epoch,
        "freeze_evidence_sha256": freeze_sha256,
        "files": dict(files or {}),
        "pull_policy": "never",
        "source_mutated": False,
        "current_mutated": False,
        "source_stopped_or_restarted": production_mutated,
        "redis_restored": False,
    }


def _current_verify_host_result(
    request: Mapping[str, Any],
    worker_result: Mapping[str, Any],
) -> dict[str, Any]:
    result = {
        "schema": HOST_CURRENT_VERIFY_SCHEMA,
        "status": "verified-current-frozen",
        "action": "verify-current",
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "role": request["role"],
        "mode": "frozen-final",
        "binding_sha256": request["binding_sha256"],
        "state_receipt_sha256": request["state_receipt_sha256"],
        "readback_challenge_sha256": worker_result[
            "readback_challenge_sha256"
        ],
        "issued_at_epoch": worker_result["issued_at_epoch"],
        "expires_at_epoch": worker_result["expires_at_epoch"],
        "captured_at_epoch": worker_result["captured_at_epoch"],
        "lease_claim_sha256": request["lease_claim_sha256"],
        "lease_claim_epoch": worker_result["live_lease_claim_epoch"],
        "previous_live_lease_claim_sha256": worker_result[
            "previous_live_lease_claim_sha256"
        ],
        "freeze_evidence_live_lease_claim_sha256": worker_result[
            "freeze_evidence_live_lease_claim_sha256"
        ],
        "freeze_evidence_sha256": worker_result[
            "freeze_evidence_sha256"
        ],
        "role_freeze_generation_sha256": worker_result[
            "role_freeze_generation_sha256"
        ],
        "freeze_generation_sha256": worker_result[
            "freeze_generation_sha256"
        ],
        "source_container_ids": worker_result["source_container_ids"],
        "writer_container_ids": worker_result["writer_container_ids"],
        "journal_sha256": worker_result["journal_sha256"],
        "legacy_writer_process_count": worker_result[
            "legacy_writer_process_count"
        ],
        "writer_database_client_count": worker_result[
            "writer_database_client_count"
        ],
        "file_mutator_process_count": worker_result[
            "file_mutator_process_count"
        ],
        "database_container_running": worker_result[
            "database_container_running"
        ],
        "redis_container_running": worker_result[
            "redis_container_running"
        ],
        "pull_policy": "never",
        "source_stopped_or_restarted": worker_result[
            "source_stopped_or_restarted"
        ],
        "source_mutated": False,
        "current_mutated": worker_result["current_mutated"],
        "service_mutated": worker_result["service_mutated"],
        "container_mutated": worker_result["container_mutated"],
        "volume_mutated": worker_result["volume_mutated"],
        "data_mutated": worker_result["data_mutated"],
        "production_mutated": worker_result["production_mutated"],
    }
    return _validate_host_result(result, request=request)


def host_agent(
    encoded_request: str,
    *,
    runner: Runner | None = None,
    required_uid: int = 0,
    agent_path: Path | None = None,
    observed_host_addresses: set[str] | None = None,
    control_fd: int | None = None,
) -> dict[str, Any]:
    if control_fd is None:
        raise FrozenSnapshotOrchestratorError(
            "host action requires controller liveness"
        )
    with ControllerLivenessGuard(control_fd) as liveness:
        return _host_agent_under_liveness(
            encoded_request,
            runner=runner,
            required_uid=required_uid,
            agent_path=agent_path,
            observed_host_addresses=observed_host_addresses,
            controller_liveness=liveness,
        )


def _host_agent_under_liveness(
    encoded_request: str,
    *,
    runner: Runner | None,
    required_uid: int,
    agent_path: Path | None,
    observed_host_addresses: set[str] | None,
    controller_liveness: ControllerLivenessGuard,
) -> dict[str, Any]:
    _controller_liveness = controller_liveness
    _controller_liveness.check()
    request = decode_host_request(encoded_request)
    if os.geteuid() != required_uid or required_uid != 0:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot host agent must run as root"
        )
    role = str(request["role"])
    try:
        FINLAND_STAGE._verify_role_host(
            role,
            observed_host_addresses=observed_host_addresses,
        )
    except FINLAND_STAGE.FinlandStageError as exc:
        raise FrozenSnapshotOrchestratorError(
            "host identity differs from the requested Finland role"
        ) from exc
    paths = canonical_paths(
        str(request["operation_id"]),
        str(request["release_sha"]),
    )
    _validate_exact_release(
        request,
        agent_path=(
            agent_path if agent_path is not None else Path(__file__).resolve()
        ),
        runner=runner,
        required_uid=required_uid,
    )
    action = str(request["action"])
    if action == "prepare-material":
        result = _prepare_host_material(request, required_uid=required_uid)
        _controller_liveness.check()
        return result
    _install_host_material(
        request,
        required_uid=required_uid,
    )
    if action == "install-material":
        claim = _load_live_lease_material(request)
        result = _host_action_result(
            request,
            action=action,
            freeze_sha256=ZERO_SHA256,
            production_mutated=False,
            lease_claim_epoch=claim["claim_epoch"],
        )
        _controller_liveness.check()
        return result
    binding = _binding(
        Path(str(request["binding_path"])),
        operation_id=str(request["operation_id"]),
        release_sha=str(request["release_sha"]),
        role=role,
    )
    _validate_installed_nginx_material(
        request,
        required_uid=required_uid,
    )
    _load_live_lease_material(request)
    if action in {"freeze", "verify", "verify-current"}:
        worker_result = _call_freeze_worker(
            request,
            action=action,
            binding=binding,
            control_fd=_controller_liveness.control_fd,
            runner=runner,
            paths=paths,
        )
        _load_live_lease_material(request)
        freeze_sha256 = _nonzero_sha256(
            worker_result.get("freeze_evidence_sha256"),
            label="freeze evidence SHA-256",
        )
        if action == "verify-current":
            result = _current_verify_host_result(
                request,
                worker_result,
            )
            _controller_liveness.check()
            return result
        result = _host_action_result(
            request,
            action=action,
            freeze_sha256=freeze_sha256,
            production_mutated=action == "freeze"
            and worker_result.get("status") != "already-frozen",
            lease_claim_epoch=worker_result[
                "live_lease_claim_epoch"
            ],
        )
        _controller_liveness.check()
        return result
    if action != "snapshot":
        raise FrozenSnapshotOrchestratorError(
            "host action is not allowlisted"
        )
    try:
        BASE._ensure_host_output_root(required_uid=required_uid)
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "source snapshot output root is unsafe"
        ) from exc
    freeze_path = paths["roles"][role]["freeze_evidence"]
    try:
        evidence, freeze_sha256 = SOURCE.load_freeze_evidence(
            freeze_path,
            binding,
            live_lease_claim_sha256=str(
                request["lease_claim_sha256"]
            ),
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final evidence is invalid"
        ) from exc
    producer_arguments = [
        PYTHON,
        "-B",
        str(paths["producer"]),
        "--binding",
        str(paths["roles"][role]["binding"]),
        "--output-root",
        str(SOURCE_OUTPUT_ROOT),
        "--freeze-evidence",
        str(freeze_path),
        "--live-lease-claim",
        str(request["lease_claim_path"]),
        "--live-lease-claim-sha256",
        str(request["lease_claim_sha256"]),
        "--apply",
        "--control-fd",
        str(_controller_liveness.control_fd),
        "--confirm",
        SOURCE.confirmation_phrase(binding),
    ]
    producer_raw = _run_command(
        producer_arguments,
        runner=runner,
        timeout=8 * 60 * 60,
        allowed=frozenset({PYTHON}),
        pass_fds=(_controller_liveness.control_fd,),
    )
    producer_result = _parse_command_json(
        producer_raw,
        label="frozen-final source snapshot producer",
    )
    if (
        producer_result.get("schema") != SOURCE.MANIFEST_SCHEMA
        or producer_result.get("status")
        not in {"applied", "resume-verified"}
        or producer_result.get("operation_id") != binding.operation_id
        or producer_result.get("role") != role
        or producer_result.get("mode") != "frozen-final"
        or producer_result.get("manifest")
        != str(paths["roles"][role]["manifest"])
        or producer_result.get("zero_residue") is not True
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen-final source producer result differs"
        )
    post_freeze = _call_freeze_worker(
        request,
        action="verify",
        binding=binding,
        control_fd=_controller_liveness.control_fd,
        runner=runner,
        paths=paths,
    )
    if (
        post_freeze["freeze_evidence_sha256"] != freeze_sha256
        or post_freeze["live_lease_claim_sha256"]
        != request["lease_claim_sha256"]
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen-final post-producer freeze verification differs"
        )
    final_claim = _load_live_lease_material(request)
    if (
        final_claim.get("claim_epoch")
        != post_freeze["live_lease_claim_epoch"]
    ):
        raise FrozenSnapshotOrchestratorError(
            "post-producer live lease epoch differs"
        )
    if (
        evidence.get("live_lease_claim_sha256")
        != request["lease_claim_sha256"]
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen-final evidence lease binding differs"
        )
    files = _snapshot_inventory(
        request,
        binding=binding,
        freeze_sha256=freeze_sha256,
        required_uid=required_uid,
    )
    result = _host_action_result(
        request,
        action="snapshot",
        freeze_sha256=freeze_sha256,
        files=files,
        production_mutated=False,
        lease_claim_epoch=post_freeze["live_lease_claim_epoch"],
    )
    _controller_liveness.check()
    return result


def _validate_prepare_result(
    document: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != HOST_PREPARE_FIELDS
        or document["schema"] != HOST_PREPARE_SCHEMA
        or document["status"] != "prepared"
        or document["action"] != "prepare-material"
        or any(
            document.get(field) != request[field]
            for field in (
                "operation_id",
                "release_sha",
                "role",
                "lease_claim_sha256",
            )
        )
        or not isinstance(document["need_transfer"], list)
        or any(key not in MATERIAL_KEYS for key in document["need_transfer"])
        or len(document["need_transfer"])
        != len(set(document["need_transfer"]))
        or document["need_transfer"]
        != [
            key for key in MATERIAL_KEYS if key in document["need_transfer"]
        ]
        or not isinstance(document["reconciled"], list)
        or any(key not in MATERIAL_KEYS for key in document["reconciled"])
        or document["reconciled"]
        != [key for key in MATERIAL_KEYS if key in document["reconciled"]]
        or document["docker_contacted"] is not False
        or document["production_mutated"] is not False
    ):
        raise FrozenSnapshotOrchestratorError(
            "host material prepare result differs"
        )
    return dict(document)


def _validate_host_result(
    document: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    expected_claim_epoch: int | None = None,
) -> dict[str, Any]:
    action = str(request["action"])
    if action == "verify-current":
        identity = {
            "operation_id": request["operation_id"],
            "release_sha": request["release_sha"],
            "release_tree_sha": request["release_tree_sha"],
            "role": request["role"],
            "binding_sha256": request["binding_sha256"],
            "state_receipt_sha256": request["state_receipt_sha256"],
            "lease_claim_sha256": request["lease_claim_sha256"],
        }
        source_keys = set(SOURCE.SOURCE_CONTAINERS)
        writer_keys = {
            kind
            for kind, _name, _service in FREEZE.ROLE_WRITERS[
                str(request["role"])
            ]
        }
        if (
            not isinstance(document, dict)
            or set(document) != HOST_CURRENT_VERIFY_FIELDS
            or document["schema"] != HOST_CURRENT_VERIFY_SCHEMA
            or document["status"] != "verified-current-frozen"
            or document["action"] != action
            or document["mode"] != "frozen-final"
            or any(
                document.get(field) != value
                for field, value in identity.items()
            )
            or document["pull_policy"] != "never"
            or type(document["lease_claim_epoch"]) is not int
            or document["lease_claim_epoch"] < 2
            or (
                expected_claim_epoch is not None
                and document["lease_claim_epoch"]
                != expected_claim_epoch
            )
            or type(document["issued_at_epoch"]) is not int
            or type(document["expires_at_epoch"]) is not int
            or type(document["captured_at_epoch"]) is not int
            or not (
                1
                <= document["issued_at_epoch"]
                <= document["captured_at_epoch"]
                <= document["expires_at_epoch"]
            )
            or document["legacy_writer_process_count"] != 0
            or document["writer_database_client_count"] != 0
            or document["file_mutator_process_count"] != 0
            or document["database_container_running"] is not True
            or document["redis_container_running"] is not True
            or document["source_stopped_or_restarted"] is not False
            or any(
                document[field] is not False
                for field in (
                    "source_mutated",
                    "current_mutated",
                    "service_mutated",
                    "container_mutated",
                    "volume_mutated",
                    "data_mutated",
                    "production_mutated",
                )
            )
            or not isinstance(document["source_container_ids"], dict)
            or set(document["source_container_ids"]) != source_keys
            or not isinstance(document["writer_container_ids"], dict)
            or set(document["writer_container_ids"]) != writer_keys
        ):
            raise FrozenSnapshotOrchestratorError(
                "host verify-current result differs"
            )
        for field in (
            "state_receipt_sha256",
            "readback_challenge_sha256",
            "lease_claim_sha256",
            "previous_live_lease_claim_sha256",
            "freeze_evidence_live_lease_claim_sha256",
            "freeze_evidence_sha256",
            "role_freeze_generation_sha256",
            "freeze_generation_sha256",
            "journal_sha256",
        ):
            _nonzero_sha256(
                document[field],
                label=f"verify-current {field}",
            )
        for group in (
            document["source_container_ids"],
            document["writer_container_ids"],
        ):
            if any(
                not isinstance(value, str)
                or FREEZE.CONTAINER_ID_RE.fullmatch(value) is None
                or value == ZERO_SHA256
                for value in group.values()
            ):
                raise FrozenSnapshotOrchestratorError(
                    "host verify-current container identity differs"
                )
        return json.loads(canonical_json(document).decode("ascii"))

    expected_status = {
        "install-material": "material-installed",
        "freeze": "frozen",
        "verify": "verified-frozen",
        "snapshot": "snapshot-created",
    }.get(action)
    if (
        expected_status is None
        or not isinstance(document, dict)
        or set(document) != HOST_RESULT_FIELDS
        or document["schema"] != HOST_RESULT_SCHEMA
        or document["status"] != expected_status
        or document["action"] != action
        or document["release_tree_sha"] != request["release_tree_sha"]
        or document["mode"] != "frozen-final"
        or any(
            document.get(field) != request[field]
            for field in (
                "operation_id",
                "release_sha",
                "role",
                "binding_sha256",
                "state_receipt_sha256",
                "lease_claim_sha256",
            )
        )
        or document["pull_policy"] != "never"
        or type(document["lease_claim_epoch"]) is not int
        or document["lease_claim_epoch"] < 1
        or (
            expected_claim_epoch is not None
            and document["lease_claim_epoch"] != expected_claim_epoch
        )
        or document["source_mutated"] is not False
        or document["current_mutated"] is not False
        or type(document["source_stopped_or_restarted"]) is not bool
        or document["redis_restored"] is not False
        or not isinstance(document["files"], dict)
    ):
        raise FrozenSnapshotOrchestratorError(
            f"host {action} result differs"
        )
    if action == "install-material":
        if (
            document["freeze_evidence_sha256"] != ZERO_SHA256
            or document["files"]
            or document["source_mutated"]
            or document["source_stopped_or_restarted"]
        ):
            raise FrozenSnapshotOrchestratorError(
                "host material installation result differs"
            )
    else:
        _nonzero_sha256(
            document["freeze_evidence_sha256"],
            label="freeze evidence SHA-256",
        )
        if action == "snapshot":
            if set(document["files"]) != set(SNAPSHOT_FILENAMES):
                raise FrozenSnapshotOrchestratorError(
                    "host frozen-final file set differs"
                )
            for name, row in document["files"].items():
                maximum = (
                    MAX_JSON_BYTES
                    if name == SOURCE.MANIFEST_FILE
                    else MAX_ARTIFACT_BYTES
                )
                if (
                    not isinstance(row, dict)
                    or set(row) != FILE_RESULT_FIELDS
                ):
                    raise FrozenSnapshotOrchestratorError(
                        "host frozen-final file inventory differs"
                    )
                _nonzero_sha256(
                    row["sha256"],
                    label=f"{name} SHA-256",
                )
                _bounded_bytes(
                    row["bytes"],
                    label=f"{name} bytes",
                    maximum=maximum,
                )
        elif document["files"]:
            raise FrozenSnapshotOrchestratorError(
                f"host {action} unexpectedly returned files"
            )
    return json.loads(canonical_json(document).decode("ascii"))


def _invoke_host(
    *,
    role: str,
    request: Mapping[str, Any],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    known_hosts: Path,
    runner: Runner | None,
) -> dict[str, Any]:
    arguments = [
        PYTHON,
        "-B",
        str(paths["agent"]),
        "--host-request-b64",
        encode_host_request(request),
        "--control-fd",
        "0",
    ]
    if role == "webapp_fi":
        arguments = ssh_arguments(
            ssh_identity,
            known_hosts=known_hosts,
            remote_arguments=arguments,
        )
        allowed = frozenset({SSH})
    else:
        allowed = frozenset({PYTHON})
    control_read_fd = -1
    control_write_fd = -1
    try:
        control_read_fd, control_write_fd = os.pipe()
        os.set_inheritable(control_read_fd, False)
        os.set_inheritable(control_write_fd, False)
        raw = _run_command(
            arguments,
            runner=runner,
            timeout=8 * 60 * 60,
            allowed=allowed,
            stdin=control_read_fd,
        )
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "controller liveness pipe could not be created"
        ) from exc
    finally:
        for descriptor in (control_read_fd, control_write_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return _parse_command_json(raw, label=f"{role} frozen host agent")


def _leased_call(
    lease: Any,
    *,
    label: str,
    call: Callable[[], Any],
    checkpoint: Checkpoint,
    authorization: AuthorizationCheck | None = None,
) -> Any:
    if authorization is not None:
        authorization()
    lease.verify()
    checkpoint(f"before-rpc:{label}")
    if authorization is not None:
        authorization()
    result = call()
    lease.verify()
    checkpoint(f"after-rpc:{label}")
    return result


def _copy_local_material(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    required_uid: int,
) -> None:
    digest, size = _hash_file(
        source,
        label="controller material source",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JSON_BYTES,
    )
    if digest != expected_sha256:
        raise FrozenSnapshotOrchestratorError(
            "controller material source digest differs"
        )
    try:
        BASE._copy_local_partial(
            source,
            destination,
            expected_sha256=digest,
            expected_bytes=size,
            required_uid=required_uid,
            maximum=MAX_JSON_BYTES,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "local material transfer failed closed"
        ) from exc


def _transfer_material(
    *,
    role: str,
    key: str,
    request: Mapping[str, Any],
    source: Path,
    ssh_identity: Path,
    known_hosts: Path,
    runner: Runner | None,
    required_uid: int,
) -> None:
    final, partial, digest = _material_paths(request)[key]
    del final
    if role == "bot_fi":
        _copy_local_material(
            source,
            partial,
            expected_sha256=digest,
            required_uid=required_uid,
        )
        return
    source_digest, _source_bytes = _hash_file(
        source,
        label=f"{key} upload source",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JSON_BYTES,
    )
    if source_digest != digest:
        raise FrozenSnapshotOrchestratorError(
            f"{key} upload source digest differs"
        )
    _run_command(
        scp_upload_arguments(
            ssh_identity,
            known_hosts=known_hosts,
            source=source,
            remote_destination=partial,
            operation_id=str(request["operation_id"]),
        ),
        runner=runner,
        timeout=30 * 60,
        allowed=frozenset({SCP}),
    )


def _prepare_collection_partial(
    path: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    maximum: int,
) -> bool:
    try:
        return BASE._prepare_collection_partial(
            path,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            required_uid=required_uid,
            maximum=maximum,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "collection transfer partial is unsafe"
        ) from exc


def _publish_collection_file(
    partial: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    required_uid: int,
    maximum: int,
) -> str:
    try:
        return BASE._publish_collection_file(
            partial,
            destination,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            required_uid=required_uid,
            maximum=maximum,
        )
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "collection publication failed closed"
        ) from exc


def _collect_file(
    *,
    role: str,
    name: str,
    row: Mapping[str, Any],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    known_hosts: Path,
    runner: Runner | None,
    required_uid: int,
) -> str:
    maximum = (
        MAX_JSON_BYTES
        if name == SOURCE.MANIFEST_FILE
        else MAX_ARTIFACT_BYTES
    )
    digest = _nonzero_sha256(
        row["sha256"],
        label=f"{role} {name} SHA-256",
    )
    size = _bounded_bytes(
        row["bytes"],
        label=f"{role} {name} bytes",
        maximum=maximum,
    )
    source = paths["roles"][role]["snapshot"] / name
    destination = paths["roles"][role]["collection"] / name
    partial = destination.with_name(f".{name}.transfer")
    try:
        exists = bool(os.lstat(destination))
    except FileNotFoundError:
        exists = False
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "collection destination cannot be inspected"
        ) from exc
    if exists:
        observed = _hash_file(
            destination,
            label="collected frozen-final artifact",
            required_uid=required_uid,
            expected_mode=0o600,
            maximum=maximum,
        )
        if observed != (digest, size):
            raise FrozenSnapshotOrchestratorError(
                "existing collected frozen-final artifact differs"
            )
        return "reused"
    partial_ready = _prepare_collection_partial(
        partial,
        expected_sha256=digest,
        expected_bytes=size,
        required_uid=required_uid,
        maximum=maximum,
    )
    if not partial_ready:
        if role == "bot_fi":
            try:
                BASE._copy_local_partial(
                    source,
                    partial,
                    expected_sha256=digest,
                    expected_bytes=size,
                    required_uid=required_uid,
                    maximum=maximum,
                )
            except BASE.FinlandSourceSnapshotOrchestratorError as exc:
                raise FrozenSnapshotOrchestratorError(
                    "local frozen-final collection failed closed"
                ) from exc
        else:
            _run_command(
                scp_download_arguments(
                    ssh_identity,
                    known_hosts=known_hosts,
                    remote_source=source,
                    destination=partial,
                    operation_id=str(paths["operation_id"]),
                ),
                runner=runner,
                timeout=8 * 60 * 60,
                allowed=frozenset({SCP}),
            )
    if _hash_file(
        partial,
        label="frozen-final collection transfer",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=maximum,
    ) != (digest, size):
        raise FrozenSnapshotOrchestratorError(
            "frozen-final collection transfer differs"
        )
    return _publish_collection_file(
        partial,
        destination,
        expected_sha256=digest,
        expected_bytes=size,
        required_uid=required_uid,
        maximum=maximum,
    )


def _verify_collected_role(
    *,
    role: str,
    binding: SOURCE.SnapshotBinding,
    freeze_sha256: str,
    lease_claim_sha256: str,
    paths: Mapping[str, Any],
) -> dict[str, Any]:
    collection = paths["roles"][role]["collection"]
    output_paths = SOURCE.OutputPaths(
        operation_root=collection.parent.parent,
        role_root=collection.parent,
        final=collection,
        staging=collection / ".unused",
        manifest=collection / SOURCE.MANIFEST_FILE,
    )
    try:
        manifest = SOURCE.verify_completed_output(
            output_paths,
            binding,
            freeze_sha256=freeze_sha256,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise FrozenSnapshotOrchestratorError(
            f"{role} collected frozen-final snapshot is invalid"
        ) from exc
    files: dict[str, dict[str, Any]] = {}
    for name in SNAPSHOT_FILENAMES:
        maximum = (
            MAX_JSON_BYTES
            if name == SOURCE.MANIFEST_FILE
            else MAX_ARTIFACT_BYTES
        )
        digest, size = _hash_file(
            collection / name,
            label=f"{role} collected {name}",
            expected_mode=0o600,
            maximum=maximum,
        )
        files[name] = {"sha256": digest, "bytes": size}
    return {
        "freeze_evidence_sha256": freeze_sha256,
        "lease_claim_sha256": lease_claim_sha256,
        "manifest_binding_sha256": binding.canonical_sha256,
        "files": files,
    }


def _state_sha256(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned["state_sha256"] = ZERO_SHA256
    return _hash_payload(canonical_json(unsigned))


def _event_sha256(event: Mapping[str, Any]) -> str:
    unsigned = dict(event)
    unsigned["event_sha256"] = ZERO_SHA256
    return _hash_payload(canonical_json(unsigned))


def _append_event(
    journal: dict[str, Any],
    *,
    kind: str,
    role: str | None,
    details: Mapping[str, Any],
) -> None:
    if (
        re.fullmatch(r"[a-z][a-z0-9-]{0,63}", kind) is None
        or role not in {None, *ROLES}
    ):
        raise FrozenSnapshotOrchestratorError(
            "journal event identity is invalid"
        )
    event = {
        "schema": JOURNAL_EVENT_SCHEMA,
        "sequence": len(journal["events"]) + 1,
        "kind": kind,
        "role": role,
        "details_sha256": _hash_payload(canonical_json(details)),
        "previous_event_sha256": journal["event_tail_sha256"],
        "event_sha256": ZERO_SHA256,
    }
    event["event_sha256"] = _event_sha256(event)
    journal["events"].append(event)
    journal["event_tail_sha256"] = event["event_sha256"]
    journal["state_sha256"] = _state_sha256(journal)


def _role_state() -> dict[str, Any]:
    return {
        "phase": "pending",
        "freeze_evidence_sha256": None,
        "snapshot": None,
        "collection": None,
    }


def _initial_journal(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
) -> dict[str, Any]:
    document = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "state_receipt_sha256": state_receipt_sha256,
        "bindings": {
            role: bindings[role].canonical_sha256 for role in ROLES
        },
        "public_phase_handoff": None,
        "lease": None,
        "status": "prepared",
        "roles": {role: _role_state() for role in ROLES},
        "outcome_sha256": None,
        "consumption_sha256": None,
        "events": [],
        "event_tail_sha256": ZERO_SHA256,
        "state_sha256": ZERO_SHA256,
    }
    document["state_sha256"] = _state_sha256(document)
    return document


def _validate_public_phase_handoff(
    value: Any,
    *,
    inputs: NGINX.CoordinatorInputs,
) -> None:
    if value is None:
        return
    fields = {
        "schema",
        "status",
        "phase",
        "manifest_sha256",
        "plan_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "nginx_aggregate_sha256",
        "approval_sha256",
        "approval_policy_sha256",
        "prestart_journal_state_sha256",
        "prestart_journal_event_tail_sha256",
        "prestart_journal_event_count",
        "started_journal_state_sha256",
        "started_journal_event_tail_sha256",
        "started_journal_event_count",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value["schema"] != PUBLIC_PHASE_HANDOFF_SCHEMA
        or value["status"] not in {"intent", "started"}
        or value["phase"] != PUBLIC_PHASE
        or value["operation_id"] != inputs.operation_id
        or value["release_sha"] != inputs.release_sha
        or value["nginx_aggregate_sha256"] != inputs.aggregate_sha256
        or value["campaign_id"] == inputs.operation_id
        or _canonical_uuid4(value["operation_id"]) != inputs.operation_id
        or _release_sha(value["release_sha"]) != inputs.release_sha
        or _release_sha(value["legacy_release_sha"], label="legacy release SHA")
        == inputs.release_sha
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot public phase handoff differs"
        )
    try:
        CONTROLLER._canonical_campaign_id(value["campaign_id"])  # noqa: SLF001
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot public phase handoff campaign differs"
        ) from exc
    for field in (
        "manifest_sha256",
        "plan_sha256",
        "nginx_aggregate_sha256",
        "approval_sha256",
        "approval_policy_sha256",
        "prestart_journal_state_sha256",
        "prestart_journal_event_tail_sha256",
    ):
        _nonzero_sha256(value[field], label=f"public handoff {field}")
    if (
        type(value["prestart_journal_event_count"]) is not int
        or value["prestart_journal_event_count"] < 1
    ):
        raise FrozenSnapshotOrchestratorError(
            "public handoff prestart journal event count differs"
        )
    if value["status"] == "intent":
        if any(
            value[field] is not None
            for field in (
                "started_journal_state_sha256",
                "started_journal_event_tail_sha256",
                "started_journal_event_count",
            )
        ):
            raise FrozenSnapshotOrchestratorError(
                "public handoff intent contains a premature public start"
            )
        return
    for field in (
        "started_journal_state_sha256",
        "started_journal_event_tail_sha256",
    ):
        _nonzero_sha256(value[field], label=f"public handoff {field}")
    if (
        type(value["started_journal_event_count"]) is not int
        or value["started_journal_event_count"]
        != value["prestart_journal_event_count"] + 1
    ):
        raise FrozenSnapshotOrchestratorError(
            "public handoff started journal event count differs"
        )


def _validate_event_chain(events: Any, tail: Any) -> None:
    if not isinstance(events, list) or len(events) > 100_000:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal event list is invalid"
        )
    previous = ZERO_SHA256
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event)
            != {
                "schema",
                "sequence",
                "kind",
                "role",
                "details_sha256",
                "previous_event_sha256",
                "event_sha256",
            }
            or event["schema"] != JOURNAL_EVENT_SCHEMA
            or event["sequence"] != index
            or not isinstance(event["kind"], str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", event["kind"])
            is None
            or event["role"] not in {None, *ROLES}
            or event["previous_event_sha256"] != previous
        ):
            raise FrozenSnapshotOrchestratorError(
                "frozen snapshot journal event chain differs"
            )
        _nonzero_sha256(
            event["details_sha256"],
            label="journal event details SHA-256",
        )
        if event["event_sha256"] != _event_sha256(event):
            raise FrozenSnapshotOrchestratorError(
                "frozen snapshot journal event digest differs"
            )
        previous = event["event_sha256"]
    if tail != previous:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal event tail differs"
        )


def _validate_snapshot_inventory(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "freeze_evidence_sha256",
            "lease_claim_sha256",
            "files",
        }
    ):
        raise FrozenSnapshotOrchestratorError(
            "journaled host snapshot inventory differs"
        )
    _nonzero_sha256(
        value["freeze_evidence_sha256"],
        label="journal freeze evidence SHA-256",
    )
    _nonzero_sha256(
        value["lease_claim_sha256"],
        label="journal lease claim SHA-256",
    )
    if not isinstance(value["files"], dict) or set(value["files"]) != set(
        SNAPSHOT_FILENAMES
    ):
        raise FrozenSnapshotOrchestratorError(
            "journaled frozen-final file set differs"
        )
    for name, row in value["files"].items():
        maximum = (
            MAX_JSON_BYTES
            if name == SOURCE.MANIFEST_FILE
            else MAX_ARTIFACT_BYTES
        )
        if not isinstance(row, dict) or set(row) != FILE_RESULT_FIELDS:
            raise FrozenSnapshotOrchestratorError(
                "journaled frozen-final file row differs"
            )
        _nonzero_sha256(row["sha256"], label=f"{name} SHA-256")
        _bounded_bytes(row["bytes"], label=f"{name} bytes", maximum=maximum)


def _validate_collection(value: Any) -> None:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "freeze_evidence_sha256",
            "lease_claim_sha256",
            "manifest_binding_sha256",
            "files",
        }
    ):
        raise FrozenSnapshotOrchestratorError(
            "journaled collection differs"
        )
    _nonzero_sha256(
        value["freeze_evidence_sha256"],
        label="collection freeze evidence SHA-256",
    )
    _nonzero_sha256(
        value["lease_claim_sha256"],
        label="collection lease claim SHA-256",
    )
    _nonzero_sha256(
        value["manifest_binding_sha256"],
        label="collection binding SHA-256",
    )
    _validate_snapshot_inventory(
        {
            "freeze_evidence_sha256": value[
                "freeze_evidence_sha256"
            ],
            "lease_claim_sha256": value["lease_claim_sha256"],
            "files": value["files"],
        }
    )


def _validate_journal(
    document: Mapping[str, Any],
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "nginx_aggregate_sha256",
        "state_receipt_sha256",
        "bindings",
        "public_phase_handoff",
        "lease",
        "status",
        "roles",
        "outcome_sha256",
        "consumption_sha256",
        "events",
        "event_tail_sha256",
        "state_sha256",
    }
    identity = {
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "state_receipt_sha256": state_receipt_sha256,
    }
    binding_digests = {
        role: bindings[role].canonical_sha256 for role in ROLES
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document["schema"] != JOURNAL_SCHEMA
        or any(document.get(key) != value for key, value in identity.items())
        or document["bindings"] != binding_digests
        or document["status"]
        not in {
            "prepared",
            "active",
            "ready-to-consume",
            "complete",
            "reconciliation-required",
        }
        or not isinstance(document["roles"], dict)
        or set(document["roles"]) != set(ROLES)
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal identity or fields differ"
        )
    _validate_public_phase_handoff(
        document["public_phase_handoff"],
        inputs=inputs,
    )
    lease = document["lease"]
    if lease is not None:
        if (
            not isinstance(lease, dict)
            or set(lease) != {"claim_path", "claim_sha256", "claim_epoch"}
            or not isinstance(lease["claim_path"], str)
            or type(lease["claim_epoch"]) is not int
            or lease["claim_epoch"] < 1
        ):
            raise FrozenSnapshotOrchestratorError(
                "frozen snapshot journal lease binding differs"
            )
        claim_sha256 = _nonzero_sha256(
            lease["claim_sha256"],
            label="journal lease claim SHA-256",
        )
        expected_claim = canonical_paths(
            inputs.operation_id,
            inputs.release_sha,
            lease_claim_sha256=claim_sha256,
        )["lease_claim"]
        if lease["claim_path"] != str(expected_claim):
            raise FrozenSnapshotOrchestratorError(
                "frozen snapshot journal lease path differs"
            )
    for role in ROLES:
        row = document["roles"][role]
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "phase",
                "freeze_evidence_sha256",
                "snapshot",
                "collection",
            }
            or row["phase"] not in ROLE_PHASES
        ):
            raise FrozenSnapshotOrchestratorError(
                f"{role} journal phase differs"
            )
        phase_index = ROLE_PHASES.index(row["phase"])
        if phase_index < ROLE_PHASES.index("frozen"):
            if (
                row["freeze_evidence_sha256"] is not None
                or row["snapshot"] is not None
                or row["collection"] is not None
            ):
                raise FrozenSnapshotOrchestratorError(
                    f"{role} journal contains premature evidence"
                )
        else:
            _nonzero_sha256(
                row["freeze_evidence_sha256"],
                label=f"{role} freeze evidence SHA-256",
            )
        if phase_index >= ROLE_PHASES.index("snapshotted"):
            _validate_snapshot_inventory(row["snapshot"])
        elif row["snapshot"] is not None:
            raise FrozenSnapshotOrchestratorError(
                f"{role} journal contains a premature snapshot"
            )
        if phase_index >= ROLE_PHASES.index("collected"):
            _validate_collection(row["collection"])
        elif row["collection"] is not None:
            raise FrozenSnapshotOrchestratorError(
                f"{role} journal contains a premature collection"
            )
    _validate_event_chain(
        document["events"],
        document["event_tail_sha256"],
    )
    if document["state_sha256"] != _state_sha256(document):
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal state digest differs"
        )
    if document["outcome_sha256"] is not None:
        _nonzero_sha256(
            document["outcome_sha256"],
            label="journal outcome SHA-256",
        )
    if document["consumption_sha256"] is not None:
        _nonzero_sha256(
            document["consumption_sha256"],
            label="journal consumption SHA-256",
        )
    if document["lease"] is not None and document["public_phase_handoff"] is None:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal lease lacks a public phase handoff"
        )
    if document["status"] in {"ready-to-consume", "complete"} and (
        document["lease"] is None
        or document["outcome_sha256"] is None
        or any(
            document["roles"][role]["phase"] != "collected"
            for role in ROLES
        )
    ):
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal completion closure differs"
        )
    if (
        document["status"] == "complete"
        and document["consumption_sha256"] is None
    ):
        raise FrozenSnapshotOrchestratorError(
            "complete frozen snapshot journal lacks consumption"
        )
    return json.loads(canonical_json(document).decode("ascii"))


def _write_private_atomic(
    path: Path,
    document: Mapping[str, Any],
    *,
    required_uid: int,
    create: bool,
) -> None:
    payload = canonical_json(document)
    if not 1 <= len(payload) <= MAX_JOURNAL_BYTES:
        raise FrozenSnapshotOrchestratorError(
            "private controller document is oversized"
        )
    _assert_directory(
        path.parent,
        label="private controller document parent",
        required_uid=required_uid,
        private=True,
    )
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short controller document write")
            view = view[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if create:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise FrozenSnapshotOrchestratorError(
                    "private controller document already exists"
                ) from exc
        else:
            metadata = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != required_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                raise FrozenSnapshotOrchestratorError(
                    "private controller document is unsafe"
                )
            os.replace(temporary, path)
        _fsync_directory(path.parent)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(path.parent)
    except FrozenSnapshotOrchestratorError:
        raise
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "private controller document write failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except OSError:
            pass


def _reconcile_create_link(
    path: Path,
    *,
    required_uid: int,
) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "private controller document cannot be inspected"
        ) from exc
    if metadata.st_nlink == 1:
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != required_uid
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 2
    ):
        raise FrozenSnapshotOrchestratorError(
            "private controller document link state is unsafe"
        )
    pattern = re.compile(
        rf"^\.{re.escape(path.name)}\.[1-9][0-9]*\."
        r"[0-9a-f]{16}\.tmp$"
    )
    matches: list[Path] = []
    try:
        entries = list(path.parent.iterdir())
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "private controller directory cannot be inspected"
        ) from exc
    for candidate in entries:
        if pattern.fullmatch(candidate.name) is None:
            continue
        try:
            candidate_metadata = candidate.stat(follow_symlinks=False)
        except OSError as exc:
            raise FrozenSnapshotOrchestratorError(
                "private controller publication residue is unavailable"
            ) from exc
        if (
            stat.S_ISREG(candidate_metadata.st_mode)
            and candidate_metadata.st_uid == required_uid
            and candidate_metadata.st_gid == 0
            and stat.S_IMODE(candidate_metadata.st_mode) == 0o600
            and candidate_metadata.st_dev == metadata.st_dev
            and candidate_metadata.st_ino == metadata.st_ino
            and candidate_metadata.st_nlink == 2
        ):
            matches.append(candidate)
    if len(matches) != 1:
        raise FrozenSnapshotOrchestratorError(
            "private controller publication residue is ambiguous"
        )
    try:
        matches[0].unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "private controller publication residue cannot be reconciled"
        ) from exc


def _read_journal(
    path: Path,
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    required_uid: int,
) -> dict[str, Any] | None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal cannot be inspected"
        ) from exc
    _reconcile_create_link(path, required_uid=required_uid)
    raw = _read_file(
        path,
        label="frozen snapshot journal",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JOURNAL_BYTES,
    )
    document = _strict_json(raw, label="frozen snapshot journal")
    if raw != canonical_json(document):
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot journal is not canonical JSON"
        )
    return _validate_journal(
        document,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
    )


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    required_uid: int,
    create: bool,
) -> None:
    journal["state_sha256"] = _state_sha256(journal)
    _validate_journal(
        journal,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
    )
    _write_private_atomic(
        path,
        journal,
        required_uid=required_uid,
        create=create,
    )


@contextmanager
def _controller_lock(
    path: Path,
    *,
    required_uid: int,
) -> Iterator[None]:
    try:
        with BASE._controller_lock(path, required_uid=required_uid):
            yield
    except BASE.FinlandSourceSnapshotOrchestratorError as exc:
        raise FrozenSnapshotOrchestratorError(str(exc)) from exc


def _ensure_controller_directories(
    paths: Mapping[str, Any],
    *,
    required_uid: int,
) -> None:
    _assert_directory(
        paths["operation_secret"],
        label="operation secret root",
        required_uid=required_uid,
        private=True,
    )
    _assert_directory(
        paths["controller_root"],
        label="controller secret root",
        required_uid=required_uid,
        private=True,
    )
    _ensure_private_child(
        paths["results"],
        parent=paths["controller_root"],
        label="controller frozen-final results",
        required_uid=required_uid,
    )
    snapshot_root = paths["controller_root"] / "source-snapshots"
    _ensure_private_child(
        snapshot_root,
        parent=paths["controller_root"],
        label="controller source snapshot root",
        required_uid=required_uid,
    )
    _ensure_private_child(
        paths["collection_root"],
        parent=snapshot_root,
        label="controller frozen-final collection",
        required_uid=required_uid,
    )
    for role in ROLES:
        _ensure_private_child(
            paths["roles"][role]["collection"],
            parent=paths["collection_root"],
            label=f"{role} frozen-final collection",
            required_uid=required_uid,
        )


def _persist_outcome(
    path: Path,
    document: Mapping[str, Any],
    *,
    required_uid: int,
) -> str:
    payload = canonical_json(document)
    digest = _hash_payload(payload)
    _nonzero_sha256(digest, label="frozen-final outcome SHA-256")
    try:
        os.lstat(path)
    except FileNotFoundError:
        _write_private_atomic(
            path,
            document,
            required_uid=required_uid,
            create=True,
        )
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final outcome cannot be inspected"
        ) from exc
    _reconcile_create_link(path, required_uid=required_uid)
    observed = _read_file(
        path,
        label="frozen-final outcome",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JOURNAL_BYTES,
    )
    if observed != payload or _hash_payload(observed) != digest:
        raise FrozenSnapshotOrchestratorError(
            "existing frozen-final outcome differs"
        )
    return digest


def _outcome_document(
    *,
    inputs: NGINX.CoordinatorInputs,
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    if journal["lease"] is None:
        raise FrozenSnapshotOrchestratorError(
            "outcome requires a held live lease binding"
        )
    return {
        "schema": OUTCOME_SCHEMA,
        "status": "frozen-final-collected",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "state_receipt_sha256": journal["state_receipt_sha256"],
        "lease_claim_sha256": journal["lease"]["claim_sha256"],
        "lease_claim_epoch": journal["lease"]["claim_epoch"],
        "roles": {
            role: journal["roles"][role]["collection"] for role in ROLES
        },
        "legacy_writers_frozen": True,
        "automatic_restore_performed": False,
        "next_owner": "shadow-readonly-handoff",
    }


def _controller_result(
    *,
    inputs: NGINX.CoordinatorInputs,
    paths: Mapping[str, Any],
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = journal.get("public_phase_handoff")
    _validate_public_phase_handoff(handoff, inputs=inputs)
    if not isinstance(handoff, dict) or handoff["status"] != "started":
        raise FrozenSnapshotOrchestratorError(
            "completed frozen snapshot journal lacks a public phase start"
        )
    handoff_sha256 = _hash_payload(canonical_json(handoff))
    return {
        "schema": RESULT_SCHEMA,
        "status": "complete",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "state_receipt_sha256": journal["state_receipt_sha256"],
        "lease_claim_sha256": journal["lease"]["claim_sha256"],
        "outcome_sha256": journal["outcome_sha256"],
        "consumption_sha256": journal["consumption_sha256"],
        "roles": {
            role: {
                "host": ROLE_HOSTS[role],
                "transport": ROLE_TRANSPORTS[role],
                "binding_sha256": journal["bindings"][role],
                **journal["roles"][role]["collection"],
            }
            for role in ROLES
        },
        "collection_root": str(paths["collection_root"]),
        "outcome_path": str(paths["outcome"]),
        "journal_path": str(paths["journal"]),
        "journal_state_sha256": journal["state_sha256"],
        "public_phase": PUBLIC_PHASE,
        "public_phase_handoff_sha256": handoff_sha256,
        "public_phase_start_journal_state_sha256": handoff[
            "started_journal_state_sha256"
        ],
        "public_phase_start_journal_event_tail_sha256": handoff[
            "started_journal_event_tail_sha256"
        ],
        "public_phase_start_journal_event_count": handoff[
            "started_journal_event_count"
        ],
        "live_lease_outcome": "handoff-shadow-readonly",
        "legacy_writers_frozen": True,
        "automatic_restore_performed": False,
        "pull_policy": "never",
        "build_performed": False,
        "object_storage_used": False,
        "wa_contacted": False,
    }


def _persist_controller_result(
    *,
    paths: Mapping[str, Any],
    result: Mapping[str, Any],
    required_uid: int,
) -> Path:
    payload = canonical_json(result)
    digest = _hash_payload(payload)
    _nonzero_sha256(digest, label="frozen snapshot coordinator result SHA-256")
    path = paths["results"] / f"{RESULT_PREFIX}.{digest}.json"
    try:
        os.lstat(path)
    except FileNotFoundError:
        _write_private_atomic(
            path,
            result,
            required_uid=required_uid,
            create=True,
        )
    except OSError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen snapshot coordinator result cannot be inspected"
        ) from exc
    _reconcile_create_link(path, required_uid=required_uid)
    observed = _read_file(
        path,
        label="frozen snapshot coordinator result",
        required_uid=required_uid,
        expected_mode=0o600,
        maximum=MAX_JOURNAL_BYTES,
    )
    if observed != payload or _hash_payload(observed) != digest:
        raise FrozenSnapshotOrchestratorError(
            "existing frozen snapshot coordinator result differs"
        )
    return path


def _final_controller_result(
    *,
    inputs: NGINX.CoordinatorInputs,
    paths: Mapping[str, Any],
    journal: Mapping[str, Any],
    required_uid: int,
) -> dict[str, Any]:
    result = _controller_result(
        inputs=inputs,
        paths=paths,
        journal=journal,
    )
    _persist_controller_result(
        paths=paths,
        result=result,
        required_uid=required_uid,
    )
    return result


def render_plan(
    *,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    binding_paths: Mapping[str, Path],
    state_receipt_path: Path,
    state_receipt_sha256: str,
    release_file_sha256: Mapping[str, str],
    resume_claim_path: Path | None,
    resume_claim_sha256: str | None,
) -> dict[str, Any]:
    paths = canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        state_receipt_sha256=state_receipt_sha256,
    )
    roles: dict[str, Any] = {}
    for role in ROLES:
        remote_prefix = [
            PYTHON,
            "-B",
            str(paths["agent"]),
            "--host-request-b64",
            "LEASE_BOUND_CANONICAL_REQUEST",
        ]
        roles[role] = {
            "host": ROLE_HOSTS[role],
            "transport": ROLE_TRANSPORTS[role],
            "binding_source": str(binding_paths[role]),
            "binding_sha256": bindings[role].canonical_sha256,
            "binding_destination": str(paths["roles"][role]["binding"]),
            "nginx_manifest": str(paths["roles"][role]["nginx_manifest"]),
            "nginx_archive": str(paths["roles"][role]["nginx_archive"]),
            "freeze_evidence": str(
                paths["roles"][role]["freeze_evidence"]
            ),
            "snapshot_directory": str(paths["roles"][role]["snapshot"]),
            "collection_directory": str(
                paths["roles"][role]["collection"]
            ),
            "workflow": [
                "install-material-create-only",
                "freeze-legacy-writers",
                "verify-frozen",
                "hold-verified-freeze",
                "produce-frozen-final",
                "collect-create-only",
            ],
            "host_agent_argv_template": (
                ssh_arguments(
                    inputs.ssh_identity,
                    known_hosts=inputs.known_hosts,
                    remote_arguments=remote_prefix,
                )
                if role == "webapp_fi"
                else remote_prefix
            ),
        }
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "state_receipt_path": str(state_receipt_path),
        "state_receipt_sha256": state_receipt_sha256,
        "live_lease_mode": (
            "resume-exact-unresolved-claim"
            if resume_claim_path is not None
            else "create-controller-authoritative-claim"
        ),
        "resume_claim_path": (
            str(resume_claim_path) if resume_claim_path is not None else None
        ),
        "resume_claim_sha256": resume_claim_sha256,
        "release_file_sha256": dict(release_file_sha256),
        "release_root": str(paths["release_root"]),
        "output_root": str(SOURCE_OUTPUT_ROOT),
        "collection_root": str(paths["collection_root"]),
        "outcome_path": str(paths["outcome"]),
        "journal_path": str(paths["journal"]),
        "roles": roles,
        "required_confirmation": confirmation_phrase(
            inputs.operation_id,
            inputs.release_sha,
            nginx_aggregate_sha256=inputs.aggregate_sha256,
            state_receipt_sha256=state_receipt_sha256,
            binding_sha256={
                role: bindings[role].canonical_sha256 for role in ROLES
            },
        ),
        "live_lease_outcome": "handoff-shadow-readonly",
        "pull_policy": "never",
        "build_performed": False,
        "docker_contacted": False,
        "network_io": False,
        "filesystem_mutated": False,
        "production_mutated": False,
        "automatic_restore_planned": False,
        "object_storage_used": False,
        "wa_contacted": False,
    }


def _load_inputs_and_receipt(
    *,
    aggregate_path: Path,
    bot_fi_nginx_manifest: Path,
    bot_fi_nginx_archive: Path,
    webapp_fi_nginx_manifest: Path,
    webapp_fi_nginx_archive: Path,
    state_receipt_path: Path,
    state_receipt_sha256: str,
    known_hosts: Path,
    ssh_identity: Path,
) -> NGINX.CoordinatorInputs:
    try:
        inputs = NGINX.load_inputs(
            aggregate_path=aggregate_path,
            bot_fi_manifest=bot_fi_nginx_manifest,
            bot_fi_archive=bot_fi_nginx_archive,
            webapp_fi_manifest=webapp_fi_nginx_manifest,
            webapp_fi_archive=webapp_fi_nginx_archive,
            known_hosts=known_hosts,
            ssh_identity=ssh_identity,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "Nginx generation coordinator inputs are invalid"
        ) from exc
    state_receipt_sha256 = _nonzero_sha256(
        state_receipt_sha256,
        label="legacy-frozen receipt SHA-256",
    )
    expected = canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        state_receipt_sha256=state_receipt_sha256,
    )["state_receipt"]
    if state_receipt_path != expected:
        raise FrozenSnapshotOrchestratorError(
            "legacy-frozen receipt path is not canonical"
        )
    try:
        _receipt, observed = NGINX.load_state_receipt(
            state_receipt_path,
            "legacy-frozen",
            inputs.operation_id,
            inputs.release_sha,
            inputs.release_tree_sha,
            inputs.aggregate_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "legacy-frozen receipt is invalid"
        ) from exc
    if observed != state_receipt_sha256:
        raise FrozenSnapshotOrchestratorError(
            "legacy-frozen receipt digest differs"
        )
    return inputs


def _validate_resume_arguments(
    *,
    inputs: NGINX.CoordinatorInputs,
    claim_path: Path | None,
    claim_sha256: str | None,
    claim_nonce: str | None,
) -> bool:
    supplied = (
        claim_path is not None,
        claim_sha256 is not None,
        claim_nonce is not None,
    )
    if any(supplied) and not all(supplied):
        raise FrozenSnapshotOrchestratorError(
            "resume requires the exact claim path, digest, and nonce"
        )
    if not all(supplied):
        return False
    assert claim_path is not None
    assert claim_sha256 is not None
    assert claim_nonce is not None
    digest = _nonzero_sha256(
        claim_sha256,
        label="resume claim SHA-256",
    )
    if (
        NONCE_RE.fullmatch(claim_nonce) is None
        or claim_nonce == ZERO_SHA256
    ):
        raise FrozenSnapshotOrchestratorError(
            "resume claim nonce is invalid"
        )
    expected = canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        lease_claim_sha256=digest,
    )["lease_claim"]
    if claim_path != expected:
        raise FrozenSnapshotOrchestratorError(
            "resume claim path is not canonical"
        )
    return True


def _recover_consumed_lease(
    *,
    journal: dict[str, Any],
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_path: Path,
    state_receipt_sha256: str,
    paths: Mapping[str, Any],
    required_uid: int,
) -> bool:
    if (
        journal["lease"] is None
        or journal["outcome_sha256"] is None
        or any(
            journal["roles"][role]["phase"] != "collected"
            for role in ROLES
        )
    ):
        return False
    handoff = journal.get("public_phase_handoff")
    _validate_public_phase_handoff(handoff, inputs=inputs)
    if not isinstance(handoff, dict) or handoff["status"] != "started":
        raise FrozenSnapshotOrchestratorError(
            "consumed lease recovery lacks a started public phase handoff"
        )
    claim_path = Path(journal["lease"]["claim_path"])
    claim_sha256 = journal["lease"]["claim_sha256"]
    try:
        claim, observed = NGINX.load_live_lease_claim_material(
            claim_path,
            state_receipt_path=state_receipt_path,
            expected_claim_sha256=claim_sha256,
            expected_state_receipt_sha256=state_receipt_sha256,
            operation_id=inputs.operation_id,
            release_sha=inputs.release_sha,
            release_tree_sha=inputs.release_tree_sha,
            aggregate_sha256=inputs.aggregate_sha256,
        )
        consumption = NGINX._load_consumption_audit(
            inputs,
            claim=claim,
            claim_sha256=claim_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise FrozenSnapshotOrchestratorError(
            "live lease consumption recovery is invalid"
        ) from exc
    if observed != claim_sha256 or consumption is None:
        return False
    audit, consumption_sha256 = consumption
    if (
        claim.get("owner_action") != "capture-frozen-final-snapshots"
        or audit.get("outcome") != "handoff-shadow-readonly"
        or audit.get("outcome_sha256") != journal["outcome_sha256"]
        or audit.get("final_state") != "legacy-frozen"
        or audit.get("final_state_receipt_sha256")
        != state_receipt_sha256
        or audit.get("readiness_audit_sha256") is not None
        or audit.get("automatic") is not False
    ):
        raise FrozenSnapshotOrchestratorError(
            "consumed live lease outcome differs from frozen handoff"
        )
    for role in ROLES:
        observed_collection = _verify_collected_role(
            role=role,
            binding=bindings[role],
            freeze_sha256=journal["roles"][role][
                "freeze_evidence_sha256"
            ],
            lease_claim_sha256=claim_sha256,
            paths=paths,
        )
        if observed_collection != journal["roles"][role]["collection"]:
            raise FrozenSnapshotOrchestratorError(
                f"{role} collection changed before consumption recovery"
            )
    outcome = _outcome_document(inputs=inputs, journal=journal)
    if (
        _persist_outcome(
            paths["outcome"],
            outcome,
            required_uid=required_uid,
        )
        != journal["outcome_sha256"]
    ):
        raise FrozenSnapshotOrchestratorError(
            "consumed lease outcome document differs"
        )
    journal["consumption_sha256"] = consumption_sha256
    journal["status"] = "complete"
    _append_event(
        journal,
        kind="lease-consumption-recovered",
        role=None,
        details={
            "outcome_sha256": journal["outcome_sha256"],
            "consumption_sha256": consumption_sha256,
        },
    )
    _journal_write_existing(
        paths=paths,
        journal=journal,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        required_uid=required_uid,
    )
    return True


def _phase_at_least(current: str, expected: str) -> bool:
    return ROLE_PHASES.index(current) >= ROLE_PHASES.index(expected)


def _journal_write_existing(
    *,
    paths: Mapping[str, Any],
    journal: dict[str, Any],
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    required_uid: int,
) -> None:
    _write_journal(
        paths["journal"],
        journal,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        required_uid=required_uid,
        create=False,
    )


def _assert_handoff_context(
    handoff: Mapping[str, Any],
    *,
    context: PublicCutoverContext,
    inputs: NGINX.CoordinatorInputs,
) -> None:
    expected = {
        **_public_journal_bindings(context),
        "nginx_aggregate_sha256": inputs.aggregate_sha256,
        "approval_sha256": context.approval_sha256,
        "approval_policy_sha256": context.approval_policy_sha256,
        "phase": PUBLIC_PHASE,
    }
    if any(handoff.get(key) != value for key, value in expected.items()):
        raise FrozenSnapshotOrchestratorError(
            "public phase handoff differs from the verified cutover closure"
        )


def _assert_handoff_matches_started_state(
    handoff: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    context: PublicCutoverContext,
    inputs: NGINX.CoordinatorInputs,
) -> None:
    _assert_handoff_context(handoff, context=context, inputs=inputs)
    state = _validate_public_phase_state(
        state,
        context=context,
        status="phase_started",
    )
    last = state["events"][-1]
    if (
        handoff.get("status") != "started"
        or handoff.get("started_journal_state_sha256")
        != state["state_sha256"]
        or handoff.get("started_journal_event_tail_sha256")
        != state["event_tail_sha256"]
        or handoff.get("started_journal_event_count") != len(state["events"])
        or last.get("previous_hash")
        != handoff.get("prestart_journal_event_tail_sha256")
        or len(state["events"])
        != handoff.get("prestart_journal_event_count", -1) + 1
    ):
        raise FrozenSnapshotOrchestratorError(
            "public phase handoff does not match the durable journal start"
        )


def _ensure_public_phase_started(
    *,
    journal: dict[str, Any],
    paths: Mapping[str, Any],
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    required_uid: int,
    context: PublicCutoverContext,
) -> None:
    """Durably start the public phase before any legacy writer freeze RPC."""

    public_journal = CONTROLLER.ProductionCutoverJournal(
        Path(context.manifest["deployment"]["controller_journal_path"])
    )
    try:
        state = public_journal.assert_bindings(
            **_public_journal_bindings(context)
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenSnapshotOrchestratorError(
            "public cutover journal binding differs"
        ) from exc
    handoff = journal["public_phase_handoff"]
    if handoff is not None:
        _validate_public_phase_handoff(handoff, inputs=inputs)
        assert isinstance(handoff, dict)
        _assert_handoff_context(handoff, context=context, inputs=inputs)
        if handoff["status"] == "started":
            _assert_handoff_matches_started_state(
                handoff,
                state,
                context=context,
                inputs=inputs,
            )
            return
        if handoff["status"] != "intent":
            raise FrozenSnapshotOrchestratorError(
                "public phase handoff status is invalid"
            )
        if state.get("status") == "phase_started":
            started_handoff = _public_phase_handoff(
                context=context,
                inputs=inputs,
                state=state,
                status="started",
                prior_intent=handoff,
            )
            _assert_handoff_matches_started_state(
                started_handoff,
                state,
                context=context,
                inputs=inputs,
            )
            journal["public_phase_handoff"] = started_handoff
            _append_event(
                journal,
                kind="public-phase-started",
                role=None,
                details={
                    "handoff_sha256": _hash_payload(
                        canonical_json(started_handoff)
                    )
                },
            )
            _journal_write_existing(
                paths=paths,
                journal=journal,
                inputs=inputs,
                bindings=bindings,
                state_receipt_sha256=state_receipt_sha256,
                required_uid=required_uid,
            )
            return
        state = _validate_public_phase_state(
            state,
            context=context,
            status="active",
        )
        if (
            state["state_sha256"]
            != handoff["prestart_journal_state_sha256"]
            or state["event_tail_sha256"]
            != handoff["prestart_journal_event_tail_sha256"]
            or len(state["events"])
            != handoff["prestart_journal_event_count"]
        ):
            raise FrozenSnapshotOrchestratorError(
                "public cutover journal changed after the durable handoff intent"
            )
    else:
        if journal["lease"] is not None:
            raise FrozenSnapshotOrchestratorError(
                "live lease exists without a durable public phase handoff"
            )
        state = _validate_public_phase_state(
            state,
            context=context,
            status="active",
        )
        handoff = _public_phase_handoff(
            context=context,
            inputs=inputs,
            state=state,
            status="intent",
        )
        journal["public_phase_handoff"] = handoff
        _append_event(
            journal,
            kind="public-phase-intent",
            role=None,
            details={"handoff_sha256": _hash_payload(canonical_json(handoff))},
        )
        _journal_write_existing(
            paths=paths,
            journal=journal,
            inputs=inputs,
            bindings=bindings,
            state_receipt_sha256=state_receipt_sha256,
            required_uid=required_uid,
        )
    _verify_public_cutover_authorization(context)
    try:
        state = public_journal.begin_phase(PUBLIC_PHASE)
        state = public_journal.assert_bindings(
            **_public_journal_bindings(context)
        )
    except CONTROLLER.CutoverContractError as exc:
        raise FrozenSnapshotOrchestratorError(
            "public cutover phase cannot be durably started"
        ) from exc
    started_handoff = _public_phase_handoff(
        context=context,
        inputs=inputs,
        state=state,
        status="started",
        prior_intent=handoff,
    )
    _assert_handoff_matches_started_state(
        started_handoff,
        state,
        context=context,
        inputs=inputs,
    )
    journal["public_phase_handoff"] = started_handoff
    _append_event(
        journal,
        kind="public-phase-started",
        role=None,
        details={
            "handoff_sha256": _hash_payload(canonical_json(started_handoff))
        },
    )
    _journal_write_existing(
        paths=paths,
        journal=journal,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        required_uid=required_uid,
    )


def _request_for(
    *,
    action: str,
    inputs: NGINX.CoordinatorInputs,
    role: str,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    lease_claim_sha256: str,
    release_file_sha256: Mapping[str, str],
) -> dict[str, Any]:
    return build_host_request(
        action=action,
        inputs=inputs,
        role=role,
        binding_sha256=bindings[role].canonical_sha256,
        state_receipt_sha256=state_receipt_sha256,
        lease_claim_sha256=lease_claim_sha256,
        release_file_sha256=release_file_sha256,
    )


def _install_role_material(
    *,
    role: str,
    lease: Any,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    binding_paths: Mapping[str, Path],
    state_receipt_path: Path,
    state_receipt_sha256: str,
    release_file_sha256: Mapping[str, str],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    known_hosts: Path,
    runner: Runner | None,
    required_uid: int,
    checkpoint: Checkpoint,
    authorization: AuthorizationCheck | None = None,
) -> None:
    request = _request_for(
        action="prepare-material",
        inputs=inputs,
        role=role,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        lease_claim_sha256=lease.claim_sha256,
        release_file_sha256=release_file_sha256,
    )
    prepared = _validate_prepare_result(
        _leased_call(
            lease,
            label=f"prepare-material:{role}",
            call=lambda: _invoke_host(
                role=role,
                request=request,
                paths=paths,
                ssh_identity=ssh_identity,
                known_hosts=known_hosts,
                runner=runner,
            ),
            checkpoint=checkpoint,
            authorization=authorization,
        ),
        request=request,
    )
    sources = {
        "binding": binding_paths[role],
        "state_receipt": state_receipt_path,
        "lease_claim": lease.claim_path,
    }
    for key in prepared["need_transfer"]:
        _leased_call(
            lease,
            label=f"transfer-{key}:{role}",
            call=lambda key=key: _transfer_material(
                role=role,
                key=key,
                request=request,
                source=sources[key],
                ssh_identity=ssh_identity,
                known_hosts=known_hosts,
                runner=runner,
                required_uid=required_uid,
            ),
            checkpoint=checkpoint,
            authorization=authorization,
        )
    install = dict(request)
    install["action"] = "install-material"
    install = decode_host_request(encode_host_request(install))
    _validate_host_result(
        _leased_call(
            lease,
            label=f"install-material:{role}",
            call=lambda: _invoke_host(
                role=role,
                request=install,
                paths=paths,
                ssh_identity=ssh_identity,
                known_hosts=known_hosts,
                runner=runner,
            ),
            checkpoint=checkpoint,
            authorization=authorization,
        ),
        request=install,
        expected_claim_epoch=lease.claim["claim_epoch"],
    )


def _invoke_bound_action(
    *,
    action: str,
    role: str,
    lease: Any,
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    release_file_sha256: Mapping[str, str],
    paths: Mapping[str, Any],
    ssh_identity: Path,
    known_hosts: Path,
    runner: Runner | None,
    checkpoint: Checkpoint,
    authorization: AuthorizationCheck | None = None,
) -> dict[str, Any]:
    request = _request_for(
        action=action,
        inputs=inputs,
        role=role,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        lease_claim_sha256=lease.claim_sha256,
        release_file_sha256=release_file_sha256,
    )
    return _validate_host_result(
        _leased_call(
            lease,
            label=f"{action}:{role}",
            call=lambda: _invoke_host(
                role=role,
                request=request,
                paths=paths,
                ssh_identity=ssh_identity,
                known_hosts=known_hosts,
                runner=runner,
            ),
            checkpoint=checkpoint,
            authorization=authorization,
        ),
        request=request,
        expected_claim_epoch=lease.claim["claim_epoch"],
    )


def _record_role_phase(
    *,
    journal: dict[str, Any],
    role: str,
    phase: str,
    details: Mapping[str, Any],
    paths: Mapping[str, Any],
    inputs: NGINX.CoordinatorInputs,
    bindings: Mapping[str, SOURCE.SnapshotBinding],
    state_receipt_sha256: str,
    required_uid: int,
) -> None:
    current = journal["roles"][role]["phase"]
    if ROLE_PHASES.index(phase) < ROLE_PHASES.index(current):
        raise FrozenSnapshotOrchestratorError(
            f"{role} journal phase cannot move backward"
        )
    journal["roles"][role]["phase"] = phase
    _append_event(journal, kind=phase, role=role, details=details)
    _journal_write_existing(
        paths=paths,
        journal=journal,
        inputs=inputs,
        bindings=bindings,
        state_receipt_sha256=state_receipt_sha256,
        required_uid=required_uid,
    )


def orchestrate(
    *,
    aggregate_path: Path,
    bot_fi_nginx_manifest: Path,
    bot_fi_nginx_archive: Path,
    webapp_fi_nginx_manifest: Path,
    webapp_fi_nginx_archive: Path,
    bot_fi_binding: Path,
    webapp_fi_binding: Path,
    state_receipt_path: Path,
    state_receipt_sha256: str,
    known_hosts: Path = KNOWN_HOSTS,
    ssh_identity: Path = DEFAULT_SSH_IDENTITY,
    resume_claim_path: Path | None = None,
    resume_claim_sha256: str | None = None,
    resume_claim_nonce: str | None = None,
    manifest_path: Path | None = None,
    approval_path: Path | None = None,
    approval_policy_path: Path | None = None,
    apply: bool = False,
    confirm: str | None = None,
    runner: Runner | None = None,
    required_uid: int = 0,
    checkpoint: Checkpoint | None = None,
    observed_host_addresses: set[str] | None = None,
) -> dict[str, Any]:
    inputs = _load_inputs_and_receipt(
        aggregate_path=aggregate_path,
        bot_fi_nginx_manifest=bot_fi_nginx_manifest,
        bot_fi_nginx_archive=bot_fi_nginx_archive,
        webapp_fi_nginx_manifest=webapp_fi_nginx_manifest,
        webapp_fi_nginx_archive=webapp_fi_nginx_archive,
        state_receipt_path=state_receipt_path,
        state_receipt_sha256=state_receipt_sha256,
        known_hosts=known_hosts,
        ssh_identity=ssh_identity,
    )
    binding_paths = {
        "bot_fi": bot_fi_binding,
        "webapp_fi": webapp_fi_binding,
    }
    bindings = load_bindings(
        operation_id=inputs.operation_id,
        release_sha=inputs.release_sha,
        bot_fi_binding=bot_fi_binding,
        webapp_fi_binding=webapp_fi_binding,
    )
    state_receipt_sha256 = _nonzero_sha256(
        state_receipt_sha256,
        label="legacy-frozen receipt SHA-256",
    )
    resume = _validate_resume_arguments(
        inputs=inputs,
        claim_path=resume_claim_path,
        claim_sha256=resume_claim_sha256,
        claim_nonce=resume_claim_nonce,
    )
    release_file_sha256 = _release_file_hashes(
        inputs.operation_id,
        inputs.release_sha,
        required_uid=required_uid,
    )
    plan = render_plan(
        inputs=inputs,
        bindings=bindings,
        binding_paths=binding_paths,
        state_receipt_path=state_receipt_path,
        state_receipt_sha256=state_receipt_sha256,
        release_file_sha256=release_file_sha256,
        resume_claim_path=resume_claim_path,
        resume_claim_sha256=resume_claim_sha256,
    )
    if not apply:
        if confirm is not None:
            raise FrozenSnapshotOrchestratorError(
                "--confirm is valid only with --apply"
            )
        return plan
    required_confirmation = confirmation_phrase(
        inputs.operation_id,
        inputs.release_sha,
        nginx_aggregate_sha256=inputs.aggregate_sha256,
        state_receipt_sha256=state_receipt_sha256,
        binding_sha256={
            role: bindings[role].canonical_sha256 for role in ROLES
        },
    )
    if confirm != required_confirmation:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final snapshot confirmation mismatch"
        )
    if os.geteuid() != required_uid or required_uid != 0:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final snapshot controller must run as root"
        )
    try:
        FINLAND_STAGE._verify_role_host(
            "bot_fi",
            observed_host_addresses=observed_host_addresses,
        )
    except FINLAND_STAGE.FinlandStageError as exc:
        raise FrozenSnapshotOrchestratorError(
            "frozen-final controller host is not Bot-FI"
        ) from exc
    _assert_ssh_material(
        ssh_identity,
        known_hosts=known_hosts,
        required_uid=required_uid,
    )
    paths = canonical_paths(
        inputs.operation_id,
        inputs.release_sha,
        state_receipt_sha256=state_receipt_sha256,
    )
    _ensure_controller_directories(paths, required_uid=required_uid)
    callback = checkpoint if checkpoint is not None else (lambda _name: None)

    with _controller_lock(paths["lock"], required_uid=required_uid):
        journal = _read_journal(
            paths["journal"],
            inputs=inputs,
            bindings=bindings,
            state_receipt_sha256=state_receipt_sha256,
            required_uid=required_uid,
        )
        if journal is None:
            if resume:
                raise FrozenSnapshotOrchestratorError(
                    "resume claim has no matching controller journal"
                )
            journal = _initial_journal(
                inputs=inputs,
                bindings=bindings,
                state_receipt_sha256=state_receipt_sha256,
            )
            _write_journal(
                paths["journal"],
                journal,
                inputs=inputs,
                bindings=bindings,
                state_receipt_sha256=state_receipt_sha256,
                required_uid=required_uid,
                create=True,
            )
        if journal["status"] == "complete":
            if resume:
                raise FrozenSnapshotOrchestratorError(
                    "completed operation cannot resume an unresolved claim"
                )
            for role in ROLES:
                observed = _verify_collected_role(
                    role=role,
                    binding=bindings[role],
                    freeze_sha256=journal["roles"][role][
                        "freeze_evidence_sha256"
                    ],
                    lease_claim_sha256=journal["lease"][
                        "claim_sha256"
                    ],
                    paths=paths,
                )
                if observed != journal["roles"][role]["collection"]:
                    raise FrozenSnapshotOrchestratorError(
                        f"{role} completed collection changed"
                    )
            outcome = _outcome_document(inputs=inputs, journal=journal)
            if (
                _persist_outcome(
                    paths["outcome"],
                    outcome,
                    required_uid=required_uid,
                )
                != journal["outcome_sha256"]
            ):
                raise FrozenSnapshotOrchestratorError(
                    "completed outcome digest differs"
                )
            return _final_controller_result(
                inputs=inputs,
                paths=paths,
                journal=journal,
                required_uid=required_uid,
            )
        if _recover_consumed_lease(
            journal=journal,
            inputs=inputs,
            bindings=bindings,
            state_receipt_path=state_receipt_path,
            state_receipt_sha256=state_receipt_sha256,
            paths=paths,
            required_uid=required_uid,
        ):
            return _final_controller_result(
                inputs=inputs,
                paths=paths,
                journal=journal,
                required_uid=required_uid,
            )
        public_context = _load_public_cutover_context(
            manifest_path=manifest_path,
            approval_path=approval_path,
            approval_policy_path=approval_policy_path,
            inputs=inputs,
            bindings=bindings,
        )
        _ensure_public_phase_started(
            journal=journal,
            paths=paths,
            inputs=inputs,
            bindings=bindings,
            state_receipt_sha256=state_receipt_sha256,
            required_uid=required_uid,
            context=public_context,
        )
        _verify_public_cutover_authorization(public_context)
        authorization_callback: AuthorizationCheck = (
            lambda: _verify_public_cutover_authorization(public_context)
        )
        if resume:
            if (
                journal["lease"] is None
                or journal["lease"]["claim_path"] != str(resume_claim_path)
                or journal["lease"]["claim_sha256"]
                != resume_claim_sha256
            ):
                raise FrozenSnapshotOrchestratorError(
                    "resume claim differs from the controller journal"
                )
            assert resume_claim_path is not None
            assert resume_claim_sha256 is not None
            assert resume_claim_nonce is not None
            lease_context = NGINX.resume_coordinator_live_lease(
                inputs=inputs,
                expected_owner_action=(
                    "capture-frozen-final-snapshots"
                ),
                claim_path=resume_claim_path,
                expected_claim_sha256=resume_claim_sha256,
                expected_nonce=resume_claim_nonce,
            )
        else:
            if journal["lease"] is not None:
                raise FrozenSnapshotOrchestratorError(
                    "unresolved live lease requires explicit exact resume"
                )
            if journal["status"] != "prepared":
                raise FrozenSnapshotOrchestratorError(
                    "non-pristine journal requires explicit lease resume"
                )
            lease_context = NGINX.hold_coordinator_live_lease(
                inputs=inputs,
                owner_action="capture-frozen-final-snapshots",
                legacy_frozen_receipt_path=state_receipt_path,
                legacy_frozen_receipt_sha256=state_receipt_sha256,
            )
        # The lease context's entry is the first live mutation. Revalidate the
        # approval as the final operation before either a new or resumed lease.
        authorization_callback()
        with lease_context as lease:
            try:
                lease.verify()
                claim = lease.claim
                expected_claim_path = canonical_paths(
                    inputs.operation_id,
                    inputs.release_sha,
                    lease_claim_sha256=lease.claim_sha256,
                )["lease_claim"]
                if (
                    lease.claim_path != expected_claim_path
                    or claim.get("owner_action")
                    != "capture-frozen-final-snapshots"
                    or claim.get("operation_id") != inputs.operation_id
                    or claim.get("release_sha") != inputs.release_sha
                    or claim.get("release_tree_sha")
                    != inputs.release_tree_sha
                    or claim.get("aggregate_sha256")
                    != inputs.aggregate_sha256
                    or claim.get("legacy_frozen_receipt_sha256")
                    != state_receipt_sha256
                    or type(claim.get("claim_epoch")) is not int
                    or claim["claim_epoch"] < 1
                ):
                    raise FrozenSnapshotOrchestratorError(
                        "held live lease claim binding differs"
                    )
                claim_digest, _claim_bytes = _hash_file(
                    lease.claim_path,
                    label="held live lease claim",
                    required_uid=required_uid,
                    expected_mode=0o600,
                    maximum=MAX_JSON_BYTES,
                )
                if claim_digest != lease.claim_sha256:
                    raise FrozenSnapshotOrchestratorError(
                        "held live lease claim digest differs"
                    )
                if journal["lease"] is None:
                    journal["lease"] = {
                        "claim_path": str(lease.claim_path),
                        "claim_sha256": lease.claim_sha256,
                        "claim_epoch": claim["claim_epoch"],
                    }
                    journal["status"] = "active"
                    _append_event(
                        journal,
                        kind="lease-held",
                        role=None,
                        details={
                            "claim_sha256": lease.claim_sha256,
                            "claim_epoch": claim["claim_epoch"],
                        },
                    )
                    _journal_write_existing(
                        paths=paths,
                        journal=journal,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        required_uid=required_uid,
                    )
                elif (
                    journal["lease"]["claim_path"] != str(lease.claim_path)
                    or journal["lease"]["claim_sha256"]
                    != lease.claim_sha256
                    or journal["lease"]["claim_epoch"]
                    != claim["claim_epoch"]
                ):
                    raise FrozenSnapshotOrchestratorError(
                        "resumed live lease differs from journal"
                    )
                callback("after-lease-held")

                for role in ROLES:
                    _install_role_material(
                        role=role,
                        lease=lease,
                        inputs=inputs,
                        bindings=bindings,
                        binding_paths=binding_paths,
                        state_receipt_path=state_receipt_path,
                        state_receipt_sha256=state_receipt_sha256,
                        release_file_sha256=release_file_sha256,
                        paths=paths,
                        ssh_identity=ssh_identity,
                        known_hosts=known_hosts,
                        runner=runner,
                        required_uid=required_uid,
                        checkpoint=callback,
                        authorization=authorization_callback,
                    )
                    if not _phase_at_least(
                        journal["roles"][role]["phase"],
                        "material-installed",
                    ):
                        _record_role_phase(
                            journal=journal,
                            role=role,
                            phase="material-installed",
                            details={
                                "binding_sha256": bindings[
                                    role
                                ].canonical_sha256,
                                "lease_claim_sha256": lease.claim_sha256,
                            },
                            paths=paths,
                            inputs=inputs,
                            bindings=bindings,
                            state_receipt_sha256=state_receipt_sha256,
                            required_uid=required_uid,
                        )

                for role in ROLES:
                    if _phase_at_least(
                        journal["roles"][role]["phase"],
                        "frozen",
                    ):
                        continue
                    result = _invoke_bound_action(
                        action="freeze",
                        role=role,
                        lease=lease,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        release_file_sha256=release_file_sha256,
                        paths=paths,
                        ssh_identity=ssh_identity,
                        known_hosts=known_hosts,
                        runner=runner,
                        checkpoint=callback,
                        authorization=authorization_callback,
                    )
                    freeze_sha256 = result["freeze_evidence_sha256"]
                    journal["roles"][role][
                        "freeze_evidence_sha256"
                    ] = freeze_sha256
                    _record_role_phase(
                        journal=journal,
                        role=role,
                        phase="frozen",
                        details={
                            "freeze_evidence_sha256": freeze_sha256,
                            "lease_claim_sha256": lease.claim_sha256,
                        },
                        paths=paths,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        required_uid=required_uid,
                    )

                for role in ROLES:
                    result = _invoke_bound_action(
                        action="verify",
                        role=role,
                        lease=lease,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        release_file_sha256=release_file_sha256,
                        paths=paths,
                        ssh_identity=ssh_identity,
                        known_hosts=known_hosts,
                        runner=runner,
                        checkpoint=callback,
                        authorization=authorization_callback,
                    )
                    if (
                        result["freeze_evidence_sha256"]
                        != journal["roles"][role][
                            "freeze_evidence_sha256"
                        ]
                    ):
                        raise FrozenSnapshotOrchestratorError(
                            f"{role} freeze evidence changed"
                        )
                    if not _phase_at_least(
                        journal["roles"][role]["phase"],
                        "verified",
                    ):
                        _record_role_phase(
                            journal=journal,
                            role=role,
                            phase="verified",
                            details={
                                "freeze_evidence_sha256": result[
                                    "freeze_evidence_sha256"
                                ],
                                "lease_claim_sha256": lease.claim_sha256,
                            },
                            paths=paths,
                            inputs=inputs,
                            bindings=bindings,
                            state_receipt_sha256=state_receipt_sha256,
                            required_uid=required_uid,
                        )

                for role in ROLES:
                    phase = journal["roles"][role]["phase"]
                    if _phase_at_least(phase, "collected"):
                        observed = _verify_collected_role(
                            role=role,
                            binding=bindings[role],
                            freeze_sha256=journal["roles"][role][
                                "freeze_evidence_sha256"
                            ],
                            lease_claim_sha256=lease.claim_sha256,
                            paths=paths,
                        )
                        if observed != journal["roles"][role]["collection"]:
                            raise FrozenSnapshotOrchestratorError(
                                f"{role} collected snapshot changed"
                            )
                        continue
                    result = _invoke_bound_action(
                        action="snapshot",
                        role=role,
                        lease=lease,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        release_file_sha256=release_file_sha256,
                        paths=paths,
                        ssh_identity=ssh_identity,
                        known_hosts=known_hosts,
                        runner=runner,
                        checkpoint=callback,
                        authorization=authorization_callback,
                    )
                    if (
                        result["freeze_evidence_sha256"]
                        != journal["roles"][role][
                            "freeze_evidence_sha256"
                        ]
                    ):
                        raise FrozenSnapshotOrchestratorError(
                            f"{role} snapshot freeze evidence differs"
                        )
                    snapshot = {
                        "freeze_evidence_sha256": result[
                            "freeze_evidence_sha256"
                        ],
                        "lease_claim_sha256": lease.claim_sha256,
                        "files": result["files"],
                    }
                    if _phase_at_least(phase, "snapshotted"):
                        if snapshot != journal["roles"][role]["snapshot"]:
                            raise FrozenSnapshotOrchestratorError(
                                f"{role} resumed host snapshot differs"
                            )
                    else:
                        journal["roles"][role]["snapshot"] = snapshot
                        _record_role_phase(
                            journal=journal,
                            role=role,
                            phase="snapshotted",
                            details=snapshot,
                            paths=paths,
                            inputs=inputs,
                            bindings=bindings,
                            state_receipt_sha256=state_receipt_sha256,
                            required_uid=required_uid,
                        )
                    for name in SNAPSHOT_FILENAMES:
                        _leased_call(
                            lease,
                            label=f"collect-{name}:{role}",
                            call=lambda name=name: _collect_file(
                                role=role,
                                name=name,
                                row=result["files"][name],
                                paths=paths,
                                ssh_identity=ssh_identity,
                                known_hosts=known_hosts,
                                runner=runner,
                                required_uid=required_uid,
                            ),
                            checkpoint=callback,
                            authorization=authorization_callback,
                        )
                    collection = _verify_collected_role(
                        role=role,
                        binding=bindings[role],
                        freeze_sha256=result["freeze_evidence_sha256"],
                        lease_claim_sha256=lease.claim_sha256,
                        paths=paths,
                    )
                    if collection["files"] != result["files"]:
                        raise FrozenSnapshotOrchestratorError(
                            f"{role} collection differs from host inventory"
                        )
                    journal["roles"][role]["collection"] = collection
                    _record_role_phase(
                        journal=journal,
                        role=role,
                        phase="collected",
                        details=collection,
                        paths=paths,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        required_uid=required_uid,
                    )

                outcome = _outcome_document(inputs=inputs, journal=journal)
                outcome_sha256 = _persist_outcome(
                    paths["outcome"],
                    outcome,
                    required_uid=required_uid,
                )
                journal["outcome_sha256"] = outcome_sha256
                journal["status"] = "ready-to-consume"
                _append_event(
                    journal,
                    kind="outcome-published",
                    role=None,
                    details={"outcome_sha256": outcome_sha256},
                )
                _journal_write_existing(
                    paths=paths,
                    journal=journal,
                    inputs=inputs,
                    bindings=bindings,
                    state_receipt_sha256=state_receipt_sha256,
                    required_uid=required_uid,
                )
                callback("before-lease-consume")
                authorization_callback()
                lease.verify()
                authorization_callback()
                _consumption_path, consumption_sha256 = lease.consume(
                    outcome="handoff-shadow-readonly",
                    outcome_sha256=outcome_sha256,
                )
                callback("after-lease-consume")
                journal["consumption_sha256"] = consumption_sha256
                journal["status"] = "complete"
                _append_event(
                    journal,
                    kind="lease-consumed",
                    role=None,
                    details={
                        "outcome_sha256": outcome_sha256,
                        "consumption_sha256": consumption_sha256,
                    },
                )
                _journal_write_existing(
                    paths=paths,
                    journal=journal,
                    inputs=inputs,
                    bindings=bindings,
                    state_receipt_sha256=state_receipt_sha256,
                    required_uid=required_uid,
                )
            except BaseException as exc:
                if journal["status"] != "complete":
                    journal["status"] = "reconciliation-required"
                    _append_event(
                        journal,
                        kind="reconciliation-required",
                        role=None,
                        details={
                            "error_sha256": _hash_payload(
                                (
                                    f"{type(exc).__name__}:"
                                    f"{str(exc)}"
                                ).encode("utf-8", errors="replace")
                            ),
                            "automatic_restore_performed": False,
                        },
                    )
                    _journal_write_existing(
                        paths=paths,
                        journal=journal,
                        inputs=inputs,
                        bindings=bindings,
                        state_receipt_sha256=state_receipt_sha256,
                        required_uid=required_uid,
                    )
                raise
        final_journal = _read_journal(
            paths["journal"],
            inputs=inputs,
            bindings=bindings,
            state_receipt_sha256=state_receipt_sha256,
            required_uid=required_uid,
        )
        if final_journal is None or final_journal["status"] != "complete":
            raise FrozenSnapshotOrchestratorError(
                "frozen-final controller did not complete"
            )
        return _final_controller_result(
            inputs=inputs,
            paths=paths,
            journal=final_journal,
            required_uid=required_uid,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path)
    parser.add_argument("--bot-fi-nginx-manifest", type=Path)
    parser.add_argument("--bot-fi-nginx-archive", type=Path)
    parser.add_argument("--webapp-fi-nginx-manifest", type=Path)
    parser.add_argument("--webapp-fi-nginx-archive", type=Path)
    parser.add_argument("--bot-fi-binding", type=Path)
    parser.add_argument("--webapp-fi-binding", type=Path)
    parser.add_argument("--state-receipt", type=Path)
    parser.add_argument("--state-receipt-sha256")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-policy", type=Path)
    parser.add_argument("--known-hosts", type=Path, default=KNOWN_HOSTS)
    parser.add_argument(
        "--ssh-identity",
        type=Path,
        default=DEFAULT_SSH_IDENTITY,
    )
    parser.add_argument("--resume-claim", type=Path)
    parser.add_argument("--resume-claim-sha256")
    parser.add_argument("--resume-claim-nonce")
    parser.add_argument("--pull", choices=("never",), default="never")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--host-request-b64", help=argparse.SUPPRESS)
    parser.add_argument("--control-fd", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pull != "never":
            raise FrozenSnapshotOrchestratorError(
                "only --pull never is supported"
            )
        if args.host_request_b64 is not None:
            forbidden = (
                args.aggregate,
                args.bot_fi_nginx_manifest,
                args.bot_fi_nginx_archive,
                args.webapp_fi_nginx_manifest,
                args.webapp_fi_nginx_archive,
                args.bot_fi_binding,
                args.webapp_fi_binding,
                args.state_receipt,
                args.state_receipt_sha256,
                args.manifest,
                args.approval,
                args.approval_policy,
                args.resume_claim,
                args.resume_claim_sha256,
                args.resume_claim_nonce,
                args.confirm,
            )
            if (
                any(value is not None for value in forbidden)
                or args.apply
                or args.control_fd is None
            ):
                raise FrozenSnapshotOrchestratorError(
                    "host request requires liveness and cannot be combined "
                    "with controller arguments"
                )
            result = host_agent(
                args.host_request_b64,
                control_fd=args.control_fd,
            )
        else:
            if args.control_fd is not None:
                raise FrozenSnapshotOrchestratorError(
                    "--control-fd is valid only for a host request"
                )
            required = (
                args.aggregate,
                args.bot_fi_nginx_manifest,
                args.bot_fi_nginx_archive,
                args.webapp_fi_nginx_manifest,
                args.webapp_fi_nginx_archive,
                args.bot_fi_binding,
                args.webapp_fi_binding,
                args.state_receipt,
                args.state_receipt_sha256,
            )
            if any(value is None for value in required):
                raise FrozenSnapshotOrchestratorError(
                    "aggregate, both Nginx role archives, both bindings, "
                    "and the legacy-frozen receipt are required"
                )
            result = orchestrate(
                aggregate_path=args.aggregate,
                bot_fi_nginx_manifest=args.bot_fi_nginx_manifest,
                bot_fi_nginx_archive=args.bot_fi_nginx_archive,
                webapp_fi_nginx_manifest=args.webapp_fi_nginx_manifest,
                webapp_fi_nginx_archive=args.webapp_fi_nginx_archive,
                bot_fi_binding=args.bot_fi_binding,
                webapp_fi_binding=args.webapp_fi_binding,
                state_receipt_path=args.state_receipt,
                state_receipt_sha256=args.state_receipt_sha256,
                known_hosts=args.known_hosts,
                ssh_identity=args.ssh_identity,
                resume_claim_path=args.resume_claim,
                resume_claim_sha256=args.resume_claim_sha256,
                resume_claim_nonce=args.resume_claim_nonce,
                manifest_path=args.manifest,
                approval_path=args.approval,
                approval_policy_path=args.approval_policy,
                apply=args.apply,
                confirm=args.confirm,
            )
        print(canonical_json(result).decode("ascii"))
        return 0
    except (
        FrozenSnapshotOrchestratorError,
        NGINX.NginxCoordinatorError,
        NGINX_GENERATION.NginxGenerationError,
        FREEZE.LegacyWriterFreezeError,
        SOURCE.SourceSnapshotError,
        FINLAND_STAGE.FinlandStageError,
    ) as exc:
        print(
            canonical_json(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                }
            ).decode("ascii")
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
