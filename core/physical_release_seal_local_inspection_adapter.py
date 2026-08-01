"""Concrete, fail-closed local-only adapters for release-seal inspection.

The admission layer deliberately accepts injected filesystem and Git readers so
it can stay pure with respect to operating-system effects.  This module is the
only local implementation for that boundary.  It opens a pre-pinned, classic
Git worktree through no-follow descriptors and permits exactly the four
read-only Git query shapes that admission emits.  It never selects a release,
builds or loads an image, accesses a remote, publishes, deploys, or authorizes
a campaign.

The adapter is separately default-off.  A caller must opt into both this
adapter and ``PhysicalReleaseSealAdmissionConfig``; even then the result is
only the existing in-memory, non-authorizing descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import time

from core.append_only_sync_delta_batch import RELEASE_SHA_RE
import core.physical_release_seal_admission as _admission


__all__ = (
    "DEFAULT_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS",
    "MAX_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS",
    "PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_ADAPTER_DEFAULT_ENABLED",
    "PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_ADAPTER_SCHEMA",
    "PhysicalReleaseSealLocalInspectionAdapter",
    "PhysicalReleaseSealLocalInspectionAdapterConfig",
    "PhysicalReleaseSealLocalInspectionAdapterError",
)


PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_ADAPTER_SCHEMA = (
    "gold-trade-physical-release-seal-local-inspection-adapter-v1"
)
PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_ADAPTER_DEFAULT_ENABLED = False
DEFAULT_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS = 15
MAX_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS = 30

_MAX_GIT_OUTPUT_BYTES = _admission.MAX_PHYSICAL_RELEASE_SEAL_GIT_OUTPUT_BYTES
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | _CLOEXEC
_REGULAR_OPEN_FLAGS = os.O_RDONLY | _CLOEXEC
_PROC_SELF_FD = Path("/proc/self/fd")


class PhysicalReleaseSealLocalInspectionAdapterError(ValueError):
    """A fail-closed refusal by the local read-only inspection adapter."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalReleaseSealLocalInspectionAdapterConfig:
    """Explicit local-only binding for one prospective source identity."""

    worktree: Path | None = None
    expected_release_sha: str = ""
    enabled: bool = PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_ADAPTER_DEFAULT_ENABLED
    command_timeout_seconds: int = DEFAULT_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class _Facts:
    worktree: Path
    expected_release_sha: str
    command_timeout_seconds: int


def _fail(code: str) -> None:
    raise PhysicalReleaseSealLocalInspectionAdapterError(code)


def _safe_absolute_path(value: object, *, code: str) -> Path:
    if (
        not isinstance(value, Path)
        or not value.is_absolute()
        or value == Path("/")
        or any(part in {"", ".", ".."} for part in value.parts[1:])
    ):
        _fail(code)
    return value


def _normalise_config(value: object) -> _Facts:
    if type(value) is not PhysicalReleaseSealLocalInspectionAdapterConfig:
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_DISABLED")
    worktree = _safe_absolute_path(
        value.worktree,
        code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_INVALID",
    )
    if (
        type(value.expected_release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(value.expected_release_sha) is None
    ):
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_RELEASE_INVALID")
    if (
        type(value.command_timeout_seconds) is not int
        or not 1
        <= value.command_timeout_seconds
        <= MAX_PHYSICAL_RELEASE_SEAL_LOCAL_GIT_TIMEOUT_SECONDS
    ):
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_TIMEOUT_INVALID")
    return _Facts(
        worktree=worktree,
        expected_release_sha=value.expected_release_sha,
        command_timeout_seconds=value.command_timeout_seconds,
    )


def _require_no_follow_platform() -> None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PLATFORM_UNSAFE")


def _directory_is_safe(info: os.stat_result, *, permit_sticky_root_parent: bool) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or mode & 0o500 != 0o500
    ):
        return False
    if not mode & 0o022:
        return True
    return (
        permit_sticky_root_parent
        and bool(info.st_mode & stat.S_ISVTX)
        and info.st_uid == 0
    )


def _require_safe_directory(
    descriptor: int,
    *,
    permit_sticky_root_parent: bool,
    code: str,
) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail(code)
    if not _directory_is_safe(
        info, permit_sticky_root_parent=permit_sticky_root_parent
    ):
        _fail(code)
    return info


def _open_root_controlled_directory(path: Path, *, code: str) -> int:
    """Open ``path`` component-by-component without following a symlink.

    A root-owned sticky ancestor (normally ``/tmp``) is safe for an existing
    root-owned child: non-root users cannot replace that child.  Any other
    writable ancestor is refused.  The final directory itself is always
    required to be non-writable by group/other.
    """

    _require_no_follow_platform()
    _safe_absolute_path(path, code=code)
    descriptor = -1
    try:
        descriptor = os.open("/", _DIRECTORY_OPEN_FLAGS | os.O_NOFOLLOW)
        _require_safe_directory(
            descriptor,
            permit_sticky_root_parent=True,
            code=code,
        )
        components = path.parts[1:]
        for index, component in enumerate(components):
            if component in {"", ".", ".."}:  # defensive; path was normalized above
                _fail(code)
            next_descriptor = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
            _require_safe_directory(
                descriptor,
                permit_sticky_root_parent=index < len(components) - 1,
                code=code,
            )
        return descriptor
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _open_child_directory(
    parent_descriptor: int,
    *,
    name: str,
    code: str,
) -> tuple[int, os.stat_result]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            _DIRECTORY_OPEN_FLAGS | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        info = _require_safe_directory(
            descriptor,
            permit_sticky_root_parent=False,
            code=code,
        )
        return descriptor, info
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _require_safe_regular_executable(
    descriptor: int,
    *,
    code: str,
) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail(code)
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or mode & 0o022
        or not info.st_mode & stat.S_IXUSR
    ):
        _fail(code)
    return info


def _require_safe_regular_file(
    descriptor: int,
    *,
    code: str,
) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail(code)
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or mode & 0o022
    ):
        _fail(code)
    return info


def _open_child_regular_file(
    parent_descriptor: int,
    *,
    name: str,
    code: str,
) -> tuple[int, os.stat_result]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            _REGULAR_OPEN_FLAGS | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        info = _require_safe_regular_file(descriptor, code=code)
        return descriptor, info
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)


def _path_absent(parent_descriptor: int, *, relative_path: str, code: str) -> None:
    """Require that a potentially cross-root Git control path does not exist."""

    try:
        os.stat(relative_path, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        _fail(code)
    _fail(code)


def _require_classic_local_git_metadata(
    worktree_descriptor: int,
    *,
    code: str,
) -> tuple[int, os.stat_result]:
    """Pin a classic ``.git`` directory and reject external object metadata."""

    git_descriptor, git_info = _open_child_directory(
        worktree_descriptor,
        name=".git",
        code=code,
    )
    objects_descriptor = -1
    config_descriptor = -1
    head_descriptor = -1
    try:
        # A linked worktree/common-dir setup or object alternate can move the
        # observed source outside the pinned worktree.  Release sealing is
        # intentionally stricter: it admits only a classic, self-contained
        # local repository.
        _path_absent(git_descriptor, relative_path="commondir", code=code)
        _path_absent(git_descriptor, relative_path="gitdir", code=code)
        _path_absent(git_descriptor, relative_path="config.worktree", code=code)
        config_descriptor, _ = _open_child_regular_file(
            git_descriptor,
            name="config",
            code=code,
        )
        os.close(config_descriptor)
        config_descriptor = -1
        head_descriptor, _ = _open_child_regular_file(
            git_descriptor,
            name="HEAD",
            code=code,
        )
        os.close(head_descriptor)
        head_descriptor = -1
        objects_descriptor, _ = _open_child_directory(
            git_descriptor,
            name="objects",
            code=code,
        )
        info_descriptor, _ = _open_child_directory(
            objects_descriptor,
            name="info",
            code=code,
        )
        try:
            _path_absent(info_descriptor, relative_path="alternates", code=code)
            _path_absent(info_descriptor, relative_path="http-alternates", code=code)
        finally:
            os.close(info_descriptor)
        os.close(objects_descriptor)
        objects_descriptor = -1
        return git_descriptor, git_info
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if config_descriptor >= 0:
            os.close(config_descriptor)
        if head_descriptor >= 0:
            os.close(head_descriptor)
        if objects_descriptor >= 0:
            os.close(objects_descriptor)
        os.close(git_descriptor)
        raise
    except OSError:
        if config_descriptor >= 0:
            os.close(config_descriptor)
        if head_descriptor >= 0:
            os.close(head_descriptor)
        if objects_descriptor >= 0:
            os.close(objects_descriptor)
        os.close(git_descriptor)
        _fail(code)


def _filesystem_object(
    *,
    path: Path,
    info: os.stat_result,
    regular_file: bool,
    directory: bool,
    executable: bool,
) -> _admission.PhysicalReleaseSealFilesystemObject:
    return _admission.PhysicalReleaseSealFilesystemObject(
        path=path,
        owner_uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
        regular_file=regular_file,
        directory=directory,
        symlink=False,
        executable=executable,
        ancestors_root_controlled=True,
        device=info.st_dev,
        inode=info.st_ino,
        ctime_ns=info.st_ctime_ns,
        mtime_ns=info.st_mtime_ns,
    )


def _open_fixed_git_binary(*, code: str) -> tuple[int, os.stat_result]:
    binary = _admission.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY
    parent_descriptor = _open_root_controlled_directory(binary.parent, code=code)
    descriptor = -1
    try:
        descriptor = os.open(
            binary.name,
            _REGULAR_OPEN_FLAGS | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        info = _require_safe_regular_executable(descriptor, code=code)
        return descriptor, info
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail(code)
    finally:
        os.close(parent_descriptor)


def _open_pinned_worktree(
    worktree: Path,
    *,
    code: str,
) -> tuple[int, int, os.stat_result, os.stat_result]:
    worktree_descriptor = _open_root_controlled_directory(worktree, code=code)
    git_descriptor = -1
    try:
        worktree_info = _require_safe_directory(
            worktree_descriptor,
            permit_sticky_root_parent=False,
            code=code,
        )
        git_descriptor, git_info = _require_classic_local_git_metadata(
            worktree_descriptor,
            code=code,
        )
        return worktree_descriptor, git_descriptor, worktree_info, git_info
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if git_descriptor >= 0:
            os.close(git_descriptor)
        os.close(worktree_descriptor)
        raise
    except OSError:
        if git_descriptor >= 0:
            os.close(git_descriptor)
        os.close(worktree_descriptor)
        _fail(code)


def _require_proc_fd_path(descriptor: int, *, code: str) -> str:
    try:
        if not _PROC_SELF_FD.is_dir():
            _fail(code)
        path = _PROC_SELF_FD / str(descriptor)
        # The descriptor must be observable through procfs before it can be
        # handed to the child.  This is a descriptor pin, never a user path.
        if not path.exists():
            _fail(code)
    except OSError:
        _fail(code)
    return str(path)


def _allowed_command_suffixes(expected_release_sha: str) -> frozenset[tuple[str, ...]]:
    return frozenset(
        {
            ("rev-parse", "--verify", "HEAD^{commit}"),
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
            ),
            ("rev-parse", "--verify", expected_release_sha + "^{tree}"),
            ("ls-tree", "-r", "-z", "--full-tree", expected_release_sha),
        }
    )


def _validate_invocation(
    invocation: object,
    *,
    facts: _Facts,
) -> tuple[str, ...]:
    if type(invocation) is not _admission.PhysicalReleaseSealGitInvocation:
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_INVOCATION_INVALID")
    if (
        not isinstance(invocation.executable, Path)
        or invocation.executable != _admission.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY
        or not isinstance(invocation.worktree, Path)
        or invocation.worktree != facts.worktree
        or invocation.environment != _admission._GIT_ENVIRONMENT
        or type(invocation.arguments) is not tuple
        or any(type(argument) is not str for argument in invocation.arguments)
    ):
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_INVOCATION_INVALID")
    prefix = (
        str(_admission.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY),
        "-C",
        str(facts.worktree),
    )
    if invocation.arguments[:3] != prefix:
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_INVOCATION_INVALID")
    suffix = invocation.arguments[3:]
    if suffix not in _allowed_command_suffixes(facts.expected_release_sha):
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_COMMAND_FORBIDDEN")
    return suffix


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded_local_git(
    *,
    command: tuple[str, ...],
    executable: str,
    environment: dict[str, str],
    pass_fds: tuple[int, ...],
    timeout_seconds: int,
) -> _admission.PhysicalReleaseSealGitCommandResult:
    """Run an already validated Git builtin with bounded stdout and time."""

    process: subprocess.Popen[bytes] | None = None
    stdout = bytearray()
    eof = False
    try:
        process = subprocess.Popen(
            command,
            executable=executable,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env=environment,
            shell=False,
            close_fds=True,
            pass_fds=pass_fds,
            start_new_session=True,
        )
        if process.stdout is None:  # pragma: no cover - PIPE is fixed above
            _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCESS_UNAVAILABLE")
        stdout_descriptor = process.stdout.fileno()
        os.set_blocking(stdout_descriptor, False)
        deadline = time.monotonic() + timeout_seconds
        while not (eof and process.poll() is not None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCESS_TIMEOUT")
            try:
                ready, _, _ = select.select([stdout_descriptor], [], [], remaining)
            except InterruptedError:
                continue
            if ready:
                chunk = os.read(
                    stdout_descriptor,
                    min(64 * 1024, _MAX_GIT_OUTPUT_BYTES + 1 - len(stdout)),
                )
                if chunk:
                    stdout.extend(chunk)
                    if len(stdout) > _MAX_GIT_OUTPUT_BYTES:
                        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_GIT_OUTPUT_TOO_LARGE")
                else:
                    eof = True
        return _admission.PhysicalReleaseSealGitCommandResult(
            exit_code=process.wait(timeout=0),
            stdout_bytes=bytes(stdout),
        )
    except PhysicalReleaseSealLocalInspectionAdapterError:
        if process is not None:
            _terminate_process(process)
        raise
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            _terminate_process(process)
        _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCESS_UNAVAILABLE")
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()


class PhysicalReleaseSealLocalInspectionAdapter:
    """One default-off object implementing both release-seal read-only ports.

    It is deliberately bound to one lexical worktree and one expected release
    SHA.  Passing it as both ``filesystem_inspector`` and ``git_runner`` to
    ``admit_physical_release_seal`` prevents an arbitrary caller from using
    the runner as a broad Git command wrapper.
    """

    def __init__(self, *, config: PhysicalReleaseSealLocalInspectionAdapterConfig) -> None:
        self._facts = _normalise_config(config)

    def inspect_worktree(
        self,
        *,
        worktree: Path,
    ) -> _admission.PhysicalReleaseSealWorktreeInspection:
        if not isinstance(worktree, Path) or worktree != self._facts.worktree:
            _fail("PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_MISMATCH")
        worktree_descriptor = -1
        git_descriptor = -1
        git_binary_descriptor = -1
        try:
            (
                worktree_descriptor,
                git_descriptor,
                worktree_info,
                git_info,
            ) = _open_pinned_worktree(
                self._facts.worktree,
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_UNSAFE",
            )
            git_binary_descriptor, git_binary_info = _open_fixed_git_binary(
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_GIT_BINARY_UNSAFE"
            )
            return _admission.PhysicalReleaseSealWorktreeInspection(
                worktree=_filesystem_object(
                    path=self._facts.worktree,
                    info=worktree_info,
                    regular_file=False,
                    directory=True,
                    executable=bool(worktree_info.st_mode & stat.S_IXUSR),
                ),
                git_metadata=_filesystem_object(
                    path=self._facts.worktree / ".git",
                    info=git_info,
                    regular_file=False,
                    directory=True,
                    executable=bool(git_info.st_mode & stat.S_IXUSR),
                ),
                git_binary=_filesystem_object(
                    path=_admission.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
                    info=git_binary_info,
                    regular_file=True,
                    directory=False,
                    executable=bool(git_binary_info.st_mode & stat.S_IXUSR),
                ),
            )
        finally:
            if git_binary_descriptor >= 0:
                os.close(git_binary_descriptor)
            if git_descriptor >= 0:
                os.close(git_descriptor)
            if worktree_descriptor >= 0:
                os.close(worktree_descriptor)

    def run(
        self,
        *,
        invocation: _admission.PhysicalReleaseSealGitInvocation,
    ) -> _admission.PhysicalReleaseSealGitCommandResult:
        suffix = _validate_invocation(invocation, facts=self._facts)
        worktree_descriptor = -1
        git_descriptor = -1
        git_binary_descriptor = -1
        try:
            (
                worktree_descriptor,
                git_descriptor,
                _,
                _,
            ) = _open_pinned_worktree(
                self._facts.worktree,
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_WORKTREE_UNSAFE",
            )
            git_binary_descriptor, _ = _open_fixed_git_binary(
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_GIT_BINARY_UNSAFE"
            )
            worktree_proc_path = _require_proc_fd_path(
                worktree_descriptor,
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCFS_UNAVAILABLE",
            )
            git_metadata_proc_path = _require_proc_fd_path(
                git_descriptor,
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCFS_UNAVAILABLE",
            )
            git_binary_proc_path = _require_proc_fd_path(
                git_binary_descriptor,
                code="PHYSICAL_RELEASE_SEAL_LOCAL_INSPECTION_PROCFS_UNAVAILABLE",
            )
            environment = dict(invocation.environment)
            # These three descriptor paths are derived after no-follow
            # validation.  They pin the executable, Git directory, and
            # worktree across the subprocess boundary without accepting a
            # controller-controlled path or inherited environment variable.
            environment.update(
                {
                    "GIT_DIR": git_metadata_proc_path,
                    "GIT_WORK_TREE": worktree_proc_path,
                    "GIT_CEILING_DIRECTORIES": "/",
                }
            )
            command = (
                str(_admission.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY),
                "-C",
                worktree_proc_path,
                *suffix,
            )
            return _run_bounded_local_git(
                command=command,
                executable=git_binary_proc_path,
                environment=environment,
                pass_fds=(
                    worktree_descriptor,
                    git_descriptor,
                    git_binary_descriptor,
                ),
                timeout_seconds=self._facts.command_timeout_seconds,
            )
        finally:
            if git_binary_descriptor >= 0:
                os.close(git_binary_descriptor)
            if git_descriptor >= 0:
                os.close(git_descriptor)
            if worktree_descriptor >= 0:
                os.close(worktree_descriptor)
