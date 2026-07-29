#!/usr/bin/env python3
"""Run only reversible, operation-owned production-shadow preparation steps."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import secrets
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping
from uuid import UUID

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.docker_image_identity import (
    DockerImageIdentityError,
    image_content_descriptor,
    image_content_descriptor_from_archive_config,
    verify_content_descriptor,
)
from core.secure_file_io import (
    SecureFileError,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from scripts.render_three_site_production_shadow_role_compose import (
    parse_env_values,
)
from scripts.wa_ir_production_operation import (
    DATABASE_FINGERPRINT_CLIENT_ENCODING,
    DATABASE_FINGERPRINT_PGOPTIONS,
    ProductionOperationError,
    StreamDigest,
    Image,
    _concurrent_index_names,
    _fingerprint_from_streams,
    _load_migration_graph,
    _migration_corridor,
    _docker_archive_identity,
)


MANIFEST_SCHEMA = "production-shadow-precommit-operation-v1"
JOURNAL_SCHEMA = "production-shadow-precommit-journal-v1"
EVIDENCE_SCHEMA = "production-shadow-precommit-evidence-v1"
DIRECTORY_BINDINGS_SCHEMA = "production-shadow-data-directory-bindings-v1"
READONLY_ACCEPTANCE_SCHEMA = "production-shadow-readonly-acceptance-v1"
PROJECT_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow")
DATA_ROOT_PREFIX = Path("/srv/trading-bot-three-site-production-shadow-data")
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
DOCKER = "/usr/bin/docker"
GIT = "/usr/bin/git"
MAX_FILE_BYTES = 64 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TAR_MEMBERS = 250_000
MAX_COMMAND_STDOUT_BYTES = 16 * 1024 * 1024
MAX_COMMAND_STDERR_BYTES = 2 * 1024 * 1024
PROCESS_GROUP_TERM_SECONDS = 1.0
PYTHON3 = "/usr/bin/python3"
BOUNDED_EXEC_WRAPPER = "--bounded-exec-wrapper"
GROUP_REPORT_START = "S"
GROUP_REPORT_DONE = "D"
COMMAND_MODE_NORMAL = "N"
COMMAND_MODE_CLEANUP = "C"
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
PURPOSE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PR_SET_CHILD_SUBREAPER = 36
PROCESS_TREE_QUIESCENCE_SECONDS = 0.25
ONEOFF_QUIESCENCE_SECONDS = 1.0
ONEOFF_CLEANUP_TIMEOUT_SECONDS = 30.0
POSTGRES_RUNTIME_UID = 70
POSTGRES_RUNTIME_GID = 70
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-z_]{1,64}$")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
ROLE_NAMES = ("bot_fi", "webapp_fi")
ACTIONS = (
    "verify-installation",
    "bootstrap-database",
    "restore-shadow",
    "prepare-shadow",
    "readonly-acceptance",
)
MUTATING_ACTIONS = frozenset(ACTIONS[1:])
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_sha256",
        "approval_sha256",
        "role_material_sha256",
        "canonical_compose_sha256",
        "role_compose_sha256",
        "environment_sha256",
        "worker_sha256",
        "acceptance_producer_sha256",
        "image_artifacts",
        "runtime_image_ids",
        "artifacts",
        "source_database",
        "target_migration_revision",
        "postgres_runtime_uid",
        "postgres_runtime_gid",
    }
)
IMAGE_FIELDS = frozenset({"app", "postgres", "redis", "nginx"})
IMAGE_ARTIFACT_FIELDS = frozenset(
    {
        "archive_sha256",
        "archive_bytes",
        "config_digest",
        "content_descriptor",
        "content_identity",
    }
)
ARTIFACT_KINDS = (
    "release-bundle",
    "role-material",
    "app-image-archive",
    "postgres-image-archive",
    "redis-image-archive",
    "nginx-image-archive",
    "database-backup",
    "uploads-archive",
    "audit-archive",
)
ARTIFACT_FIELDS = frozenset({"sha256", "bytes", "restored_tree_sha256"})
SOURCE_DATABASE_FIELDS = frozenset(
    {
        "alembic_revision",
        "fingerprint_algorithm",
        "database_fingerprint_sha256",
        "row_count",
        "table_count",
    }
)
JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "role",
        "release_sha",
        "manifest_sha256",
        "completed_actions",
        "current_action",
        "attempts",
        "evidence",
        "events",
        "state_sha256",
    }
)
EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "status",
        "action",
        "operation_id",
        "role",
        "release_sha",
        "release_tree_sha",
        "manifest_sha256",
        "controller_manifest_sha256",
        "approval_sha256",
        "role_material_sha256",
        "business_write_allowed",
        "freeze_performed",
        "current_mutated",
        "legacy_mutated",
        "object_storage_mutated",
        "destructive_cleanup_performed",
        "semantic",
    }
)
_SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/root/.docker",
}
_SAFE_GIT_ENV = {
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
}
_GIT_CONFIG_ARGUMENTS = (
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fileMode=true",
)


class PrecommitWorkerError(RuntimeError):
    """A redacted fail-closed preparation error."""


class PrecommitWorkerCancellation(PrecommitWorkerError):
    """The controller connection or worker process authority was lost."""


class BoundedCommandError(RuntimeError):
    """A subprocess exceeded the local bounded execution contract."""


def _anonymous_pipe_identity(
    descriptor: int,
    *,
    access_mode: int,
    reject_opposite_end: bool,
    label: str,
) -> tuple[int, int]:
    if type(descriptor) is not int or descriptor < 0:
        raise PrecommitWorkerError(f"{label} descriptor is invalid")
    try:
        metadata = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} descriptor is unavailable") from exc
    if (
        not stat.S_ISFIFO(metadata.st_mode)
        or flags & os.O_ACCMODE != access_mode
        or target != f"pipe:[{metadata.st_ino}]"
    ):
        raise PrecommitWorkerError(
            f"{label} must be an anonymous pipe with exact direction"
        )
    if reject_opposite_end:
        opposite_modes = (
            {os.O_WRONLY, os.O_RDWR}
            if access_mode == os.O_RDONLY
            else {os.O_RDONLY, os.O_RDWR}
        )
        try:
            entries = tuple(Path("/proc/self/fd").iterdir())
        except OSError as exc:
            raise PrecommitWorkerError(
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
                and observed_flags & os.O_ACCMODE in opposite_modes
            ):
                raise PrecommitWorkerError(
                    f"{label} writer end is held by the worker"
                )
    return metadata.st_dev, metadata.st_ino


def _enable_child_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise PrecommitWorkerError(
            f"child subreaper setup failed with errno {error}"
        )


def _argv_sha256(arguments: list[str]) -> str:
    if (
        not arguments
        or not Path(arguments[0]).is_absolute()
        or any(
            not isinstance(value, str)
            or not value
            or "\x00" in value
            for value in arguments
        )
    ):
        raise BoundedCommandError("reported subprocess arguments are invalid")
    digest = hashlib.sha256()
    for value in arguments:
        encoded = os.fsencode(value)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _proc_identity(process_id: int) -> tuple[int, int, int, int, str]:
    try:
        payload = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
        fields = payload[payload.rindex(") ") + 2 :].split()
        if len(fields) < 20:
            raise ValueError("short process stat")
        return (
            int(fields[1], 10),
            int(fields[2], 10),
            int(fields[3], 10),
            int(fields[19], 10),
            fields[0],
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise BoundedCommandError(
            "subprocess identity cannot be read"
        ) from exc


class ControllerLivenessGuard:
    """Bind mutating execution to one controller-owned pipe read end."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _HANDLED_SIGNALS = (
        signal.SIGHUP,
        signal.SIGTERM,
        signal.SIGINT,
        _WAKE_SIGNAL,
    )

    def __init__(self, control_fd: int) -> None:
        _anonymous_pipe_identity(
            control_fd,
            access_mode=os.O_RDONLY,
            reject_opposite_end=True,
            label="controller liveness",
        )
        if threading.current_thread() is not threading.main_thread():
            raise PrecommitWorkerError(
                "mutating precommit action must run in the main thread"
            )
        try:
            self._fd = os.dup(control_fd)
            os.set_inheritable(self._fd, False)
            os.set_blocking(self._fd, False)
        except OSError as exc:
            raise PrecommitWorkerError(
                "controller liveness pipe cannot be secured"
            ) from exc
        self._cancelled = threading.Event()
        self._exception_delivered = threading.Event()
        self._stopping = threading.Event()
        self._reason = "controller liveness was lost"
        self._old_handlers: dict[int, Any] = {}
        self._monitor: threading.Thread | None = None

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

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum == self._WAKE_SIGNAL:
            reason = self._reason
        else:
            reason = f"precommit worker received signal {signum}"
            self._cancel(reason, wake_main=False)
        if self._exception_delivered.is_set():
            return
        self._exception_delivered.set()
        raise PrecommitWorkerCancellation(reason)

    def _monitor_control(self) -> None:
        selector = selectors.DefaultSelector()
        try:
            selector.register(self._fd, selectors.EVENT_READ)
            while not self._stopping.is_set():
                if not selector.select(0.05):
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
                name="precommit-controller-liveness",
                daemon=True,
            )
            self._monitor.start()
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def check(self) -> None:
        if (
            self._cancelled.is_set()
            and not self._exception_delivered.is_set()
        ):
            self._exception_delivered.set()
            raise PrecommitWorkerCancellation(self._reason)

    def _restore(self) -> None:
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

    def __exit__(self, error_type: Any, _value: Any, _traceback: Any) -> None:
        cancelled = self._cancelled.is_set()
        reason = self._reason
        deliver_after_restore = (
            cancelled
            and error_type is None
            and not self._exception_delivered.is_set()
        )
        self._exception_delivered.set()
        self._restore()
        if deliver_after_restore:
            raise PrecommitWorkerCancellation(reason)


@dataclass(frozen=True)
class CommandAuthorization:
    mode: str
    nonce: str
    purpose: str
    argv_sha256: str
    process_id: int | None = None


class ProcessGroupReporter:
    """Bind normal and cleanup commands to separate host ACK channels."""

    def __init__(
        self,
        normal_report_fd: int,
        normal_ack_fd: int,
        cleanup_report_fd: int,
        cleanup_ack_fd: int,
    ) -> None:
        descriptors = (
            (normal_report_fd, os.O_WRONLY, "normal report"),
            (normal_ack_fd, os.O_RDONLY, "normal ACK"),
            (cleanup_report_fd, os.O_WRONLY, "cleanup report"),
            (cleanup_ack_fd, os.O_RDONLY, "cleanup ACK"),
        )
        identities = [
            _anonymous_pipe_identity(
                descriptor,
                access_mode=access,
                reject_opposite_end=True,
                label=label,
            )
            for descriptor, access, label in descriptors
        ]
        if len(set(identities)) != len(identities):
            raise PrecommitWorkerError(
                "process authorization channels must be distinct"
            )
        try:
            self.normal_report_fd = os.dup(normal_report_fd)
            self.normal_ack_fd = os.dup(normal_ack_fd)
            self.cleanup_report_fd = os.dup(cleanup_report_fd)
            self.cleanup_ack_fd = os.dup(cleanup_ack_fd)
            for descriptor in self.descriptors:
                os.set_inheritable(descriptor, False)
        except OSError as exc:
            for descriptor in getattr(self, "descriptors", ()):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            raise PrecommitWorkerError(
                "process-group reporting pipes cannot be secured"
            ) from exc

    @property
    def descriptors(self) -> tuple[int, int, int, int]:
        return (
            self.normal_report_fd,
            self.normal_ack_fd,
            self.cleanup_report_fd,
            self.cleanup_ack_fd,
        )

    def authorize(
        self,
        arguments: list[str],
        *,
        purpose: str,
        cleanup_only: bool,
    ) -> tuple[CommandAuthorization, list[str], tuple[int, int]]:
        if PURPOSE_RE.fullmatch(purpose) is None:
            raise BoundedCommandError("subprocess purpose is invalid")
        authorization = CommandAuthorization(
            mode=(
                COMMAND_MODE_CLEANUP
                if cleanup_only
                else COMMAND_MODE_NORMAL
            ),
            nonce=secrets.token_hex(16),
            purpose=purpose,
            argv_sha256=_argv_sha256(arguments),
        )
        report_fd, ack_fd = (
            (self.cleanup_report_fd, self.cleanup_ack_fd)
            if cleanup_only
            else (self.normal_report_fd, self.normal_ack_fd)
        )
        return (
            authorization,
            [
                PYTHON3,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                BOUNDED_EXEC_WRAPPER,
                authorization.mode,
                authorization.nonce,
                authorization.purpose,
                authorization.argv_sha256,
                str(report_fd),
                str(ack_fd),
                "--",
                *arguments,
            ],
            (report_fd, ack_fd),
        )

    def complete(
        self,
        authorization: CommandAuthorization,
        process_id: int,
    ) -> None:
        report_fd = (
            self.cleanup_report_fd
            if authorization.mode == COMMAND_MODE_CLEANUP
            else self.normal_report_fd
        )
        payload = (
            f"{GROUP_REPORT_DONE}:{authorization.mode}:"
            f"{authorization.nonce}:{process_id}\n"
        ).encode("ascii")
        try:
            written = os.write(report_fd, payload)
        except OSError as exc:
            raise BoundedCommandError(
                "process-group completion could not be reported"
            ) from exc
        if written != len(payload):
            raise BoundedCommandError(
                "process-group completion report was truncated"
            )

    def __enter__(self) -> ProcessGroupReporter:
        global _ACTIVE_GROUP_REPORTER
        if _ACTIVE_GROUP_REPORTER is not None:
            raise PrecommitWorkerError(
                "process-group reporter is already active"
            )
        _enable_child_subreaper()
        _ACTIVE_GROUP_REPORTER = self
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        global _ACTIVE_GROUP_REPORTER
        _ACTIVE_GROUP_REPORTER = None
        for descriptor in self.descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass


_ACTIVE_GROUP_REPORTER: ProcessGroupReporter | None = None
_ACTIVE_LIVENESS_GUARD: ControllerLivenessGuard | None = None


@contextmanager
def _execution_authority(
    *,
    control_fd: int,
    normal_report_fd: int,
    normal_ack_fd: int,
    cleanup_report_fd: int,
    cleanup_ack_fd: int,
):  # noqa: ANN202
    global _ACTIVE_LIVENESS_GUARD
    with (
        ControllerLivenessGuard(control_fd) as liveness,
        ProcessGroupReporter(
            normal_report_fd,
            normal_ack_fd,
            cleanup_report_fd,
            cleanup_ack_fd,
        ),
    ):
        if _ACTIVE_LIVENESS_GUARD is not None:
            raise PrecommitWorkerError(
                "controller liveness guard is already active"
            )
        _ACTIVE_LIVENESS_GUARD = liveness
        try:
            liveness.check()
            yield
        finally:
            _ACTIVE_LIVENESS_GUARD = None


def _bounded_exec_wrapper(argv: list[str]) -> int:
    if (
        len(argv) < 9
        or argv[0] != BOUNDED_EXEC_WRAPPER
        or argv[7] != "--"
    ):
        return 125
    try:
        mode = argv[1]
        nonce = argv[2]
        purpose = argv[3]
        argv_sha256 = argv[4]
        report_fd = int(argv[5], 10)
        ack_fd = int(argv[6], 10)
        arguments = argv[8:]
        if (
            mode not in {COMMAND_MODE_NORMAL, COMMAND_MODE_CLEANUP}
            or NONCE_RE.fullmatch(nonce) is None
            or PURPOSE_RE.fullmatch(purpose) is None
            or SHA256_RE.fullmatch(argv_sha256) is None
            or _argv_sha256(arguments) != argv_sha256
            or report_fd < 0
            or ack_fd < 0
            or report_fd == ack_fd
        ):
            return 125
        _anonymous_pipe_identity(
            report_fd,
            access_mode=os.O_WRONLY,
            reject_opposite_end=True,
            label="wrapper report",
        )
        _anonymous_pipe_identity(
            ack_fd,
            access_mode=os.O_RDONLY,
            reject_opposite_end=True,
            label="wrapper ACK",
        )
        os.setsid()
        process_id = os.getpid()
        _parent, process_group, session, starttime, _state = _proc_identity(
            process_id
        )
        start = (
            f"{GROUP_REPORT_START}:{mode}:{nonce}:{process_id}:"
            f"{starttime}:{process_group}:{session}:{purpose}:"
            f"{argv_sha256}\n"
        ).encode("ascii")
        if (
            process_group != process_id
            or session != process_id
            or os.write(report_fd, start) != len(start)
        ):
            return 125
        expected_ack = (
            b"A" + start[1:]
        )
        ack = bytearray()
        while len(ack) < len(expected_ack):
            chunk = os.read(ack_fd, len(expected_ack) - len(ack))
            if not chunk:
                return 125
            ack.extend(chunk)
        if bytes(ack) != expected_ack:
            return 125
        os.close(report_fd)
        os.close(ack_fd)
        os.execve(arguments[0], arguments, dict(os.environ))
    except (
        OSError,
        ValueError,
        PrecommitWorkerError,
        BoundedCommandError,
    ):
        return 125
    return 125


@dataclass(frozen=True)
class ArtifactBinding:
    sha256: str
    bytes: int
    restored_tree_sha256: str | None


@dataclass(frozen=True)
class ImageArtifactBinding:
    archive_sha256: str
    archive_bytes: int
    config_digest: str
    content_descriptor: Mapping[str, Any]
    content_identity: str


@dataclass(frozen=True)
class PrecommitManifest:
    operation_id: str
    role: str
    release_sha: str
    release_tree_sha: str
    controller_manifest_sha256: str
    approval_sha256: str
    role_material_sha256: str
    canonical_compose_sha256: str
    role_compose_sha256: str
    environment_sha256: str
    worker_sha256: str
    acceptance_producer_sha256: str
    image_artifacts: Mapping[str, ImageArtifactBinding]
    runtime_image_ids: Mapping[str, str]
    artifacts: Mapping[str, ArtifactBinding]
    source_database: Mapping[str, Any]
    target_migration_revision: str
    postgres_runtime_uid: int
    postgres_runtime_gid: int
    canonical_sha256: str


@dataclass(frozen=True)
class OperationPaths:
    project_base: str
    project_name: str
    project_root: Path
    release_root: Path
    data_root: Path
    secret_root: Path
    compose: Path
    environment: Path
    manifest: Path
    journal_directory: Path
    journal: Path
    evidence_directory: Path
    directory_bindings: Path
    artifacts: Mapping[str, Path]


ROLE_SERVICES = {
    "bot_fi": {
        "profile": "bot-fi",
        "database": "bot_fi_db",
        "restore": "bot_fi_restore_tool",
        "migration": "bot_fi_migration",
        "roles": "bot_fi_db_roles",
        "roles_post": None,
        "fencing": "bot_fi_db_fencing",
        "observer": "bot_fi_sync_observer",
        "database_env": "BOT_FI_POSTGRES_DB",
        "network": "bot_fi",
        "redis_service": "bot_fi_redis",
    },
    "webapp_fi": {
        "profile": "webapp-fi",
        "database": "webapp_fi_db",
        "restore": "webapp_fi_restore_tool",
        "migration": "webapp_fi_migration",
        "roles": "webapp_fi_db_roles",
        "roles_post": "webapp_fi_db_roles_post_migration",
        "fencing": "webapp_fi_db_fencing",
        "observer": "webapp_fi_sync_observer",
        "database_env": "WEBAPP_FI_POSTGRES_DB",
        "network": "webapp_fi",
        "redis_service": "webapp_fi_redis",
    },
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PrecommitWorkerError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _canonical_operation_id(value: Any) -> str:
    if not isinstance(value, str):
        raise PrecommitWorkerError("operation id is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise PrecommitWorkerError("operation id is invalid") from exc
    if str(parsed) != value:
        raise PrecommitWorkerError("operation id is not canonical")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not SHA256_RE.fullmatch(value)
        or value == "0" * 64
    ):
        raise PrecommitWorkerError(f"{label} is not a nonzero SHA-256")
    return value


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise PrecommitWorkerError(f"{label} is outside its bound")
    return value


def operation_paths(
    operation_id: str,
    release_sha: str,
    role: str,
) -> OperationPaths:
    operation_id = _canonical_operation_id(operation_id)
    if not SHA40_RE.fullmatch(release_sha) or role not in ROLE_NAMES:
        raise PrecommitWorkerError("operation path binding is invalid")
    role_path = role.replace("_", "-")
    project_base = f"tb3p-{operation_id.replace('-', '')}"
    project_root = PROJECT_ROOT_PREFIX / operation_id
    data_root = DATA_ROOT_PREFIX / operation_id
    secret_root = SECRET_ROOT_PREFIX / operation_id
    restore_input = data_root / "restore-input" / role_path
    journal_directory = secret_root / role_path / "precommit"
    return OperationPaths(
        project_base=project_base,
        project_name=f"{project_base}-{role_path}",
        project_root=project_root,
        release_root=project_root / "releases" / release_sha,
        data_root=data_root,
        secret_root=secret_root,
        compose=project_root / "rendered" / role_path / "docker-compose.yml",
        environment=secret_root / role_path / "runtime.env.role",
        manifest=secret_root / role_path / "precommit-operation.json",
        journal_directory=journal_directory,
        journal=journal_directory / "journal.json",
        evidence_directory=journal_directory / "evidence",
        directory_bindings=journal_directory / "data-directories.json",
        artifacts={
            "release-bundle": project_root / "incoming" / "release.bundle",
            "role-material": project_root
            / "incoming"
            / f"role-material-{role_path}.tar",
            "app-image-archive": project_root
            / "incoming"
            / "app-image.tar",
            "postgres-image-archive": project_root
            / "incoming"
            / "postgres-image.tar",
            "redis-image-archive": project_root
            / "incoming"
            / "redis-image.tar",
            "nginx-image-archive": project_root
            / "incoming"
            / "nginx-image.tar",
            "database-backup": restore_input / "database.dump",
            "uploads-archive": restore_input / "uploads.tar.gz",
            "audit-archive": restore_input / "audit.tar.gz",
        },
    )


def _read_root_file(
    path: Path,
    *,
    label: str,
    maximum: int,
    mode: int = 0o600,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or not 1 <= before.st_size <= maximum
        ):
            raise PrecommitWorkerError(f"{label} is unavailable or unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if len(payload) > maximum or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        ):
            raise PrecommitWorkerError(f"{label} changed while being read")
        return payload
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_root_file(
    path: Path,
    *,
    label: str,
    maximum: int,
) -> tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= maximum
        ):
            raise PrecommitWorkerError(f"{label} is unavailable or unsafe")
        digest = hashlib.sha256()
        observed_bytes = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed_bytes += len(chunk)
            if observed_bytes > maximum:
                raise PrecommitWorkerError(f"{label} exceeds its size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            observed_bytes != before.st_size
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise PrecommitWorkerError(f"{label} changed while being hashed")
        return digest.hexdigest(), observed_bytes
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} is unavailable or unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@contextmanager
def _held_artifact(
    path: Path,
    binding: ArtifactBinding,
    *,
    label: str,
):  # noqa: ANN202
    descriptor = -1
    stream: BinaryIO | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != binding.bytes
        ):
            raise PrecommitWorkerError(f"{label} is unavailable or unsafe")

        def digest_descriptor() -> str:
            os.lseek(descriptor, 0, os.SEEK_SET)
            digest = hashlib.sha256()
            observed = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > binding.bytes:
                    raise PrecommitWorkerError(
                        f"{label} exceeded its bound while being read"
                    )
                digest.update(chunk)
            if observed != binding.bytes:
                raise PrecommitWorkerError(f"{label} was truncated")
            return digest.hexdigest()

        if digest_descriptor() != binding.sha256:
            raise PrecommitWorkerError(f"{label} identity differs")
        os.lseek(descriptor, 0, os.SEEK_SET)
        stream = os.fdopen(descriptor, "rb", closefd=False)
        yield stream
        stream.flush()
        after = os.fstat(descriptor)
        try:
            path_after = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrecommitWorkerError(
                f"{label} path changed while being consumed"
            ) from exc
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            or getattr(before, field) != getattr(path_after, field)
            for field in stable
        ):
            raise PrecommitWorkerError(
                f"{label} changed while being consumed"
            )
        if digest_descriptor() != binding.sha256:
            raise PrecommitWorkerError(
                f"{label} content changed while being consumed"
            )
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} is unavailable or unsafe") from exc
    finally:
        if stream is not None:
            stream.close()
        if descriptor >= 0:
            os.close(descriptor)


def load_manifest(path: Path) -> PrecommitManifest:
    payload = _read_root_file(
        path,
        label="precommit operation manifest",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitWorkerError(
            "precommit operation manifest is invalid JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != MANIFEST_FIELDS
        or document.get("schema") != MANIFEST_SCHEMA
    ):
        raise PrecommitWorkerError(
            "precommit operation manifest fields are not exact"
        )
    operation_id = _canonical_operation_id(document["operation_id"])
    role = document.get("role")
    release_sha = document.get("release_sha")
    release_tree_sha = document.get("release_tree_sha")
    target_revision = document.get("target_migration_revision")
    if (
        role not in ROLE_NAMES
        or not isinstance(release_sha, str)
        or not SHA40_RE.fullmatch(release_sha)
        or not isinstance(release_tree_sha, str)
        or not SHA40_RE.fullmatch(release_tree_sha)
        or not isinstance(target_revision, str)
        or not REVISION_RE.fullmatch(target_revision)
        or document["postgres_runtime_uid"] != POSTGRES_RUNTIME_UID
        or document["postgres_runtime_gid"] != POSTGRES_RUNTIME_GID
    ):
        raise PrecommitWorkerError(
            "precommit release, role, or migration identity is invalid"
        )
    paths = operation_paths(operation_id, release_sha, role)
    if path != paths.manifest:
        raise PrecommitWorkerError(
            "precommit operation manifest path is not operation-derived"
        )

    runtime_image_ids = document.get("runtime_image_ids")
    if (
        not isinstance(runtime_image_ids, dict)
        or set(runtime_image_ids) != IMAGE_FIELDS
        or len(set(runtime_image_ids.values())) != len(runtime_image_ids)
        or any(
            not isinstance(value, str)
            or not IMAGE_ID_RE.fullmatch(value)
            or value == f"sha256:{'0' * 64}"
            for value in runtime_image_ids.values()
        )
    ):
        raise PrecommitWorkerError(
            "precommit runtime image inventory is invalid"
        )

    raw_image_artifacts = document.get("image_artifacts")
    if (
        not isinstance(raw_image_artifacts, dict)
        or set(raw_image_artifacts) != IMAGE_FIELDS
    ):
        raise PrecommitWorkerError(
            "precommit image artifact inventory is invalid"
        )
    image_artifacts: dict[str, ImageArtifactBinding] = {}
    for kind in IMAGE_FIELDS:
        row = raw_image_artifacts[kind]
        if (
            not isinstance(row, dict)
            or set(row) != IMAGE_ARTIFACT_FIELDS
        ):
            raise PrecommitWorkerError(
                "precommit image artifact fields are not exact"
            )
        config_digest = row["config_digest"]
        if (
            not isinstance(config_digest, str)
            or IMAGE_ID_RE.fullmatch(config_digest) is None
            or config_digest == f"sha256:{'0' * 64}"
        ):
            raise PrecommitWorkerError(
                f"{kind} image config digest is invalid"
            )
        content_identity = row["content_identity"]
        if (
            not isinstance(content_identity, str)
            or IMAGE_ID_RE.fullmatch(content_identity) is None
            or content_identity == f"sha256:{'0' * 64}"
        ):
            raise PrecommitWorkerError(
                f"{kind} image content identity is invalid"
            )
        try:
            observed_content_identity = verify_content_descriptor(
                row["content_descriptor"]
            )
        except DockerImageIdentityError as exc:
            raise PrecommitWorkerError(
                f"{kind} image content descriptor is invalid"
            ) from exc
        if (
            row["content_descriptor"]["architecture"] != "amd64"
            or row["content_descriptor"]["os"] != "linux"
            or observed_content_identity != content_identity
        ):
            raise PrecommitWorkerError(
                f"{kind} image content identity differs"
            )
        image_artifacts[kind] = ImageArtifactBinding(
            archive_sha256=_nonzero_sha256(
                row["archive_sha256"],
                label=f"{kind} image archive",
            ),
            archive_bytes=_bounded_int(
                row["archive_bytes"],
                minimum=1,
                maximum=MAX_FILE_BYTES,
                label=f"{kind} image archive bytes",
            ),
            config_digest=config_digest,
            content_descriptor=dict(row["content_descriptor"]),
            content_identity=content_identity,
        )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {getattr(image_artifacts[kind], field) for kind in IMAGE_FIELDS}
        ) != len(IMAGE_FIELDS):
            raise PrecommitWorkerError(
                f"precommit image {field} values must be distinct"
            )

    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, dict) or set(raw_artifacts) != set(
        ARTIFACT_KINDS
    ):
        raise PrecommitWorkerError("precommit artifact inventory is incomplete")
    artifacts: dict[str, ArtifactBinding] = {}
    for kind in ARTIFACT_KINDS:
        row = raw_artifacts[kind]
        if not isinstance(row, dict) or set(row) != ARTIFACT_FIELDS:
            raise PrecommitWorkerError("precommit artifact fields are not exact")
        tree = row["restored_tree_sha256"]
        if kind in {"uploads-archive", "audit-archive"}:
            tree = _nonzero_sha256(tree, label=f"{kind} restored tree")
        elif tree is not None:
            raise PrecommitWorkerError(
                f"{kind} must not declare a restored tree digest"
            )
        artifacts[kind] = ArtifactBinding(
            sha256=_nonzero_sha256(row["sha256"], label=kind),
            bytes=_bounded_int(
                row["bytes"],
                minimum=1,
                maximum=MAX_FILE_BYTES,
                label=f"{kind} bytes",
            ),
            restored_tree_sha256=tree,
        )

    source_database = document.get("source_database")
    if (
        not isinstance(source_database, dict)
        or set(source_database) != SOURCE_DATABASE_FIELDS
        or not isinstance(source_database["alembic_revision"], str)
        or not REVISION_RE.fullmatch(source_database["alembic_revision"])
        or source_database["fingerprint_algorithm"]
        != "pg-copy-jsonl-sha256-canonical-session-v1"
    ):
        raise PrecommitWorkerError("source database binding is invalid")
    _nonzero_sha256(
        source_database["database_fingerprint_sha256"],
        label="source database fingerprint",
    )
    _bounded_int(
        source_database["row_count"],
        minimum=0,
        maximum=10**15,
        label="source database row count",
    )
    _bounded_int(
        source_database["table_count"],
        minimum=1,
        maximum=100_000,
        label="source database table count",
    )
    for field in (
        "controller_manifest_sha256",
        "approval_sha256",
        "role_material_sha256",
        "canonical_compose_sha256",
        "role_compose_sha256",
        "environment_sha256",
        "worker_sha256",
        "acceptance_producer_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    if (
        artifacts["role-material"].sha256
        != document["role_material_sha256"]
    ):
        raise PrecommitWorkerError(
            "role material artifact differs from its top-level binding"
        )
    image_archive_digests = {
        artifacts[f"{kind}-image-archive"].sha256
        for kind in IMAGE_FIELDS
    }
    if len(image_archive_digests) != len(IMAGE_FIELDS):
        raise PrecommitWorkerError(
            "precommit image archive digests must be distinct"
        )
    for kind in IMAGE_FIELDS:
        artifact = artifacts[f"{kind}-image-archive"]
        binding = image_artifacts[kind]
        if (
            artifact.sha256 != binding.archive_sha256
            or artifact.bytes != binding.archive_bytes
        ):
            raise PrecommitWorkerError(
                f"{kind} image archive differs from its content binding"
            )
    return PrecommitManifest(
        operation_id=operation_id,
        role=role,
        release_sha=release_sha,
        release_tree_sha=release_tree_sha,
        controller_manifest_sha256=document["controller_manifest_sha256"],
        approval_sha256=document["approval_sha256"],
        role_material_sha256=document["role_material_sha256"],
        canonical_compose_sha256=document["canonical_compose_sha256"],
        role_compose_sha256=document["role_compose_sha256"],
        environment_sha256=document["environment_sha256"],
        worker_sha256=document["worker_sha256"],
        acceptance_producer_sha256=document[
            "acceptance_producer_sha256"
        ],
        image_artifacts=image_artifacts,
        runtime_image_ids=dict(runtime_image_ids),
        artifacts=artifacts,
        source_database=dict(source_database),
        target_migration_revision=target_revision,
        postgres_runtime_uid=document["postgres_runtime_uid"],
        postgres_runtime_gid=document["postgres_runtime_gid"],
        canonical_sha256=hashlib.sha256(_canonical_json(document)).hexdigest(),
    )


def confirmation_phrase(manifest: PrecommitManifest, action: str) -> str:
    if action not in ACTIONS:
        raise PrecommitWorkerError("precommit action is invalid")
    return (
        f"prepare-precommit:{manifest.operation_id}:{manifest.role}:"
        f"{action}:{manifest.release_sha}"
    )


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class ProcessIdentity:
    process_id: int
    parent_id: int
    process_group: int
    session_id: int
    starttime: int
    state: str

    @property
    def key(self) -> tuple[int, int]:
        return self.process_id, self.starttime


def _process_snapshot() -> dict[int, ProcessIdentity]:
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise BoundedCommandError(
            "process closure cannot be enumerated"
        ) from exc
    observed: dict[int, ProcessIdentity] = {}
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        process_id = int(entry.name, 10)
        try:
            parent, group, session, starttime, state = _proc_identity(
                process_id
            )
        except BoundedCommandError:
            continue
        observed[process_id] = ProcessIdentity(
            process_id=process_id,
            parent_id=parent,
            process_group=group,
            session_id=session,
            starttime=starttime,
            state=state,
        )
    return observed


def _direct_child_baseline() -> frozenset[tuple[int, int]]:
    owner = os.getpid()
    return frozenset(
        identity.key
        for identity in _process_snapshot().values()
        if identity.parent_id == owner
    )


def _owned_processes(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
    include_zombies: bool = False,
) -> tuple[ProcessIdentity, ...]:
    snapshot = _process_snapshot()
    owned_ids = {root_process_id}
    changed = True
    while changed:
        changed = False
        for identity in snapshot.values():
            if (
                identity.process_id not in owned_ids
                and identity.parent_id in owned_ids
            ):
                owned_ids.add(identity.process_id)
                changed = True
    owner = os.getpid()
    for identity in snapshot.values():
        if (
            identity.parent_id == owner
            and identity.key not in baseline_children
        ):
            owned_ids.add(identity.process_id)
    return tuple(
        identity
        for process_id, identity in snapshot.items()
        if process_id in owned_ids
        and (include_zombies or identity.state != "Z")
    )


def _signal_process_identity(
    identity: ProcessIdentity,
    signum: int,
) -> None:
    try:
        current = _proc_identity(identity.process_id)
    except BoundedCommandError:
        return
    if current[3] != identity.starttime:
        return
    try:
        descriptor = os.pidfd_open(identity.process_id, 0)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedCommandError(
            "identity-bound process handle cannot be opened"
        ) from exc
    try:
        refreshed = _proc_identity(identity.process_id)
        if refreshed[3] != identity.starttime:
            return
        signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BoundedCommandError(
            "identity-bound process signal failed"
        ) from exc
    finally:
        os.close(descriptor)


def _reap_owned_zombies(
    root_process_id: int,
    *,
    baseline_children: frozenset[tuple[int, int]],
) -> None:
    owner = os.getpid()
    while True:
        reaped = False
        for identity in _owned_processes(
            root_process_id,
            baseline_children=baseline_children,
            include_zombies=True,
        ):
            if (
                identity.process_id == root_process_id
                or identity.parent_id != owner
                or identity.state != "Z"
            ):
                continue
            try:
                waited, _status = os.waitpid(identity.process_id, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                continue
            except OSError as exc:
                raise BoundedCommandError(
                    "adopted subprocess child could not be reaped"
                ) from exc
            reaped |= waited == identity.process_id
        if not reaped:
            return


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    baseline_children: frozenset[tuple[int, int]] = frozenset(),
) -> None:
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_GROUP_TERM_SECONDS
    while (
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
        and time.monotonic() < deadline
    ):
        process.poll()
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    for identity in reversed(
        _owned_processes(
            process.pid,
            baseline_children=baseline_children,
        )
    ):
        _signal_process_identity(identity, signal.SIGKILL)
    try:
        process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=PROCESS_GROUP_TERM_SECONDS)
    absence_deadline = (
        time.monotonic()
        + PROCESS_GROUP_TERM_SECONDS
        + PROCESS_TREE_QUIESCENCE_SECONDS
    )
    stable_since: float | None = None
    while time.monotonic() < absence_deadline:
        _reap_owned_zombies(
            process.pid,
            baseline_children=baseline_children,
        )
        owned = _owned_processes(
            process.pid,
            baseline_children=baseline_children,
            include_zombies=True,
        )
        if owned:
            stable_since = None
            for identity in reversed(owned):
                if identity.state != "Z":
                    _signal_process_identity(identity, signal.SIGKILL)
        else:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= PROCESS_TREE_QUIESCENCE_SECONDS
            ):
                return
        time.sleep(0.01)
    _reap_owned_zombies(
        process.pid,
        baseline_children=baseline_children,
    )
    if _owned_processes(
        process.pid,
        baseline_children=baseline_children,
        include_zombies=True,
    ):
        raise BoundedCommandError(
            "subprocess descendant closure survived forced cleanup"
        )


def _validate_command_limits(
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
) -> None:
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
        or type(stdout_limit) is not int
        or stdout_limit < 1
        or type(stderr_limit) is not int
        or stderr_limit < 1
    ):
        raise BoundedCommandError("subprocess limits are invalid")


def _bounded_command(
    arguments: list[str],
    *,
    timeout: float,
    env: Mapping[str, str],
    stdin: BinaryIO | int | None,
    stdout_limit: int,
    stderr_limit: int,
    purpose: str = "normal-command",
    cleanup_only: bool = False,
) -> BoundedCommandResult:
    _validate_command_limits(
        timeout=timeout,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
    )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    group_cleaned = False
    reporter = _ACTIVE_GROUP_REPORTER
    authorization: CommandAuthorization | None = None
    pass_fds: tuple[int, ...] = ()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    if (
        reporter is not None
        and not cleanup_only
        and _ACTIVE_LIVENESS_GUARD is not None
    ):
        _ACTIVE_LIVENESS_GUARD.check()
    if reporter is not None:
        authorization, popen_arguments, report_fds = reporter.authorize(
            arguments,
            purpose=purpose,
            cleanup_only=cleanup_only,
        )
        pass_fds = report_fds
    else:
        popen_arguments = arguments
    try:
        process = subprocess.Popen(  # noqa: S603
            popen_arguments,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=reporter is None,
        )
        if process.stdout is None or process.stderr is None:
            raise BoundedCommandError("subprocess pipes are unavailable")
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedCommandError("subprocess timed out")
            events = selector.select(min(0.1, remaining))
            if not events:
                if process.poll() is not None and not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
                    group_cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                buffer = buffers[label]
                limit = (
                    stdout_limit if label == "stdout" else stderr_limit
                )
                if len(buffer) + len(chunk) > limit:
                    raise BoundedCommandError(
                        f"subprocess {label} exceeded its byte limit"
                    )
                buffer.extend(chunk)
            if process.poll() is not None and not group_cleaned:
                _terminate_process_group(
                    process,
                    baseline_children=baseline_children,
                )
                group_cleaned = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedCommandError("subprocess timed out")
        returncode = process.wait(timeout=remaining)
        return BoundedCommandResult(
            returncode=returncode,
            stdout=bytes(buffers["stdout"]),
            stderr=bytes(buffers["stderr"]),
        )
    except BoundedCommandError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundedCommandError("subprocess execution failed") from exc
    finally:
        selector.close()
        if process is not None:
            cleanup_complete = False
            try:
                if not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
                cleanup_complete = True
            finally:
                try:
                    if (
                        cleanup_complete
                        and reporter is not None
                        and authorization is not None
                    ):
                        reporter.complete(authorization, process.pid)
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()


def _run(
    arguments: list[str],
    *,
    timeout: float,
    env: Mapping[str, str] = _SAFE_ENV,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
    purpose: str = "normal-command",
    cleanup_only: bool = False,
) -> str:
    try:
        result = _bounded_command(
            arguments,
            stdin=stdin,
            timeout=timeout,
            env=dict(env),
            stdout_limit=MAX_COMMAND_STDOUT_BYTES,
            stderr_limit=MAX_COMMAND_STDERR_BYTES,
            purpose=purpose,
            cleanup_only=cleanup_only,
        )
    except BoundedCommandError as exc:
        raise PrecommitWorkerError(
            f"required command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if result.returncode != 0:
        raise PrecommitWorkerError(
            f"required command failed closed: {Path(arguments[0]).name}"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise PrecommitWorkerError(
            "required command returned non-UTF-8 output"
        ) from exc


def _bounded_streaming_sha256(
    arguments: list[str],
    *,
    timeout: float,
    stdin: BinaryIO | int | None,
    env: Mapping[str, str],
    purpose: str = "normal-stream",
    cleanup_only: bool = False,
) -> StreamDigest:
    _validate_command_limits(
        timeout=timeout,
        stdout_limit=MAX_FILE_BYTES,
        stderr_limit=MAX_COMMAND_STDERR_BYTES,
    )
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    digest = hashlib.sha256()
    stdout_bytes = 0
    stderr_bytes = 0
    records = 0
    last_byte: int | None = None
    deadline = time.monotonic() + timeout
    group_cleaned = False
    reporter = _ACTIVE_GROUP_REPORTER
    authorization: CommandAuthorization | None = None
    pass_fds: tuple[int, ...] = ()
    _enable_child_subreaper()
    baseline_children = _direct_child_baseline()
    if (
        reporter is not None
        and not cleanup_only
        and _ACTIVE_LIVENESS_GUARD is not None
    ):
        _ACTIVE_LIVENESS_GUARD.check()
    if reporter is not None:
        authorization, popen_arguments, report_fds = reporter.authorize(
            arguments,
            purpose=purpose,
            cleanup_only=cleanup_only,
        )
        pass_fds = report_fds
    else:
        popen_arguments = arguments
    try:
        process = subprocess.Popen(  # noqa: S603
            popen_arguments,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=reporter is None,
        )
        if process.stdout is None or process.stderr is None:
            raise BoundedCommandError(
                "streaming subprocess pipes are unavailable"
            )
        for label, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        ):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedCommandError(
                    "streaming subprocess timed out"
                )
            events = selector.select(min(0.1, remaining))
            if not events:
                if process.poll() is not None and not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
                    group_cleaned = True
                continue
            for key, _mask in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 1024 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_bytes += len(chunk)
                    if stdout_bytes > MAX_FILE_BYTES:
                        raise BoundedCommandError(
                            "streaming stdout exceeded its byte limit"
                        )
                    digest.update(chunk)
                    records += chunk.count(b"\n")
                    last_byte = chunk[-1]
                else:
                    stderr_bytes += len(chunk)
                    if stderr_bytes > MAX_COMMAND_STDERR_BYTES:
                        raise BoundedCommandError(
                            "streaming stderr exceeded its byte limit"
                        )
            if process.poll() is not None and not group_cleaned:
                _terminate_process_group(
                    process,
                    baseline_children=baseline_children,
                )
                group_cleaned = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedCommandError("streaming subprocess timed out")
        returncode = process.wait(timeout=remaining)
        if returncode != 0:
            raise BoundedCommandError(
                "streaming subprocess failed closed"
            )
        if stdout_bytes and last_byte != ord("\n"):
            raise BoundedCommandError(
                "streaming subprocess returned a truncated record"
            )
        return StreamDigest(
            sha256=digest.hexdigest(),
            bytes=stdout_bytes,
            records=records,
        )
    except BoundedCommandError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        raise BoundedCommandError(
            "streaming subprocess execution failed"
        ) from exc
    finally:
        selector.close()
        if process is not None:
            cleanup_complete = False
            try:
                if not group_cleaned:
                    _terminate_process_group(
                        process,
                        baseline_children=baseline_children,
                    )
                cleanup_complete = True
            finally:
                try:
                    if (
                        cleanup_complete
                        and reporter is not None
                        and authorization is not None
                    ):
                        reporter.complete(authorization, process.pid)
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
                    if process.stderr is not None:
                        process.stderr.close()


def _run_streaming_sha256(
    arguments: list[str],
    *,
    timeout: float,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
    env: Mapping[str, str] = _SAFE_ENV,
    purpose: str = "normal-stream",
    cleanup_only: bool = False,
) -> StreamDigest:
    try:
        return _bounded_streaming_sha256(
            arguments,
            timeout=timeout,
            stdin=stdin,
            env=env,
            purpose=purpose,
            cleanup_only=cleanup_only,
        )
    except BoundedCommandError as exc:
        raise ProductionOperationError(
            "required streaming command failed closed"
        ) from exc


def _compose_base(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> list[str]:
    return [
        DOCKER,
        "compose",
        "--project-name",
        paths.project_name,
        "--env-file",
        str(paths.environment),
        "--file",
        str(paths.compose),
    ]


def _load_json_output(raw: str, *, label: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_strict_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise PrecommitWorkerError(f"{label} returned invalid JSON") from exc


def _git_argv(*arguments: str) -> list[str]:
    return [GIT, *_GIT_CONFIG_ARGUMENTS, *arguments]


def _verify_release(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> None:
    heads = _run(
        _git_argv(
            "bundle",
            "list-heads",
            str(paths.artifacts["release-bundle"]),
        ),
        timeout=60,
        env=_SAFE_GIT_ENV,
    )
    if manifest.release_sha not in {
        row.split()[0] for row in heads.splitlines() if row
    }:
        raise PrecommitWorkerError("release bundle lacks the exact release")
    head = _run(
        _git_argv("-C", str(paths.release_root), "rev-parse", "HEAD"),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    tree = _run(
        _git_argv(
            "-C",
            str(paths.release_root),
            "rev-parse",
            "HEAD^{tree}",
        ),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    status = _run(
        _git_argv(
            "-C",
            str(paths.release_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    branch = _run(
        _git_argv(
            "-C",
            str(paths.release_root),
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    remotes = _run(
        _git_argv("-C", str(paths.release_root), "remote"),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    top = _run(
        _git_argv(
            "-C",
            str(paths.release_root),
            "rev-parse",
            "--show-toplevel",
        ),
        timeout=30,
        env=_SAFE_GIT_ENV,
    )
    if (
        head != manifest.release_sha
        or tree != manifest.release_tree_sha
        or status
        or branch != "HEAD"
        or remotes
        or top != str(paths.release_root)
    ):
        raise PrecommitWorkerError(
            "materialized release is not exact, detached, clean, and isolated"
        )
    for relative, expected in (
        (
            "scripts/production_shadow_precommit_worker.py",
            manifest.worker_sha256,
        ),
        (
            "scripts/produce_production_shadow_readonly_acceptance.py",
            manifest.acceptance_producer_sha256,
        ),
    ):
        source = paths.release_root / relative
        descriptor = -1
        try:
            descriptor = os.open(
                source,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != 0o644
                or not 1 <= metadata.st_size <= MAX_JSON_BYTES
            ):
                raise PrecommitWorkerError(
                    "release-owned executable source is unsafe"
                )
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            stable = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_gid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if any(
                getattr(metadata, field) != getattr(after, field)
                for field in stable
            ):
                raise PrecommitWorkerError(
                    "release-owned executable source changed while read"
                )
            tree_entry = _run(
                _git_argv(
                    "-C",
                    str(paths.release_root),
                    "ls-tree",
                    "HEAD",
                    "--",
                    relative,
                ),
                timeout=30,
                env=_SAFE_GIT_ENV,
            )
            blob = _run(
                _git_argv("hash-object", "--no-filters", str(source)),
                timeout=30,
                env=_SAFE_GIT_ENV,
            )
            expected_entry = f"100644 blob {blob}\t{relative}"
            if (
                digest.hexdigest() != expected
                or tree_entry != expected_entry
            ):
                raise PrecommitWorkerError(
                    "release-owned executable source differs from Git"
                )
        except OSError as exc:
            raise PrecommitWorkerError(
                "release-owned executable source is unavailable"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def _verify_images(manifest: PrecommitManifest) -> None:
    for role, expected in manifest.runtime_image_ids.items():
        document = _load_json_output(
            _run([DOCKER, "image", "inspect", expected], timeout=30),
            label="Docker image inspection",
        )
        if (
            not isinstance(document, list)
            or len(document) != 1
            or not isinstance(document[0], dict)
            or document[0].get("Id") != expected
        ):
            raise PrecommitWorkerError("immutable image identity differs")
        try:
            descriptor, identity = image_content_descriptor(document[0])
        except DockerImageIdentityError as exc:
            raise PrecommitWorkerError(
                "loaded image content descriptor is invalid"
            ) from exc
        expected_content = manifest.image_artifacts[role]
        if (
            descriptor != expected_content.content_descriptor
            or identity != expected_content.content_identity
        ):
            raise PrecommitWorkerError(
                "loaded image content identity differs"
            )
        if role in {"app", "postgres"}:
            config = document[0].get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            if (
                not isinstance(labels, dict)
                or labels.get("org.opencontainers.image.revision")
                != manifest.release_sha
            ):
                raise PrecommitWorkerError(
                    "release-bound image label differs"
                )
        if role == "postgres" and (
            labels.get("trading-bot.postgres.runtime-uid")
            != str(POSTGRES_RUNTIME_UID)
            or labels.get("trading-bot.postgres.runtime-gid")
            != str(POSTGRES_RUNTIME_GID)
        ):
            raise PrecommitWorkerError(
                "PostgreSQL runtime UID/GID image labels differ"
            )


def _verify_artifacts(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> None:
    expected_files = {
        **paths.artifacts,
        "compose": paths.compose,
        "environment": paths.environment,
    }
    expected_bindings: dict[str, tuple[str, int | None]] = {
        kind: (binding.sha256, binding.bytes)
        for kind, binding in manifest.artifacts.items()
    }
    expected_bindings.update(
        {
            "compose": (manifest.role_compose_sha256, None),
            "environment": (manifest.environment_sha256, None),
        }
    )
    for kind, path in expected_files.items():
        observed = _hash_root_file(
            path,
            label=f"precommit {kind}",
            maximum=MAX_FILE_BYTES,
        )
        expected_sha, expected_bytes = expected_bindings[kind]
        if observed[0] != expected_sha or (
            expected_bytes is not None and observed[1] != expected_bytes
        ):
            raise PrecommitWorkerError(
                f"precommit {kind} identity differs"
            )


def _verify_image_archives(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> None:
    for kind in sorted(IMAGE_FIELDS):
        artifact_kind = f"{kind}-image-archive"
        binding = manifest.artifacts[artifact_kind]
        image = Image(
            role=kind,
            artifact_kind=artifact_kind,
            image_id=manifest.image_artifacts[kind].config_digest,
            repo_tags=(),
            os="linux",
            architecture="amd64",
            runtime_uid=(
                POSTGRES_RUNTIME_UID if kind == "postgres" else None
            ),
            runtime_gid=(
                POSTGRES_RUNTIME_GID if kind == "postgres" else None
            ),
        )
        try:
            with _held_artifact(
                paths.artifacts[artifact_kind],
                binding,
                label=f"precommit {artifact_kind}",
            ) as stream:
                config_digest, content_descriptor, content_identity, labels = (
                    _docker_archive_attestation(stream)
                )
                expected_image = manifest.image_artifacts[kind]
                if (
                    config_digest != expected_image.config_digest
                    or content_descriptor
                    != expected_image.content_descriptor
                    or content_identity != expected_image.content_identity
                ):
                    raise PrecommitWorkerError(
                        f"precommit {artifact_kind} content identity differs"
                    )
                if kind in {"app", "postgres"} and (
                    labels.get("org.opencontainers.image.revision")
                    != manifest.release_sha
                ):
                    raise PrecommitWorkerError(
                        f"precommit {artifact_kind} release label differs"
                    )
                if kind == "postgres" and (
                    labels.get("trading-bot.postgres.runtime-uid")
                    != str(POSTGRES_RUNTIME_UID)
                    or labels.get("trading-bot.postgres.runtime-gid")
                    != str(POSTGRES_RUNTIME_GID)
                ):
                    raise PrecommitWorkerError(
                        "PostgreSQL archive runtime UID/GID labels differ"
                    )
                stream.seek(0)
                _docker_archive_identity(
                    Path(f"/proc/self/fd/{stream.fileno()}"),
                    image,
                    release_sha=manifest.release_sha,
                )
        except ProductionOperationError as exc:
            raise PrecommitWorkerError(
                f"precommit {artifact_kind} semantic identity differs"
            ) from exc


def _docker_archive_attestation(
    stream: BinaryIO,
) -> tuple[str, Mapping[str, Any], str, Mapping[str, str]]:
    try:
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            members = {
                member.name.rstrip("/"): member
                for member in archive.getmembers()
                if member.isreg()
            }
            manifest_member = members.get("manifest.json")
            if (
                manifest_member is None
                or not 1 <= manifest_member.size <= MAX_JSON_BYTES
            ):
                raise PrecommitWorkerError(
                    "Docker archive manifest is unavailable"
                )
            source = archive.extractfile(manifest_member)
            if source is None:
                raise PrecommitWorkerError(
                    "Docker archive manifest is unreadable"
                )
            raw_manifest = source.read(MAX_JSON_BYTES + 1)
            source.close()
            document = json.loads(
                raw_manifest.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
            if (
                not isinstance(document, list)
                or len(document) != 1
                or not isinstance(document[0], dict)
                or not isinstance(document[0].get("Config"), str)
            ):
                raise PrecommitWorkerError(
                    "Docker archive manifest entry is invalid"
                )
            config_name = PurePosixPath(document[0]["Config"])
            if (
                config_name.is_absolute()
                or ".." in config_name.parts
                or config_name.as_posix() not in members
            ):
                raise PrecommitWorkerError(
                    "Docker archive config path is invalid"
                )
            config_member = members[config_name.as_posix()]
            if not 1 <= config_member.size <= 16 * 1024 * 1024:
                raise PrecommitWorkerError(
                    "Docker archive config is oversized"
                )
            config_source = archive.extractfile(config_member)
            if config_source is None:
                raise PrecommitWorkerError(
                    "Docker archive config is unreadable"
                )
            raw_config = config_source.read(16 * 1024 * 1024 + 1)
            config_source.close()
            config = json.loads(
                raw_config.decode("utf-8"),
                object_pairs_hook=_strict_object,
            )
            if not isinstance(config, dict):
                raise PrecommitWorkerError(
                    "Docker archive config is invalid"
                )
            values = config.get("config") if isinstance(config, dict) else None
            labels = values.get("Labels") if isinstance(values, dict) else None
            if (
                not isinstance(labels, dict)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in labels.items()
                )
            ):
                raise PrecommitWorkerError(
                    "Docker archive labels are invalid"
                )
            try:
                descriptor, identity = (
                    image_content_descriptor_from_archive_config(config)
                )
            except DockerImageIdentityError as exc:
                raise PrecommitWorkerError(
                    "Docker archive content descriptor is invalid"
                ) from exc
            return (
                f"sha256:{hashlib.sha256(raw_config).hexdigest()}",
                descriptor,
                identity,
                dict(labels),
            )
    except PrecommitWorkerError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        raise PrecommitWorkerError(
            "Docker archive content validation failed"
        ) from exc
    finally:
        stream.seek(0)


def _verify_role_material(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> Mapping[str, str]:
    raw = _read_root_file(
        paths.environment,
        label="precommit role environment",
        maximum=MAX_JSON_BYTES,
    )
    try:
        values = parse_env_values(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RuntimeError) as exc:
        raise PrecommitWorkerError(
            "precommit role environment is invalid"
        ) from exc
    expected = {
        "PRODUCTION_SHADOW_OPERATION_ID": manifest.operation_id,
        "PRODUCTION_SHADOW_PROJECT": paths.project_base,
        "PRODUCTION_SHADOW_CGROUP_PARENT": paths.project_base,
        "PRODUCTION_SHADOW_PROJECT_ROOT": str(paths.project_root),
        "PRODUCTION_SHADOW_RELEASE_ROOT": str(paths.release_root),
        "PRODUCTION_SHADOW_DATA_ROOT": str(paths.data_root),
        "PRODUCTION_SHADOW_SECRET_ROOT": str(paths.secret_root),
        "PRODUCTION_SHADOW_RELEASE_SHA": manifest.release_sha,
        "PRODUCTION_SHADOW_APP_IMAGE_ID": manifest.runtime_image_ids["app"],
        "PRODUCTION_SHADOW_POSTGRES_IMAGE_ID": (
            manifest.runtime_image_ids["postgres"]
        ),
        "PRODUCTION_SHADOW_REDIS_IMAGE_ID": manifest.runtime_image_ids["redis"],
        "PRODUCTION_SHADOW_NGINX_IMAGE_ID": manifest.runtime_image_ids["nginx"],
    }
    if any(values.get(name) != value for name, value in expected.items()):
        raise PrecommitWorkerError(
            "role environment differs from canonical operation identity"
        )
    database_name = values.get(ROLE_SERVICES[manifest.role]["database_env"], "")
    if not NAME_RE.fullmatch(database_name):
        raise PrecommitWorkerError("operation database name is invalid")
    return values


STORE_NAMES = ("postgres", "redis", "uploads", "audit")
DIRECTORY_BINDING_FIELDS = frozenset(
    {"path", "device", "inode", "initial_uid", "initial_gid", "mode"}
)


def _directory_metadata(
    path: Path,
    *,
    label: str,
    exact_mode: int | None = None,
    allowed_owners: frozenset[tuple[int, int]] = frozenset({(0, 0)}),
) -> os.stat_result:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) not in allowed_owners
        or (
            stat.S_IMODE(metadata.st_mode) != exact_mode
            if exact_mode is not None
            else bool(stat.S_IMODE(metadata.st_mode) & 0o022)
        )
    ):
        raise PrecommitWorkerError(f"{label} directory is unsafe")
    return metadata


def _ensure_private_directory(path: Path, *, label: str) -> os.stat_result:
    try:
        return _directory_metadata(
            path,
            label=label,
            exact_mode=0o700,
        )
    except PrecommitWorkerError:
        if path.exists() or path.is_symlink():
            raise
    _directory_metadata(path.parent, label=f"{label} parent")
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise PrecommitWorkerError(f"{label} directory could not be created") from exc
    return _directory_metadata(path, label=label, exact_mode=0o700)


def _stable_directory_entries(
    path: Path,
    *,
    label: str,
    allowed_owners: frozenset[tuple[int, int]],
) -> tuple[os.stat_result, list[str]]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        entries = sorted(os.listdir(descriptor))
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
        )
        if (
            not stat.S_ISDIR(before.st_mode)
            or (before.st_uid, before.st_gid) not in allowed_owners
            or stat.S_IMODE(before.st_mode) != 0o700
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable
            )
        ):
            raise PrecommitWorkerError(f"{label} directory changed or is unsafe")
        return before, entries
    except OSError as exc:
        raise PrecommitWorkerError(
            f"{label} directory cannot be opened safely"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_operation_directory_chains(paths: OperationPaths) -> None:
    role_path = paths.manifest.parent.name
    for path, label, exact_mode in (
        (paths.project_root, "operation project root", None),
        (paths.release_root, "operation release root", None),
        (paths.compose.parent, "rendered role directory", None),
        (paths.secret_root, "operation secret root", 0o700),
        (paths.secret_root / role_path, "role secret root", 0o700),
        (
            paths.data_root / "restore-input",
            "restore input root",
            0o700,
        ),
        (
            paths.data_root / "restore-input" / role_path,
            "role restore input",
            0o700,
        ),
    ):
        _directory_metadata(
            path,
            label=label,
            exact_mode=exact_mode,
        )


def _new_directory_bindings(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    try:
        if not DATA_ROOT_PREFIX.exists():
            _directory_metadata(
                DATA_ROOT_PREFIX.parent,
                label="operation data prefix parent",
            )
            DATA_ROOT_PREFIX.mkdir(mode=0o700)
    except OSError as exc:
        raise PrecommitWorkerError(
            "operation data prefix could not be created"
        ) from exc
    _directory_metadata(
        DATA_ROOT_PREFIX,
        label="operation data prefix",
    )
    _ensure_private_directory(paths.data_root, label="operation data root")
    role_root = paths.data_root / manifest.role.replace("_", "-")
    _ensure_private_directory(role_root, label="role data root")
    stores: dict[str, Any] = {}
    for store in STORE_NAMES:
        target = role_root / store
        metadata = _ensure_private_directory(
            target,
            label=f"{store} data",
        )
        stable, entries = _stable_directory_entries(
            target,
            label=f"{store} data",
            allowed_owners=frozenset({(0, 0)}),
        )
        if entries:
            raise PrecommitWorkerError(
                f"preexisting {store} data directory is not empty"
            )
        stores[store] = {
            "path": str(target),
            "device": stable.st_dev,
            "inode": stable.st_ino,
            "initial_uid": metadata.st_uid,
            "initial_gid": metadata.st_gid,
            "mode": "0700",
        }
    document = {
        "schema": DIRECTORY_BINDINGS_SCHEMA,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "stores": stores,
    }
    try:
        write_secure_new_bytes(
            paths.directory_bindings,
            _canonical_json(document) + b"\n",
            label="precommit data directory bindings",
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitWorkerError(
            "data directory bindings could not be persisted"
        ) from exc
    return document


def _load_directory_bindings(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    if not paths.directory_bindings.exists() and not paths.directory_bindings.is_symlink():
        return _new_directory_bindings(manifest, paths)
    payload = _read_root_file(
        paths.directory_bindings,
        label="precommit data directory bindings",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitWorkerError(
            "data directory bindings are invalid JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "operation_id", "role", "stores"}
        or document["schema"] != DIRECTORY_BINDINGS_SCHEMA
        or document["operation_id"] != manifest.operation_id
        or document["role"] != manifest.role
        or not isinstance(document["stores"], dict)
        or set(document["stores"]) != set(STORE_NAMES)
    ):
        raise PrecommitWorkerError("data directory bindings are invalid")
    expected_root = paths.data_root / manifest.role.replace("_", "-")
    for store in STORE_NAMES:
        row = document["stores"][store]
        if (
            not isinstance(row, dict)
            or set(row) != DIRECTORY_BINDING_FIELDS
            or row["path"] != str(expected_root / store)
            or isinstance(row["device"], bool)
            or not isinstance(row["device"], int)
            or isinstance(row["inode"], bool)
            or not isinstance(row["inode"], int)
            or row["initial_uid"] != 0
            or row["initial_gid"] != 0
            or row["mode"] != "0700"
        ):
            raise PrecommitWorkerError(
                f"{store} data directory binding is invalid"
            )
    return document


def _attest_data_directories(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    postgres_started: bool | None,
) -> Mapping[str, Any]:
    document = _load_directory_bindings(manifest, paths)
    evidence: dict[str, Any] = {}
    for store in STORE_NAMES:
        row = document["stores"][store]
        if store == "postgres" and postgres_started is True:
            allowed_owners = frozenset(
                {(POSTGRES_RUNTIME_UID, POSTGRES_RUNTIME_GID)}
            )
        elif store == "postgres" and postgres_started is None:
            allowed_owners = frozenset(
                {
                    (0, 0),
                    (POSTGRES_RUNTIME_UID, POSTGRES_RUNTIME_GID),
                }
            )
        else:
            allowed_owners = frozenset({(0, 0)})
        metadata, entries = _stable_directory_entries(
            Path(row["path"]),
            label=f"{store} data",
            allowed_owners=allowed_owners,
        )
        if (
            metadata.st_dev != row["device"]
            or metadata.st_ino != row["inode"]
        ):
            raise PrecommitWorkerError(
                f"{store} data directory identity changed"
            )
        if store == "redis" and entries:
            raise PrecommitWorkerError(
                "Redis target is not pristine-empty"
            )
        evidence[store] = {
            "path": row["path"],
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": "0700",
            "entry_count": len(entries),
        }
    return evidence


def _verify_compose(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> None:
    payload = _read_root_file(
        paths.compose,
        label="precommit role Compose",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise PrecommitWorkerError("precommit role Compose is invalid") from exc
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        raise PrecommitWorkerError("precommit role Compose has no services")
    expected_prefix = f"{manifest.role}_"
    if (
        not services
        or any(not str(name).startswith(expected_prefix) for name in services)
        or any(
            not isinstance(service, dict)
            or "build" in service
            or "container_name" in service
            or "ports" in service
            or service.get("network_mode") == "host"
            for service in services.values()
        )
    ):
        raise PrecommitWorkerError(
            "precommit role Compose escaped its role or network boundary"
        )
    required = ROLE_SERVICES[manifest.role]
    required_services = {
        str(value)
        for key, value in required.items()
        if key
        in {
            "database",
            "restore",
            "migration",
            "roles",
            "roles_post",
            "fencing",
            "observer",
        }
        and value is not None
    }
    if set(services) != required_services:
        raise PrecommitWorkerError(
            "precommit role Compose service closure is not exact"
        )
    if document.get("name") != (
        "${PRODUCTION_SHADOW_PROJECT:"
        "?operation-bound project is required}-"
        f"{manifest.role.replace('_', '-')}"
    ):
        raise PrecommitWorkerError(
            "precommit role Compose project expression is not canonical"
        )
    if document.get("volumes") is not None and document.get("volumes") != {}:
        raise PrecommitWorkerError(
            "precommit role Compose must not declare named volumes"
        )

    role_path = manifest.role.replace("_", "-")
    data_source = (
        "${PRODUCTION_SHADOW_DATA_ROOT:"
        "?operation-bound data root is required}"
    )
    secret_source = (
        "${PRODUCTION_SHADOW_SECRET_ROOT:"
        "?operation-bound secret root is required}"
    )
    ca_mount = (
        f"{secret_source}/tls/ca.crt",
        "/run/production-dr-ca/ca.crt",
        True,
    )
    expected_mounts: dict[str, set[tuple[str, str, bool]]] = {
        str(required["database"]): {
            (
                f"{data_source}/{role_path}/postgres",
                "/var/lib/postgresql/data",
                False,
            )
        },
        str(required["restore"]): {
            (
                f"{data_source}/restore-input/{role_path}",
                "/run/restore-input",
                True,
            ),
            (
                f"{data_source}/{role_path}/uploads",
                "/run/restore-target/uploads",
                False,
            ),
            (
                f"{data_source}/{role_path}/audit",
                "/run/restore-target/audit",
                False,
            ),
        },
        str(required["migration"]): {ca_mount},
        str(required["roles"]): {ca_mount},
        str(required["observer"]): (
            {
                ca_mount,
                (
                    f"{data_source}/{role_path}/uploads",
                    "/app/uploads",
                    True,
                ),
            }
            if manifest.role == "webapp_fi"
            else {ca_mount}
        ),
    }
    if required["roles_post"] is not None:
        expected_mounts[str(required["roles_post"])] = {ca_mount}
    if required["fencing"] is not None:
        expected_mounts[str(required["fencing"])] = {ca_mount}

    def parse_mount(value: Any) -> tuple[str, str, bool]:
        if not isinstance(value, str):
            raise PrecommitWorkerError(
                "precommit role Compose mount must use short bind syntax"
            )
        if value.startswith("${"):
            closing = value.find("}")
            separator = value.find(":", closing + 1)
        else:
            separator = value.find(":")
        if separator <= 0:
            raise PrecommitWorkerError(
                "precommit role Compose mount is invalid"
            )
        source = value[:separator]
        remainder = value[separator + 1 :]
        destination, marker, mode = remainder.partition(":")
        if not destination or (marker and mode != "ro"):
            raise PrecommitWorkerError(
                "precommit role Compose mount mode is invalid"
            )
        return source, destination, bool(marker)

    for service_name, expected in expected_mounts.items():
        volumes = services[service_name].get("volumes", [])
        if (
            not isinstance(volumes, list)
            or {parse_mount(value) for value in volumes} != expected
            or len(volumes) != len(expected)
        ):
            raise PrecommitWorkerError(
                f"precommit {service_name} mount closure differs"
            )

    networks = document.get("networks")
    role_network = manifest.role
    expected_network_label = {
        "trading-bot.production.operation-id": (
            "${PRODUCTION_SHADOW_OPERATION_ID:"
            "?operation UUID is required}"
        )
    }
    if (
        not isinstance(networks, dict)
        or role_network not in networks
        or not isinstance(networks[role_network], dict)
        or networks[role_network].get("internal") is not True
        or networks[role_network].get("labels") != expected_network_label
        or "name" in networks[role_network]
        or networks[role_network].get("external") not in {None, False}
    ):
        raise PrecommitWorkerError(
            "precommit internal role network is not operation-bound"
        )
    _run(
        [
            *_compose_base(manifest, paths),
            "--profile",
            f"{required['profile']}-observe",
            "config",
            "--quiet",
        ],
        timeout=60,
    )


def _oneoff_ids(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    cleanup_only: bool = False,
) -> list[str]:
    output = _run(
        [
            DOCKER,
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={paths.project_name}",
            "--filter",
            "label=com.docker.compose.oneoff=True",
            "--filter",
            (
                "label=trading-bot.production.operation-id="
                f"{manifest.operation_id}"
            ),
        ],
        timeout=30,
        purpose=(
            "cleanup-list-oneoffs"
            if cleanup_only
            else "inventory-oneoffs"
        ),
        cleanup_only=cleanup_only,
    )
    values = [value for value in output.splitlines() if value]
    if len(values) != len(set(values)) or any(
        not CONTAINER_ID_RE.fullmatch(value) for value in values
    ):
        raise PrecommitWorkerError("operation one-off inventory is invalid")
    return sorted(values)


def _validate_oneoff(
    identifier: str,
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    cleanup_only: bool = False,
) -> dict[str, Any]:
    payload = _load_json_output(
        _run(
            [DOCKER, "inspect", identifier],
            timeout=30,
            purpose=(
                "cleanup-inspect-oneoff"
                if cleanup_only
                else "inspect-oneoff"
            ),
            cleanup_only=cleanup_only,
        ),
        label="operation one-off inspection",
    )
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise PrecommitWorkerError("operation one-off inspection is invalid")
    row = payload[0]
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    service = (
        labels.get("com.docker.compose.service")
        if isinstance(labels, dict)
        else None
    )
    allowed = {
        value
        for key, value in ROLE_SERVICES[manifest.role].items()
        if key
        in {
            "restore",
            "migration",
            "roles",
            "roles_post",
            "fencing",
            "observer",
        }
        and isinstance(value, str)
    }
    expected_image = (
        manifest.runtime_image_ids["postgres"]
        if service == ROLE_SERVICES[manifest.role]["restore"]
        else manifest.runtime_image_ids["app"]
    )
    mounts = row.get("Mounts")
    host_config = row.get("HostConfig")
    networks = (
        row.get("NetworkSettings", {}).get("Networks")
        if isinstance(row.get("NetworkSettings"), dict)
        else None
    )
    expected_network = (
        f"{paths.project_name}_{ROLE_SERVICES[manifest.role]['network']}"
    )
    if (
        not isinstance(config, dict)
        or row.get("Image") != expected_image
        or config.get("Image") != expected_image
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != paths.project_name
        or labels.get("com.docker.compose.oneoff") != "True"
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or service not in allowed
        or not isinstance(mounts, list)
        or not isinstance(host_config, dict)
        or host_config.get("Privileged") is not False
        or (
            host_config.get("PortBindings") is not None
            and host_config.get("PortBindings") != {}
        )
        or host_config.get("NetworkMode") != expected_network
        or not isinstance(networks, dict)
        or set(networks) != {expected_network}
    ):
        raise PrecommitWorkerError(
            "refusing to clean a one-off outside the exact operation"
        )
    role_path = manifest.role.replace("_", "-")
    expected_binds: set[tuple[str, str, bool]] = {
        (
            str(paths.secret_root / "tls" / "ca.crt"),
            "/run/production-dr-ca/ca.crt",
            True,
        )
    }
    if service == ROLE_SERVICES[manifest.role]["restore"]:
        expected_binds = {
            (
                str(paths.data_root / "restore-input" / role_path),
                "/run/restore-input",
                True,
            ),
            (
                str(paths.data_root / role_path / "uploads"),
                "/run/restore-target/uploads",
                False,
            ),
            (
                str(paths.data_root / role_path / "audit"),
                "/run/restore-target/audit",
                False,
            ),
        }
    elif service == ROLE_SERVICES[manifest.role]["observer"]:
        if manifest.role == "webapp_fi":
            expected_binds.add(
                (
                    str(paths.data_root / role_path / "uploads"),
                    "/app/uploads",
                    True,
                )
            )
    observed_binds: set[tuple[str, str, bool]] = set()
    anonymous: list[str] = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise PrecommitWorkerError("operation one-off mount is invalid")
        if mount.get("Type") == "bind":
            source = mount.get("Source")
            destination = mount.get("Destination")
            if not isinstance(source, str) or not isinstance(destination, str):
                raise PrecommitWorkerError(
                    "operation one-off has an unsafe bind mount"
                )
            observed_binds.add(
                (source, destination, mount.get("RW") is False)
            )
        elif mount.get("Type") == "volume":
            name = mount.get("Name")
            source = mount.get("Source")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[0-9a-f]{64}", name)
                or source != f"/var/lib/docker/volumes/{name}/_data"
                or mount.get("Destination") != "/var/lib/postgresql/data"
                or mount.get("Driver") != "local"
                or mount.get("RW") is not True
                or service != ROLE_SERVICES[manifest.role]["restore"]
            ):
                raise PrecommitWorkerError(
                    "operation one-off inherited a foreign volume"
                )
            anonymous.append(name)
        else:
            raise PrecommitWorkerError(
                "operation one-off mount type is invalid"
            )
    declares_pgdata = (
        isinstance(config.get("Volumes"), dict)
        and set(config["Volumes"]) == {"/var/lib/postgresql/data"}
    )
    expected_anonymous_count = (
        1
        if service == ROLE_SERVICES[manifest.role]["restore"]
        and declares_pgdata
        else 0
    )
    if (
        observed_binds != expected_binds
        or len(anonymous) != expected_anonymous_count
    ):
        raise PrecommitWorkerError(
            "operation one-off mount closure differs"
        )
    return {
        "container_id": str(row.get("Id", "")),
        "service": service,
        "image_id": expected_image,
        "anonymous_volumes": sorted(anonymous),
    }


def _cleanup_oneoffs(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    removed_ids: set[str] = set()
    deadline = time.monotonic() + ONEOFF_CLEANUP_TIMEOUT_SECONDS
    stable_since: float | None = None
    while time.monotonic() < deadline:
        identifiers = _oneoff_ids(
            manifest,
            paths,
            cleanup_only=True,
        )
        if not identifiers:
            if stable_since is None:
                stable_since = time.monotonic()
            elif (
                time.monotonic() - stable_since
                >= ONEOFF_QUIESCENCE_SECONDS
            ):
                return removed
            time.sleep(0.05)
            continue
        stable_since = None
        for identifier in identifiers:
            evidence = _validate_oneoff(
                identifier,
                manifest,
                paths,
                cleanup_only=True,
            )
            _run(
                [DOCKER, "rm", "--force", "--volumes", identifier],
                timeout=60,
                purpose="cleanup-remove-oneoff",
                cleanup_only=True,
            )
            if identifier not in removed_ids:
                removed.append(evidence)
                removed_ids.add(identifier)
        time.sleep(0.05)
    raise PrecommitWorkerError(
        "operation one-off residue did not reach stable empty"
    )


def _compose_oneoff(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    profile: str,
    service: str,
    command: list[str] | None = None,
    timeout: int,
    stdin: BinaryIO | int | None = subprocess.DEVNULL,
) -> str:
    if _oneoff_ids(manifest, paths):
        raise PrecommitWorkerError(
            "operation has stale one-off residue before execution"
        )
    arguments = [
        *_compose_base(manifest, paths),
        "--profile",
        profile,
        "run",
        "--rm",
        "--no-deps",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "-T",
        service,
    ]
    if command:
        arguments.extend(command)
    try:
        return _run(arguments, timeout=timeout, stdin=stdin)
    finally:
        _cleanup_oneoffs(manifest, paths)


def _psql(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    sql: str,
    *,
    timeout: int = 300,
) -> str:
    services = ROLE_SERVICES[manifest.role]
    return _compose_oneoff(
        manifest,
        paths,
        profile=f"{services['profile']}-restore",
        service=str(services["restore"]),
        command=[
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "--no-psqlrc",
            "-Atqc",
            sql,
        ],
        timeout=timeout,
    )


def _stream_copy(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    sql: str,
) -> StreamDigest:
    services = ROLE_SERVICES[manifest.role]
    if _oneoff_ids(manifest, paths):
        raise PrecommitWorkerError(
            "operation has stale one-off residue before fingerprint"
        )
    arguments = [
        *_compose_base(manifest, paths),
        "--profile",
        f"{services['profile']}-restore",
        "run",
        "--rm",
        "--no-deps",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "-T",
        "--env",
        f"PGOPTIONS={DATABASE_FINGERPRINT_PGOPTIONS}",
        "--env",
        f"PGCLIENTENCODING={DATABASE_FINGERPRINT_CLIENT_ENCODING}",
        str(services["restore"]),
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--no-psqlrc",
        "--quiet",
        "--command",
        sql,
    ]
    try:
        return _run_streaming_sha256(arguments, timeout=1800, env=_SAFE_ENV)
    except ProductionOperationError as exc:
        raise PrecommitWorkerError(
            "database fingerprint stream failed closed"
        ) from exc
    finally:
        _cleanup_oneoffs(manifest, paths)


def _database_fingerprint(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> tuple[str, int, int]:
    tables = [
        value
        for value in _psql(
            manifest,
            paths,
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' ORDER BY tablename",
        ).splitlines()
        if value
    ]
    try:
        return _fingerprint_from_streams(
            tables,
            lambda sql: _stream_copy(manifest, paths, sql),
        )
    except ProductionOperationError as exc:
        raise PrecommitWorkerError(
            "database fingerprint contract failed closed"
        ) from exc


def _verify_source_database(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> tuple[str, int, int]:
    revision = _psql(
        manifest,
        paths,
        "SELECT version_num FROM alembic_version",
    )
    observed = _database_fingerprint(manifest, paths)
    expected = manifest.source_database
    if (
        revision != expected["alembic_revision"]
        or observed[0] != expected["database_fingerprint_sha256"]
        or observed[1] != expected["row_count"]
        or observed[2] != expected["table_count"]
    ):
        raise PrecommitWorkerError(
            "restored source database fingerprint differs"
        )
    return observed


def _database_container(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> str:
    service = str(ROLE_SERVICES[manifest.role]["database"])
    value = _run(
        [*_compose_base(manifest, paths), "ps", "--all", "--quiet", service],
        timeout=30,
    )
    if value and not CONTAINER_ID_RE.fullmatch(value):
        raise PrecommitWorkerError(
            "operation database container inventory is invalid"
        )
    return value


def _network_identifier(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> str:
    name = f"{paths.project_name}_{ROLE_SERVICES[manifest.role]['network']}"
    raw = _run(
        [
            DOCKER,
            "network",
            "ls",
            "--quiet",
            "--filter",
            f"name=^{name}$",
        ],
        timeout=30,
    )
    identifiers = [value for value in raw.splitlines() if value]
    if (
        len(identifiers) > 1
        or any(not CONTAINER_ID_RE.fullmatch(value) for value in identifiers)
    ):
        raise PrecommitWorkerError(
            "operation network inventory is ambiguous"
        )
    return identifiers[0] if identifiers else ""


def _validate_network(
    identifier: str,
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    allowed_container_ids: frozenset[str],
) -> Mapping[str, Any]:
    payload = _load_json_output(
        _run([DOCKER, "network", "inspect", identifier], timeout=30),
        label="operation network inspection",
    )
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise PrecommitWorkerError(
            "operation network inspection is invalid"
        )
    row = payload[0]
    role_network = str(ROLE_SERVICES[manifest.role]["network"])
    expected_name = f"{paths.project_name}_{role_network}"
    labels = row.get("Labels")
    allowed_label_names = {
        "com.docker.compose.network",
        "com.docker.compose.project",
        "com.docker.compose.version",
        "trading-bot.production.operation-id",
    }
    ipam = row.get("IPAM")
    ipam_config = ipam.get("Config") if isinstance(ipam, dict) else None
    if not isinstance(ipam_config, list) or len(ipam_config) != 1:
        raise PrecommitWorkerError(
            "operation network IPAM closure is invalid"
        )
    entry = ipam_config[0]
    try:
        subnet = ipaddress.ip_network(entry.get("Subnet"), strict=False)
        gateway = ipaddress.ip_address(entry.get("Gateway"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PrecommitWorkerError(
            "operation network subnet is invalid"
        ) from exc
    containers = row.get("Containers")
    if containers is None:
        containers = {}
    if (
        not isinstance(row.get("Id"), str)
        or not (
            row["Id"].startswith(identifier)
            or identifier.startswith(row["Id"])
        )
        or row.get("Name") != expected_name
        or row.get("Scope") != "local"
        or row.get("Driver") != "bridge"
        or row.get("Internal") is not True
        or row.get("Attachable") is not False
        or row.get("Ingress") is not False
        or row.get("ConfigOnly") not in {None, False}
        or (
            row.get("Options") is not None
            and row.get("Options") != {}
        )
        or not isinstance(labels, dict)
        or set(labels) - allowed_label_names
        or labels.get("com.docker.compose.network") != role_network
        or labels.get("com.docker.compose.project") != paths.project_name
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or not isinstance(ipam, dict)
        or ipam.get("Driver") != "default"
        or (
            ipam.get("Options") is not None
            and ipam.get("Options") != {}
        )
        or not isinstance(entry, dict)
        or set(entry) - {"Subnet", "Gateway", "IPRange", "AuxAddress"}
        or subnet.version != 4
        or not subnet.is_private
        or gateway not in subnet
        or not isinstance(containers, dict)
    ):
        raise PrecommitWorkerError(
            "operation network differs from the exact internal binding"
        )
    observed_ids = set(containers)
    matched: set[str] = set()
    for observed in observed_ids:
        matches = {
            allowed
            for allowed in allowed_container_ids
            if observed.startswith(allowed) or allowed.startswith(observed)
        }
        if len(matches) != 1:
            raise PrecommitWorkerError(
                "operation network has a foreign endpoint"
            )
        endpoint = containers[observed]
        if (
            not isinstance(endpoint, dict)
            or not isinstance(endpoint.get("EndpointID"), str)
            or not endpoint["EndpointID"]
        ):
            raise PrecommitWorkerError(
                "operation network endpoint is invalid"
            )
        matched.update(matches)
    if matched != set(allowed_container_ids):
        raise PrecommitWorkerError(
            "operation network endpoint closure differs"
        )
    return {
        "network_id": identifier,
        "name": expected_name,
        "subnet": str(subnet),
        "gateway": str(gateway),
        "endpoint_count": len(observed_ids),
    }


def _validate_database_container(
    identifier: str,
    manifest: PrecommitManifest,
    paths: OperationPaths,
    *,
    require_running: bool | None,
) -> dict[str, Any]:
    payload = _load_json_output(
        _run([DOCKER, "inspect", identifier], timeout=30),
        label="operation database inspection",
    )
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise PrecommitWorkerError(
            "operation database inspection is invalid"
        )
    row = payload[0]
    config = row.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    state = row.get("State")
    mounts = row.get("Mounts")
    network = f"{paths.project_name}_{ROLE_SERVICES[manifest.role]['network']}"
    networks = (
        row.get("NetworkSettings", {}).get("Networks")
        if isinstance(row.get("NetworkSettings"), dict)
        else None
    )
    host_config = row.get("HostConfig")
    role_path = manifest.role.replace("_", "-")
    postgres_source = str(paths.data_root / role_path / "postgres")
    if (
        not isinstance(row.get("Id"), str)
        or not (
            row["Id"].startswith(identifier)
            or identifier.startswith(row["Id"])
        )
        or row.get("Image") != manifest.runtime_image_ids["postgres"]
        or not isinstance(config, dict)
        or config.get("Image") != manifest.runtime_image_ids["postgres"]
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != paths.project_name
        or labels.get("com.docker.compose.service")
        != ROLE_SERVICES[manifest.role]["database"]
        or labels.get("trading-bot.production.operation-id")
        != manifest.operation_id
        or labels.get("com.docker.compose.oneoff") == "True"
        or not isinstance(host_config, dict)
        or host_config.get("Privileged") is not False
        or (
            host_config.get("PortBindings") is not None
            and host_config.get("PortBindings") != {}
        )
        or host_config.get("NetworkMode") != network
        or not isinstance(state, dict)
        or (
            require_running is not None
            and state.get("Running") is not require_running
        )
        or not isinstance(networks, dict)
        or set(networks) != {network}
        or not isinstance(mounts, list)
        or len(mounts) != 1
    ):
        raise PrecommitWorkerError(
            "operation database container differs from its exact binding"
        )
    mount = mounts[0]
    if (
        not isinstance(mount, dict)
        or mount.get("Type") != "bind"
        or mount.get("Source") != postgres_source
        or mount.get("Destination") != "/var/lib/postgresql/data"
        or mount.get("RW") is not True
    ):
        raise PrecommitWorkerError(
            "operation database bind mount differs from its exact path"
        )
    return {
        "container_id": str(row.get("Id", "")),
        "image_id": manifest.runtime_image_ids["postgres"],
        "project": paths.project_name,
        "service": ROLE_SERVICES[manifest.role]["database"],
        "network": network,
        "data_path": postgres_source,
        "running": state.get("Running") is True,
    }


def _operation_non_database_containers(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> list[str]:
    database = _database_container(manifest, paths)
    raw = _run(
        [
            DOCKER,
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={paths.project_name}",
        ],
        timeout=30,
    )
    identifiers = [item for item in raw.splitlines() if item]
    return sorted(item for item in identifiers if item != database)


def _verify_static_bindings(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    _verify_operation_directory_chains(paths)
    _verify_artifacts(manifest, paths)
    _verify_release(manifest, paths)
    _verify_image_archives(manifest, paths)
    _verify_images(manifest)
    _verify_role_material(manifest, paths)
    _verify_compose(manifest, paths)
    return {
        "exact_release_verified": True,
        "release_tree_sha": manifest.release_tree_sha,
        "canonical_compose_sha256": manifest.canonical_compose_sha256,
        "role_compose_sha256": manifest.role_compose_sha256,
        "environment_sha256": manifest.environment_sha256,
        "runtime_image_ids": dict(
            sorted(manifest.runtime_image_ids.items())
        ),
        "image_artifacts": {
            kind: {
                "archive_sha256": binding.archive_sha256,
                "archive_bytes": binding.archive_bytes,
                "config_digest": binding.config_digest,
                "content_descriptor": dict(binding.content_descriptor),
                "content_identity": binding.content_identity,
            }
            for kind, binding in sorted(manifest.image_artifacts.items())
        },
        "artifact_bindings_sha256": hashlib.sha256(
            _canonical_json(
                {
                    kind: {
                        "sha256": binding.sha256,
                        "bytes": binding.bytes,
                        "restored_tree_sha256": (
                            binding.restored_tree_sha256
                        ),
                    }
                    for kind, binding in sorted(manifest.artifacts.items())
                }
            )
        ).hexdigest(),
        "compose_config_verified": True,
        "redis_started": False,
    }


def _verify_installation(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    evidence = _verify_static_bindings(manifest, paths)
    if _oneoff_ids(manifest, paths):
        raise PrecommitWorkerError(
            "operation installation has stale or foreign container residue"
        )
    return {
        **evidence,
        "data_directories_created": False,
        "zero_oneoff_residue": True,
    }


def _bootstrap_database(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    static = _verify_static_bindings(manifest, paths)
    recovered = _cleanup_oneoffs(manifest, paths)
    if _oneoff_ids(manifest, paths):
        raise PrecommitWorkerError(
            "operation one-off cleanup did not reach zero residue"
        )
    identifier = _database_container(manifest, paths)
    adopted = bool(identifier)
    services = ROLE_SERVICES[manifest.role]
    network_identifier = _network_identifier(manifest, paths)
    directory_evidence: Mapping[str, Any]
    if not identifier:
        directory_evidence = _attest_data_directories(
            manifest,
            paths,
            postgres_started=False,
        )
        if network_identifier:
            _validate_network(
                network_identifier,
                manifest,
                paths,
                allowed_container_ids=frozenset(),
            )
        _run(
            [
                *_compose_base(manifest, paths),
                "--profile",
                f"{services['profile']}-data-ready",
                "create",
                "--no-build",
                "--no-deps",
                "--pull",
                "never",
                str(services["database"]),
            ],
            timeout=300,
        )
        identifier = _database_container(manifest, paths)
        if not identifier:
            raise PrecommitWorkerError(
                "operation database container was not created"
            )
        evidence = _validate_database_container(
            identifier,
            manifest,
            paths,
            require_running=False,
        )
        directory_evidence = _attest_data_directories(
            manifest,
            paths,
            postgres_started=False,
        )
        network_identifier = _network_identifier(manifest, paths)
        if not network_identifier:
            raise PrecommitWorkerError(
                "operation internal network was not created"
            )
        network_evidence = _validate_network(
            network_identifier,
            manifest,
            paths,
            allowed_container_ids=frozenset({identifier}),
        )
        _run(
            [
                *_compose_base(manifest, paths),
                "start",
                str(services["database"]),
            ],
            timeout=300,
        )
    else:
        evidence = _validate_database_container(
            identifier,
            manifest,
            paths,
            require_running=None,
        )
        directory_evidence = _attest_data_directories(
            manifest,
            paths,
            postgres_started=True if evidence["running"] else None,
        )
        if not network_identifier:
            raise PrecommitWorkerError(
                "adopted database lacks its exact internal network"
            )
        network_evidence = _validate_network(
            network_identifier,
            manifest,
            paths,
            allowed_container_ids=frozenset({identifier}),
        )
        if not evidence["running"]:
            _run(
                [
                    *_compose_base(manifest, paths),
                    "start",
                    str(services["database"]),
                ],
                timeout=300,
            )
    ready = False
    evidence = None
    for _attempt in range(60):
        try:
            evidence = _validate_database_container(
                identifier,
                manifest,
                paths,
                require_running=True,
            )
            directory_evidence = _attest_data_directories(
                manifest,
                paths,
                postgres_started=True,
            )
            network_evidence = _validate_network(
                network_identifier,
                manifest,
                paths,
                allowed_container_ids=frozenset({identifier}),
            )
            if _psql(manifest, paths, "SELECT 1", timeout=10) == "1":
                ready = True
                break
        except PrecommitWorkerError:
            time.sleep(1)
    if not ready or evidence is None:
        raise PrecommitWorkerError(
            "operation database did not become ready"
        )
    non_database = _operation_non_database_containers(manifest, paths)
    if non_database:
        raise PrecommitWorkerError(
            "bootstrap started an unapproved operation service"
        )
    return {
        **static,
        "database": evidence,
        "network": network_evidence,
        "data_directories": directory_evidence,
        "database_ready": True,
        "adopted_existing_database": adopted,
        "redis_started": False,
        "public_service_started": False,
        "private_worker_started": False,
        "recovered_oneoffs": recovered,
        "zero_oneoff_residue": not _oneoff_ids(manifest, paths),
    }


def _validate_archive(stream: BinaryIO) -> None:
    try:
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as archive:
            count = 0
            for member in archive:
                count += 1
                if count > MAX_TAR_MEMBERS:
                    raise PrecommitWorkerError(
                        "restore archive has too many members"
                    )
                candidate = PurePosixPath(member.name)
                if (
                    candidate.is_absolute()
                    or not candidate.parts
                    or ".." in candidate.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or not (member.isdir() or member.isfile())
                ):
                    raise PrecommitWorkerError(
                        "restore archive contains an unsafe member"
                    )
            if count == 0:
                raise PrecommitWorkerError("restore archive is empty")
    except (OSError, tarfile.TarError) as exc:
        raise PrecommitWorkerError("restore archive is invalid") from exc
    finally:
        stream.seek(0)


def _tree_digest(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    target: str,
) -> str:
    services = ROLE_SERVICES[manifest.role]
    if target not in {"uploads", "audit"}:
        raise PrecommitWorkerError("restore tree target is invalid")
    if _oneoff_ids(manifest, paths):
        raise PrecommitWorkerError(
            "operation has stale one-off residue before tree attestation"
        )
    arguments = [
        *_compose_base(manifest, paths),
        "--profile",
        f"{services['profile']}-restore",
        "run",
        "--rm",
        "--no-deps",
        "--label",
        f"trading-bot.production.operation-id={manifest.operation_id}",
        "-T",
        str(services["restore"]),
        "tar",
        "--sort=name",
        "--mtime=@0",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "-cf",
        "-",
        "-C",
        f"/run/restore-target/{target}",
        ".",
    ]
    try:
        result = _run_streaming_sha256(arguments, timeout=1800, env=_SAFE_ENV)
    except ProductionOperationError as exc:
        raise PrecommitWorkerError(
            "restored tree attestation failed"
        ) from exc
    finally:
        _cleanup_oneoffs(manifest, paths)
    return result.sha256


def _restore_files(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> Mapping[str, str]:
    services = ROLE_SERVICES[manifest.role]
    evidence: dict[str, str] = {}
    for target, kind in (
        ("uploads", "uploads-archive"),
        ("audit", "audit-archive"),
    ):
        archive = paths.artifacts[kind]
        binding = manifest.artifacts[kind]
        with _held_artifact(
            archive,
            binding,
            label=f"precommit {kind}",
        ) as stream:
            _validate_archive(stream)
            _attest_data_directories(
                manifest,
                paths,
                postgres_started=True,
            )
            _compose_oneoff(
                manifest,
                paths,
                profile=f"{services['profile']}-restore",
                service=str(services["restore"]),
                command=[
                    "find",
                    f"/run/restore-target/{target}",
                    "-mindepth",
                    "1",
                    "-delete",
                ],
                timeout=600,
            )
            _attest_data_directories(
                manifest,
                paths,
                postgres_started=True,
            )
            _compose_oneoff(
                manifest,
                paths,
                profile=f"{services['profile']}-restore",
                service=str(services["restore"]),
                command=[
                    "tar",
                    "-xzf",
                    "-",
                    "--no-same-owner",
                    "--no-same-permissions",
                    "-C",
                    f"/run/restore-target/{target}",
                ],
                timeout=1800,
                stdin=stream,
            )
        _attest_data_directories(
            manifest,
            paths,
            postgres_started=True,
        )
        digest = _tree_digest(manifest, paths, target)
        expected = manifest.artifacts[kind].restored_tree_sha256
        if digest != expected:
            raise PrecommitWorkerError(
                f"restored {target} tree digest differs"
            )
        evidence[target] = digest
    return evidence


def _restore_shadow(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    bootstrap = _bootstrap_database(manifest, paths)
    values = _verify_role_material(manifest, paths)
    database_name = values[str(ROLE_SERVICES[manifest.role]["database_env"])]
    table_count = _psql(
        manifest,
        paths,
        "SELECT count(*) FROM pg_class WHERE relkind='r' AND relnamespace="
        "(SELECT oid FROM pg_namespace WHERE nspname='public')",
    )
    if not table_count.isdigit():
        raise PrecommitWorkerError(
            "operation database table inventory is invalid"
        )
    adopted = table_count != "0"
    if not adopted:
        backup = paths.artifacts["database-backup"]
        with _held_artifact(
            backup,
            manifest.artifacts["database-backup"],
            label="precommit database backup",
        ) as stream:
            _compose_oneoff(
                manifest,
                paths,
                profile=f"{ROLE_SERVICES[manifest.role]['profile']}-restore",
                service=str(ROLE_SERVICES[manifest.role]["restore"]),
                command=[
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    "--dbname",
                    database_name,
                ],
                timeout=3600,
                stdin=stream,
            )
    fingerprint = _verify_source_database(manifest, paths)
    trees = _restore_files(manifest, paths)
    return {
        "database_fingerprint_sha256": fingerprint[0],
        "database_row_count": fingerprint[1],
        "database_table_count": fingerprint[2],
        "source_revision": manifest.source_database["alembic_revision"],
        "adopted_exact_database": adopted,
        "restored_tree_sha256": dict(sorted(trees.items())),
        "database": bootstrap["database"],
        "redis_restored": False,
        "legacy_source_mounted": False,
        "persistent_cleanup_performed": False,
        "zero_oneoff_residue": not _oneoff_ids(manifest, paths),
    }


def _parse_applied(raw: str, *, label: str) -> Mapping[str, Any]:
    document = _load_json_output(raw, label=label)
    if (
        not isinstance(document, dict)
        or document.get("status") != "applied"
    ):
        raise PrecommitWorkerError(f"{label} did not report applied")
    return document


def _concurrent_status(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    names: tuple[str, ...],
) -> Mapping[str, tuple[bool, bool]]:
    if not names:
        return {}
    if any(not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name) for name in names):
        raise PrecommitWorkerError(
            "migration concurrent-index inventory is invalid"
        )
    literals = ",".join(f"'{name}'" for name in names)
    rows = _psql(
        manifest,
        paths,
        "SELECT c.relname || '|' || i.indisvalid::text || '|' || "
        "i.indisready::text FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        f"WHERE n.nspname='public' AND c.relname IN ({literals}) "
        "ORDER BY c.relname",
    )
    result: dict[str, tuple[bool, bool]] = {}
    for row in rows.splitlines():
        parts = row.split("|")
        if (
            len(parts) != 3
            or parts[0] not in names
            or parts[0] in result
            or parts[1] not in {"t", "f", "true", "false"}
            or parts[2] not in {"t", "f", "true", "false"}
        ):
            raise PrecommitWorkerError(
                "migration concurrent-index status is invalid"
            )
        result[parts[0]] = (
            parts[1] in {"t", "true"},
            parts[2] in {"t", "true"},
        )
    return result


def _repair_concurrent_indexes(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    names: tuple[str, ...],
) -> list[str]:
    status = _concurrent_status(manifest, paths, names)
    invalid = sorted(
        name for name, value in status.items() if value != (True, True)
    )
    for name in invalid:
        _psql(
            manifest,
            paths,
            f'DROP INDEX CONCURRENTLY IF EXISTS public."{name}"',
            timeout=600,
        )
    after = _concurrent_status(manifest, paths, names)
    if any(after.get(name) != (True, True) for name in invalid if name in after):
        raise PrecommitWorkerError(
            "invalid concurrent-index residue could not be removed"
        )
    return invalid


def _prepare_shadow(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    _restore_shadow(manifest, paths)
    graph = _load_migration_graph(paths.release_root)
    try:
        corridor = _migration_corridor(
            graph,
            source_revision=str(
                manifest.source_database["alembic_revision"]
            ),
            target_revision=manifest.target_migration_revision,
        )
        concurrent = _concurrent_index_names(graph, corridor)
    except ProductionOperationError as exc:
        raise PrecommitWorkerError(
            "release migration corridor is invalid"
        ) from exc
    current = _psql(
        manifest,
        paths,
        "SELECT version_num FROM alembic_version",
    )
    if current not in corridor:
        raise PrecommitWorkerError(
            "database revision is outside the bound migration corridor"
        )
    services = ROLE_SERVICES[manifest.role]
    applied: list[str] = []
    repaired: list[str] = []
    if manifest.role == "webapp_fi":
        _parse_applied(
            _compose_oneoff(
                manifest,
                paths,
                profile="webapp-fi-prepare",
                service=str(services["roles"]),
                timeout=600,
            ),
            label="pre-migration roles",
        )
        applied.append(str(services["roles"]))
    if current != manifest.target_migration_revision:
        repaired = _repair_concurrent_indexes(
            manifest,
            paths,
            concurrent,
        )
        _compose_oneoff(
            manifest,
            paths,
            profile=f"{services['profile']}-prepare",
            service=str(services["migration"]),
            timeout=3600,
        )
        applied.append(str(services["migration"]))
    current = _psql(
        manifest,
        paths,
        "SELECT version_num FROM alembic_version",
    )
    if current != manifest.target_migration_revision:
        raise PrecommitWorkerError(
            "migration did not reach the exact target revision"
        )
    role_service = (
        services["roles_post"]
        if services["roles_post"] is not None
        else services["roles"]
    )
    _parse_applied(
        _compose_oneoff(
            manifest,
            paths,
            profile=f"{services['profile']}-prepare",
            service=str(role_service),
            timeout=600,
        ),
        label="post-migration roles",
    )
    applied.append(str(role_service))
    if services["fencing"] is not None:
        _parse_applied(
            _compose_oneoff(
                manifest,
                paths,
                profile=f"{services['profile']}-prepare",
                service=str(services["fencing"]),
                timeout=600,
            ),
            label="database fencing",
        )
        applied.append(str(services["fencing"]))
    if _operation_non_database_containers(manifest, paths):
        raise PrecommitWorkerError(
            "prepare left an unapproved operation service"
        )
    return {
        "source_revision": manifest.source_database["alembic_revision"],
        "target_revision": manifest.target_migration_revision,
        "migration_corridor": list(corridor),
        "repaired_concurrent_indexes": repaired,
        "applied_services": applied,
        "background_jobs_enabled": False,
        "business_write_enabled": False,
        "public_service_started": False,
        "zero_oneoff_residue": not _oneoff_ids(manifest, paths),
    }


def _readonly_acceptance(
    manifest: PrecommitManifest,
    paths: OperationPaths,
) -> dict[str, Any]:
    _prepare_shadow(manifest, paths)
    services = ROLE_SERVICES[manifest.role]
    output = _compose_oneoff(
        manifest,
        paths,
        profile=f"{services['profile']}-observe",
        service=str(services["observer"]),
        command=[
            "python",
            "scripts/produce_production_shadow_readonly_acceptance.py",
            "--operation-id",
            manifest.operation_id,
            "--role",
            manifest.role,
            "--release-sha",
            manifest.release_sha,
            "--expected-revision",
            manifest.target_migration_revision,
        ],
        timeout=3600,
    )
    document = _load_json_output(output, label="read-only acceptance")
    expected = {
        "schema": READONLY_ACCEPTANCE_SCHEMA,
        "status": "read-only-accepted",
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "migration_revision": manifest.target_migration_revision,
        "database_role": f"{manifest.role}_observer",
        "transaction_read_only": True,
        "default_transaction_read_only": True,
        "background_jobs_enabled": False,
        "provider_credentials_present": False,
        "business_write_attempted": False,
    }
    if (
        not isinstance(document, dict)
        or any(document.get(key) != value for key, value in expected.items())
        or not SHA256_RE.fullmatch(
            str(document.get("database_fingerprint_sha256", ""))
        )
        or not isinstance(document.get("database_row_count"), int)
        or not isinstance(document.get("database_table_count"), int)
    ):
        raise PrecommitWorkerError(
            "read-only acceptance evidence is invalid"
        )
    if _operation_non_database_containers(manifest, paths):
        raise PrecommitWorkerError(
            "acceptance left an unapproved operation service"
        )
    return {
        "acceptance": document,
        "public_service_started": False,
        "provider_network_used": False,
        "business_write_allowed": False,
        "zero_oneoff_residue": not _oneoff_ids(manifest, paths),
    }


ACTION_IMPLEMENTATIONS: Mapping[
    str,
    Callable[[PrecommitManifest, OperationPaths], dict[str, Any]],
] = {
    "verify-installation": _verify_installation,
    "bootstrap-database": _bootstrap_database,
    "restore-shadow": _restore_shadow,
    "prepare-shadow": _prepare_shadow,
    "readonly-acceptance": _readonly_acceptance,
}


def _state_hash(state: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in state.items() if key != "state_sha256"}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _event_hash(event: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _validate_journal(
    document: Any,
    *,
    manifest: PrecommitManifest,
) -> dict[str, Any]:
    if (
        not isinstance(document, dict)
        or set(document) != JOURNAL_FIELDS
        or document.get("schema") != JOURNAL_SCHEMA
        or document.get("operation_id") != manifest.operation_id
        or document.get("role") != manifest.role
        or document.get("release_sha") != manifest.release_sha
        or document.get("manifest_sha256") != manifest.canonical_sha256
        or document.get("state_sha256") != _state_hash(document)
    ):
        raise PrecommitWorkerError("precommit journal binding is invalid")
    completed = document.get("completed_actions")
    current = document.get("current_action")
    attempts = document.get("attempts")
    evidence = document.get("evidence")
    events = document.get("events")
    if (
        not isinstance(completed, list)
        or completed != list(ACTIONS[: len(completed)])
        or current
        not in {
            None,
            ACTIONS[len(completed)] if len(completed) < len(ACTIONS) else None,
        }
        or not isinstance(attempts, dict)
        or set(attempts) - set(ACTIONS)
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 100
            for value in attempts.values()
        )
        or not isinstance(evidence, dict)
        or set(evidence) != set(completed)
        or any(
            not isinstance(value, str) or not SHA256_RE.fullmatch(value)
            for value in evidence.values()
        )
        or not isinstance(events, list)
        or len(events) > 1000
    ):
        raise PrecommitWorkerError("precommit journal state is invalid")
    previous = "0" * 64
    replay: list[str] = []
    active: str | None = None
    for index, event in enumerate(events, 1):
        if (
            not isinstance(event, dict)
            or set(event)
            != {
                "index",
                "kind",
                "action",
                "attempt",
                "evidence_sha256",
                "previous_event_sha256",
                "event_sha256",
            }
            or event["index"] != index
            or event["previous_event_sha256"] != previous
            or event["event_sha256"] != _event_hash(event)
            or event["kind"] not in {"started", "completed"}
            or event["action"] not in ACTIONS
        ):
            raise PrecommitWorkerError(
                "precommit journal event chain is invalid"
            )
        if event["kind"] == "started":
            if active is not None or event["action"] != ACTIONS[len(replay)]:
                raise PrecommitWorkerError(
                    "precommit journal start ordering is invalid"
                )
            active = event["action"]
            if event["evidence_sha256"] is not None:
                raise PrecommitWorkerError(
                    "precommit start event has unexpected evidence"
                )
        else:
            if active != event["action"]:
                raise PrecommitWorkerError(
                    "precommit journal completion ordering is invalid"
                )
            replay.append(active)
            active = None
            if event["evidence_sha256"] != evidence.get(event["action"]):
                raise PrecommitWorkerError(
                    "precommit completion evidence differs"
                )
        previous = event["event_sha256"]
    if replay != completed or active != current:
        raise PrecommitWorkerError(
            "precommit journal events do not replay to current state"
        )
    return dict(document)


def _ensure_root_directory(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
        except OSError as exc:
            raise PrecommitWorkerError(
                "precommit directory could not be created"
            ) from exc
        metadata = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise PrecommitWorkerError(
            "precommit directory must be root-owned mode 0700"
        )


def _prepare_journal_directories(paths: OperationPaths) -> None:
    role_root = paths.secret_root / paths.manifest.parent.name
    metadata = role_root.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PrecommitWorkerError(
            "role secret directory is unavailable or unsafe"
        )
    _ensure_root_directory(paths.journal_directory)
    _ensure_root_directory(paths.evidence_directory)


@contextmanager
def _journal_lock(paths: OperationPaths):  # noqa: ANN202
    lock = paths.journal_directory / "journal.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(lock, flags, 0o600)
    except OSError as exc:
        raise PrecommitWorkerError(
            "precommit journal lock is unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PrecommitWorkerError(
                "precommit journal lock is unsafe"
            )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_state(path: Path, state: dict[str, Any], *, create: bool) -> None:
    state["state_sha256"] = _state_hash(state)
    payload = _canonical_json(state) + b"\n"
    try:
        if create:
            write_secure_new_bytes(
                path,
                payload,
                label="precommit journal",
                max_size=MAX_JSON_BYTES,
            )
        else:
            write_secure_atomic_bytes(
                path,
                payload,
                label="precommit journal",
                max_size=MAX_JSON_BYTES,
            )
    except SecureFileError as exc:
        raise PrecommitWorkerError(
            "precommit journal could not be persisted"
        ) from exc


def _new_state(manifest: PrecommitManifest) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "manifest_sha256": manifest.canonical_sha256,
        "completed_actions": [],
        "current_action": None,
        "attempts": {},
        "evidence": {},
        "events": [],
        "state_sha256": "",
    }
    state["state_sha256"] = _state_hash(state)
    return state


def _load_state(
    paths: OperationPaths,
    manifest: PrecommitManifest,
) -> dict[str, Any]:
    if not paths.journal.exists() and not paths.journal.is_symlink():
        state = _new_state(manifest)
        _write_state(paths.journal, state, create=True)
        return state
    payload = _read_root_file(
        paths.journal,
        label="precommit journal",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitWorkerError(
            "precommit journal is invalid JSON"
        ) from exc
    return _validate_journal(document, manifest=manifest)


def _append_event(
    state: dict[str, Any],
    *,
    kind: str,
    action: str,
    attempt: int,
    evidence_sha256: str | None,
) -> None:
    previous = (
        state["events"][-1]["event_sha256"]
        if state["events"]
        else "0" * 64
    )
    event: dict[str, Any] = {
        "index": len(state["events"]) + 1,
        "kind": kind,
        "action": action,
        "attempt": attempt,
        "evidence_sha256": evidence_sha256,
        "previous_event_sha256": previous,
        "event_sha256": "",
    }
    event["event_sha256"] = _event_hash(event)
    state["events"].append(event)


def _evidence_document(
    manifest: PrecommitManifest,
    action: str,
    semantic: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "action": action,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "release_tree_sha": manifest.release_tree_sha,
        "manifest_sha256": manifest.canonical_sha256,
        "controller_manifest_sha256": manifest.controller_manifest_sha256,
        "approval_sha256": manifest.approval_sha256,
        "role_material_sha256": manifest.role_material_sha256,
        "business_write_allowed": False,
        "freeze_performed": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "destructive_cleanup_performed": False,
        "semantic": dict(semantic),
    }


def _write_or_verify_evidence(
    path: Path,
    document: Mapping[str, Any],
) -> str:
    payload = _canonical_json(document) + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists() or path.is_symlink():
        observed = _read_root_file(
            path,
            label="precommit evidence",
            maximum=MAX_JSON_BYTES,
        )
        if observed != payload:
            raise PrecommitWorkerError(
                "recomputed precommit evidence differs after retry"
            )
        return digest
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="precommit evidence",
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise PrecommitWorkerError(
            "precommit evidence could not be persisted"
        ) from exc
    return digest


def _load_completed_evidence(
    path: Path,
    *,
    manifest: PrecommitManifest,
    action: str,
) -> tuple[dict[str, Any], str]:
    payload = _read_root_file(
        path,
        label="completed precommit evidence",
        maximum=MAX_JSON_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PrecommitWorkerError(
            "completed precommit evidence is invalid JSON"
        ) from exc
    expected = {
        "schema": EVIDENCE_SCHEMA,
        "status": "completed",
        "action": action,
        "operation_id": manifest.operation_id,
        "role": manifest.role,
        "release_sha": manifest.release_sha,
        "release_tree_sha": manifest.release_tree_sha,
        "manifest_sha256": manifest.canonical_sha256,
        "controller_manifest_sha256": manifest.controller_manifest_sha256,
        "approval_sha256": manifest.approval_sha256,
        "role_material_sha256": manifest.role_material_sha256,
        "business_write_allowed": False,
        "freeze_performed": False,
        "current_mutated": False,
        "legacy_mutated": False,
        "object_storage_mutated": False,
        "destructive_cleanup_performed": False,
    }
    semantic = document.get("semantic") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != EVIDENCE_FIELDS
        or any(document.get(key) != value for key, value in expected.items())
        or not isinstance(semantic, dict)
        or semantic.get("zero_oneoff_residue") is not True
        or payload != _canonical_json(document) + b"\n"
    ):
        raise PrecommitWorkerError(
            "completed precommit evidence differs from the operation"
        )
    return document, hashlib.sha256(payload).hexdigest()


def execute_action(
    manifest_path: Path,
    *,
    action: str,
    apply: bool,
    confirm: str | None,
    control_fd: int | None = None,
    process_group_report_fd: int | None = None,
    process_group_ack_fd: int | None = None,
    cleanup_process_report_fd: int | None = None,
    cleanup_process_ack_fd: int | None = None,
) -> Mapping[str, Any]:
    manifest = load_manifest(manifest_path)
    paths = operation_paths(
        manifest.operation_id,
        manifest.release_sha,
        manifest.role,
    )
    required = confirmation_phrase(manifest, action)
    if not apply:
        if confirm is not None:
            raise PrecommitWorkerError(
                "--confirm is valid only with --apply"
            )
        return {
            "schema": EVIDENCE_SCHEMA,
            "status": "planned",
            "action": action,
            "operation_id": manifest.operation_id,
            "role": manifest.role,
            "release_sha": manifest.release_sha,
            "required_confirmation": required,
            "business_write_allowed": False,
            "freeze_allowed": False,
            "current_mutation_allowed": False,
            "legacy_mutation_allowed": False,
            "object_storage_mutation_allowed": False,
        }
    if os.geteuid() != 0:
        raise PrecommitWorkerError("precommit worker must run as root")
    if confirm != required:
        raise PrecommitWorkerError(
            f"precommit execution requires --confirm {required}"
        )
    if action in MUTATING_ACTIONS:
        if (
            control_fd is None
            or process_group_report_fd is None
            or process_group_ack_fd is None
            or cleanup_process_report_fd is None
            or cleanup_process_ack_fd is None
        ):
            raise PrecommitWorkerError(
                "mutating precommit action requires controller liveness "
                "and separate normal/cleanup process authorization"
            )
        with _execution_authority(
            control_fd=control_fd,
            normal_report_fd=process_group_report_fd,
            normal_ack_fd=process_group_ack_fd,
            cleanup_report_fd=cleanup_process_report_fd,
            cleanup_ack_fd=cleanup_process_ack_fd,
        ):
            return _execute_action_apply(manifest, paths, action)
    return _execute_action_apply(manifest, paths, action)


def _execute_action_apply(
    manifest: PrecommitManifest,
    paths: OperationPaths,
    action: str,
) -> Mapping[str, Any]:
    _prepare_journal_directories(paths)
    with _journal_lock(paths):
        state = _load_state(paths, manifest)
        target_index = ACTIONS.index(action)
        completed = list(state["completed_actions"])
        if len(completed) > target_index:
            evidence_path = paths.evidence_directory / f"{action}.json"
            digest, _size = _hash_root_file(
                evidence_path,
                label="completed precommit evidence",
                maximum=MAX_JSON_BYTES,
            )
            if state["evidence"].get(action) != digest:
                raise PrecommitWorkerError(
                    "completed precommit evidence identity differs"
                )
            return {
                "schema": EVIDENCE_SCHEMA,
                "status": "already-completed",
                "action": action,
                "operation_id": manifest.operation_id,
                "role": manifest.role,
                "evidence_sha256": digest,
            }
        if len(completed) < target_index:
            raise PrecommitWorkerError(
                f"precommit action requires {ACTIONS[len(completed)]} first"
            )
        if state["current_action"] not in {None, action}:
            raise PrecommitWorkerError(
                "precommit journal contains another in-progress action"
            )
        evidence_path = paths.evidence_directory / f"{action}.json"
        if (
            state["current_action"] == action
            and (evidence_path.exists() or evidence_path.is_symlink())
        ):
            document, evidence_sha256 = _load_completed_evidence(
                evidence_path,
                manifest=manifest,
                action=action,
            )
            attempt = state["attempts"].get(action)
            if (
                isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or attempt < 1
            ):
                raise PrecommitWorkerError(
                    "precommit journal recovery attempt is invalid"
                )
            state["current_action"] = None
            state["completed_actions"].append(action)
            state["evidence"][action] = evidence_sha256
            _append_event(
                state,
                kind="completed",
                action=action,
                attempt=attempt,
                evidence_sha256=evidence_sha256,
            )
            _write_state(paths.journal, state, create=False)
            _validate_journal(state, manifest=manifest)
            return {
                "schema": EVIDENCE_SCHEMA,
                "status": "recovered-completed",
                "action": action,
                "operation_id": manifest.operation_id,
                "role": manifest.role,
                "evidence_sha256": evidence_sha256,
                "journal_sha256": state["state_sha256"],
                "zero_oneoff_residue": True,
                "business_write_allowed": False,
                "implementation_replayed": False,
                "semantic": document["semantic"],
            }
        attempt = int(state["attempts"].get(action, 0)) + 1
        if attempt > 100:
            raise PrecommitWorkerError(
                "precommit action retry bound was exceeded"
            )
        state["attempts"][action] = attempt
        if state["current_action"] is None:
            state["current_action"] = action
            _append_event(
                state,
                kind="started",
                action=action,
                attempt=attempt,
                evidence_sha256=None,
            )
        _write_state(paths.journal, state, create=False)

        semantic = ACTION_IMPLEMENTATIONS[action](manifest, paths)
        document = _evidence_document(manifest, action, semantic)
        evidence_sha256 = _write_or_verify_evidence(
            evidence_path,
            document,
        )
        state["current_action"] = None
        state["completed_actions"].append(action)
        state["evidence"][action] = evidence_sha256
        _append_event(
            state,
            kind="completed",
            action=action,
            attempt=attempt,
            evidence_sha256=evidence_sha256,
        )
        _write_state(paths.journal, state, create=False)
        _validate_journal(state, manifest=manifest)
        return {
            "schema": EVIDENCE_SCHEMA,
            "status": "completed",
            "action": action,
            "operation_id": manifest.operation_id,
            "role": manifest.role,
            "evidence_sha256": evidence_sha256,
            "journal_sha256": state["state_sha256"],
            "zero_oneoff_residue": bool(
                semantic.get("zero_oneoff_residue")
            ),
            "business_write_allowed": False,
        }


def main(argv: list[str] | None = None) -> int:
    incoming = sys.argv[1:] if argv is None else argv
    if incoming and incoming[0] == BOUNDED_EXEC_WRAPPER:
        return _bounded_exec_wrapper(incoming)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--action", choices=ACTIONS, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--control-fd", type=int)
    parser.add_argument("--process-group-report-fd", type=int)
    parser.add_argument("--process-group-ack-fd", type=int)
    parser.add_argument("--cleanup-process-report-fd", type=int)
    parser.add_argument("--cleanup-process-ack-fd", type=int)
    args = parser.parse_args(incoming)
    try:
        result = execute_action(
            args.manifest,
            action=args.action,
            apply=args.apply,
            confirm=args.confirm,
            control_fd=args.control_fd,
            process_group_report_fd=args.process_group_report_fd,
            process_group_ack_fd=args.process_group_ack_fd,
            cleanup_process_report_fd=args.cleanup_process_report_fd,
            cleanup_process_ack_fd=args.cleanup_process_ack_fd,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, PrecommitWorkerError)
            else "production-shadow precommit worker failed closed"
        )
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": message,
                    "error_class": "PrecommitWorkerError",
                    "business_write_allowed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
