#!/usr/bin/env python3
"""Prepare a frontend static build from one exact, clean Git release.

This is deliberately an integration-neutral local primitive.  It neither
publishes an artifact nor installs a release, and it does not know about a
campaign, Object Storage, SSH, Docker, ``current``, or a running service.
In particular, it does *not* consume ``expected-static-assets-v2``: that
manifest is useful to a transport consumer, but it is not evidence that a
frontend build was derived from a tracked source release.

The primitive has a narrow trust boundary:

* the source worktree must be root-controlled, clean, checked out at the
  supplied full commit, and have no untracked files;
* Git creates an archive for that commit, and every archive blob is checked
  against the exact Git tree before npm is allowed to run;
* Git and every executable anchor come only from one fixed, root-only local
  tool-policy file.  Node and npm are never run in the controller namespace
  as root;
* the dynamic runtime is an explicit root-only, hash-pinned manifest selected
  only by that fixed policy.  It contains individual files.  The sandbox never binds host ``/usr``, ``/bin``,
  ``/lib``, or ``/lib64`` directories;
* npm runs only with an allowlisted environment and inside the mandatory
  mount, network, and PID namespace.  There is intentionally no fallback; and
* all generated source, cache, dependency tree, build output, and receipt
  live below one newly-created root-only candidate directory.

The resulting receipt is local, unsigned preparation evidence only.  It has
an explicit integration block and is not transport provenance or production
authorization.  A separate controller-verification and signing stage is
required before any transport or installation decision.

Failures after candidate creation retain that candidate for inspection.  A
retry must use a new candidate path, which keeps failed evidence from being
silently overwritten.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import selectors
import signal
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, Mapping, Sequence


SCHEMA = "gold-trade-exact-release-frontend-static-build-v1"
RELEASE_ARCHIVE_NAME = "release-source.tar"
SOURCE_DIRECTORY_NAME = "source"
OFFLINE_CACHE_DIRECTORY_NAME = "npm-cache"
OUTPUT_DIRECTORY_NAME = "static-output"
RECEIPT_NAME = "exact-release-frontend-static-build-receipt.json"
BUILD_SOURCE_DIRECTORY_NAME = "build-source"
SANDBOX_ROOT_DIRECTORY_NAME = "sandbox-root"
SANDBOX_CONTROL_NAME = "sandbox-control.json"
OFFLINE_CACHE_PREFIX = "npm-cache"
STATIC_OUTPUT_RELATIVE = "mini_app_dist"
SANDBOX_CONTROL_SCHEMA = "gold-trade-frontend-build-sandbox-control-v1"
SANDBOX_POLICY_SCHEMA = "gold-trade-frontend-build-sandbox-policy-v1"
RUNTIME_CLOSURE_SCHEMA = "gold-trade-frontend-runtime-closure-v1"
FIXED_TOOL_POLICY_SCHEMA = "gold-trade-exact-release-frontend-tool-policy-v1"
FIXED_TOOL_POLICY_PATH = Path("/etc/trading-bot-three-site/policies/exact-release-frontend-static-build-v1.json")
LOCAL_RECEIPT_PROVENANCE = "local-preparation-only-not-transport-provenance"
LOCAL_RECEIPT_INTEGRATION_STATUS = "blocked-pending-external-controller-signature"

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_OFFLINE_CACHE_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_OFFLINE_CACHE_MEMBERS = 100_000
MAX_OFFLINE_CACHE_DIRECTORIES = 100_000
MAX_TREE_ENTRIES = 100_000
MAX_PATH_BYTES = 1024
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024 * 1024
MAX_OUTPUT_FILES = 100_000
COMMAND_TIMEOUT_SECONDS = 30 * 60
MAX_CAPTURED_COMMAND_BYTES = 16 * 1024 * 1024
MAX_COMMAND_STDERR_BYTES = 4 * 1024 * 1024
MIN_SANDBOX_TMPFS_BYTES = 64 * 1024 * 1024
MAX_SANDBOX_TMPFS_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_BUILD_UID = 65_534
MAX_NPM_RUNTIME_FILES = 20_000
MAX_NPM_RUNTIME_BYTES = 512 * 1024 * 1024
MAX_BUILD_ENVIRONMENT_VALUE_BYTES = 2048
MAX_RUNTIME_CLOSURE_ENTRIES = 4096
MAX_BUILD_PROCESSES = 64
MAX_BUILD_ADDRESS_SPACE_BYTES = 3 * 1024 * 1024 * 1024
MAX_BUILD_CPU_SECONDS = 15 * 60
MAX_SANDBOX_QUIESCENCE_PASSES = 16
MAX_GIT_METADATA_ENTRIES = 200_000
MAX_GIT_CONFIG_BYTES = 2 * 1024 * 1024

FIXED_READ_ONLY_DEVICE_FILES = ("/dev/random", "/dev/urandom")
ALLOWED_BUILD_ENVIRONMENT_KEYS = frozenset({"VITE_API_BASE_URL"})

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.+_-]{1,128}$")
SIMPLE_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,4}$")
GIT_CONFIG_INCLUDE_SECTION_RE = re.compile(r"^\s*\[\s*include(?:if)?(?:\s|\])", re.IGNORECASE)


class ExactReleaseFrontendBuildError(RuntimeError):
    """The exact-release frontend build inputs or result are unsafe."""


@dataclass(frozen=True)
class PinnedToolchain:
    """Two independently pinned local executables used without ``PATH`` lookup."""

    node_path: Path
    node_sha256: str
    node_version: str
    npm_path: Path
    npm_sha256: str
    npm_version: str


@dataclass(frozen=True)
class PinnedSandboxTools:
    """All executables used to create or enter the build sandbox are pinned."""

    python_path: Path
    python_sha256: str
    python_version: str
    unshare_path: Path
    unshare_sha256: str
    unshare_version: str
    setpriv_path: Path
    setpriv_sha256: str
    setpriv_version: str
    mount_path: Path
    mount_sha256: str
    mount_version: str


@dataclass(frozen=True)
class FixedBuildToolPolicy:
    git_path: Path
    git_sha256: str
    git_version: str
    toolchain: PinnedToolchain
    sandbox: PinnedSandboxTools
    runtime_closure_manifest_path: Path
    runtime_closure_manifest_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class RuntimeClosureEntry:
    host_path: Path
    target_path: str
    sha256: str


@dataclass(frozen=True)
class VerifiedRuntimeClosure:
    manifest_path: Path
    manifest_sha256: str
    entries: tuple[RuntimeClosureEntry, ...]


@dataclass(frozen=True)
class VerifiedToolchain:
    node_path: Path
    node_sha256: str
    node_version: str
    npm_path: Path
    npm_sha256: str
    npm_version: str
    npm_runtime_root: Path
    npm_runtime_tree_sha256: str
    npm_runtime_files: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class VerifiedSandbox:
    """Fixed host executables and a deterministic no-host-files policy."""

    python_path: Path
    python_sha256: str
    python_version: str
    unshare_path: Path
    unshare_sha256: str
    unshare_version: str
    setpriv_path: Path
    setpriv_sha256: str
    setpriv_version: str
    mount_path: Path
    mount_sha256: str
    mount_version: str
    policy_sha256: str


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise ExactReleaseFrontendBuildError("exact-release frontend build preparation must run as root")


def _require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ExactReleaseFrontendBuildError(f"{field} must be one lowercase SHA-256 digest")
    return value


def _require_release(value: str) -> str:
    if not isinstance(value, str) or not GIT_SHA1_RE.fullmatch(value):
        raise ExactReleaseFrontendBuildError("release_sha must be one full lowercase Git SHA-1 commit")
    return value


def _require_version(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ExactReleaseFrontendBuildError(f"{field} is invalid")
    return value


def _require_simple_version(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not SIMPLE_VERSION_RE.fullmatch(value):
        raise ExactReleaseFrontendBuildError(f"{field} must be one numeric tool version")
    return value


def _require_absolute(path: Path, *, field: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ExactReleaseFrontendBuildError(f"{field} must be an absolute path")
    return path


def _require_safe_directory_ancestors(path: Path, *, field: str) -> None:
    """Reject an attacker-controlled parent while accepting root-owned ``/tmp``."""

    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ExactReleaseFrontendBuildError(f"{field} ancestor does not exist") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
        ):
            raise ExactReleaseFrontendBuildError(f"{field} has an unsafe ancestor")
        if stat.S_IMODE(metadata.st_mode) & 0o022 and not metadata.st_mode & stat.S_ISVTX:
            raise ExactReleaseFrontendBuildError(f"{field} has a writable non-sticky ancestor")


def _require_root_directory(path: Path, *, field: str, private: bool) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_directory_ancestors(path.parent, field=field)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot inspect {field}") from exc
    denied = 0o077 if private else 0o022
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & denied
    ):
        detail = "root-only" if private else "root-controlled"
        raise ExactReleaseFrontendBuildError(f"{field} must be one {detail} non-symlink directory")
    return resolved


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    path = _require_absolute(path, field=field)
    _require_root_directory(path.parent, field=f"{field} parent", private=True)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or target.st_nlink != 1
        or stat.S_IMODE(target.st_mode) & 0o077
        or not 1 <= target.st_size <= maximum_bytes
    ):
        raise ExactReleaseFrontendBuildError(f"{field} must be a bounded root-only non-symlink file")
    return resolved


def _load_fixed_tool_policy() -> FixedBuildToolPolicy:
    """Load the one root-only local anchor for executable paths and pins.

    The primitive deliberately has no caller-selected executable or Git
    anchor.  This local policy is still not a transport signature: the local
    receipt below remains non-provenance evidence until a separate controller
    signing stage verifies and signs it.
    """

    policy_path = _require_private_file(
        FIXED_TOOL_POLICY_PATH,
        field="fixed frontend build tool policy",
        maximum_bytes=128 * 1024,
    )
    try:
        raw = policy_path.read_bytes()
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_reject_duplicate_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExactReleaseFrontendBuildError("fixed frontend build tool policy is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise ExactReleaseFrontendBuildError("fixed frontend build tool policy must be canonical JSON")
    if (
        set(value) != {"schema", "git", "node", "npm", "sandbox", "runtime_closure"}
        or value.get("schema") != FIXED_TOOL_POLICY_SCHEMA
    ):
        raise ExactReleaseFrontendBuildError("fixed frontend build tool policy fields differ")

    def tool(item: object, *, field: str) -> tuple[Path, str, str]:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "version"}:
            raise ExactReleaseFrontendBuildError(f"fixed tool policy {field} is invalid")
        path_value = item.get("path")
        if not isinstance(path_value, str):
            raise ExactReleaseFrontendBuildError(f"fixed tool policy {field} path is invalid")
        return (
            _require_absolute(Path(path_value), field=f"fixed tool policy {field} path"),
            _require_sha256(item.get("sha256"), field=f"fixed tool policy {field} SHA-256"),
            _require_version(item.get("version"), field=f"fixed tool policy {field} version"),
        )

    git_path, git_sha, git_version = tool(value["git"], field="git")
    node_path, node_sha, node_version = tool(value["node"], field="node")
    npm_path, npm_sha, npm_version = tool(value["npm"], field="npm")
    sandbox_value = value["sandbox"]
    if not isinstance(sandbox_value, Mapping) or set(sandbox_value) != {"python", "unshare", "setpriv", "mount"}:
        raise ExactReleaseFrontendBuildError("fixed tool policy sandbox fields differ")
    python_path, python_sha, python_version = tool(sandbox_value["python"], field="sandbox python")
    unshare_path, unshare_sha, unshare_version = tool(sandbox_value["unshare"], field="sandbox unshare")
    setpriv_path, setpriv_sha, setpriv_version = tool(sandbox_value["setpriv"], field="sandbox setpriv")
    mount_path, mount_sha, mount_version = tool(sandbox_value["mount"], field="sandbox mount")
    closure_value = value["runtime_closure"]
    if not isinstance(closure_value, Mapping) or set(closure_value) != {"path", "sha256"}:
        raise ExactReleaseFrontendBuildError("fixed runtime closure policy fields differ")
    closure_path_value = closure_value.get("path")
    if not isinstance(closure_path_value, str):
        raise ExactReleaseFrontendBuildError("fixed runtime closure policy path is invalid")
    closure_path = _require_absolute(Path(closure_path_value), field="fixed runtime closure policy path")
    closure_sha256 = _require_sha256(closure_value.get("sha256"), field="fixed runtime closure policy SHA-256")
    return FixedBuildToolPolicy(
        git_path=git_path,
        git_sha256=git_sha,
        git_version=git_version,
        toolchain=PinnedToolchain(
            node_path=node_path,
            node_sha256=node_sha,
            node_version=node_version,
            npm_path=npm_path,
            npm_sha256=npm_sha,
            npm_version=npm_version,
        ),
        sandbox=PinnedSandboxTools(
            python_path=python_path,
            python_sha256=python_sha,
            python_version=python_version,
            unshare_path=unshare_path,
            unshare_sha256=unshare_sha,
            unshare_version=unshare_version,
            setpriv_path=setpriv_path,
            setpriv_sha256=setpriv_sha,
            setpriv_version=setpriv_version,
            mount_path=mount_path,
            mount_sha256=mount_sha,
            mount_version=mount_version,
        ),
        runtime_closure_manifest_path=closure_path,
        runtime_closure_manifest_sha256=closure_sha256,
        policy_sha256=sha256_bytes(raw),
    )


def _require_root_tool(path: Path, *, field: str) -> Path:
    """Resolve a fixed tool path, accepting a safe root-controlled symlink."""

    path = _require_absolute(path, field=field)
    _require_safe_directory_ancestors(path.parent, field=field)
    try:
        initial = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot inspect {field}") from exc
    _require_safe_directory_ancestors(resolved.parent, field=field)
    if (
        not (stat.S_ISREG(initial.st_mode) or stat.S_ISLNK(initial.st_mode))
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or target.st_nlink != 1
        or stat.S_IMODE(target.st_mode) & 0o022
        or not target.st_mode & stat.S_IXUSR
        or target.st_size < 1
        or target.st_size > 512 * 1024 * 1024
    ):
        raise ExactReleaseFrontendBuildError(f"{field} must resolve to a root-owned non-writable executable")
    return resolved


def _create_new_root_only_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    parent = _require_root_directory(path.parent, field=f"{field} parent", private=True)
    if path.parent != parent or path.name in {"", ".", ".."}:
        raise ExactReleaseFrontendBuildError(f"{field} is not one direct safe child")
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ExactReleaseFrontendBuildError(f"{field} must not already exist") from exc
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot create {field}") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:  # pragma: no cover - impossible absent hostile filesystem failure.
        raise ExactReleaseFrontendBuildError(f"cannot re-inspect {field}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ExactReleaseFrontendBuildError(f"new {field} is not root-only")
    return path


def _new_private_file(path: Path, *, field: str) -> Any:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        raise


def _run(
    argv: Sequence[str],
    *,
    field: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: Any = subprocess.PIPE,
    maximum_stdout_bytes: int = MAX_CAPTURED_COMMAND_BYTES,
    maximum_stderr_bytes: int = MAX_COMMAND_STDERR_BYTES,
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed argv with bounded diagnostics and no inherited FDs.

    The build receives no host standard streams or extra inherited descriptors.
    Diagnostics are intentionally discarded after bounded capture: source and npm
    output may contain untrusted text and must not become a durable artifact.
    """

    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ExactReleaseFrontendBuildError(f"{field} command is invalid")
    if (
        isinstance(maximum_stdout_bytes, bool)
        or isinstance(maximum_stderr_bytes, bool)
        or not 0 <= maximum_stdout_bytes <= MAX_CAPTURED_COMMAND_BYTES
        or not 0 <= maximum_stderr_bytes <= MAX_COMMAND_STDERR_BYTES
    ):
        raise ExactReleaseFrontendBuildError(f"{field} command output policy is invalid")
    if stdout not in {subprocess.PIPE, subprocess.DEVNULL}:
        raise ExactReleaseFrontendBuildError(f"{field} command stdout target is invalid")
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            close_fds=True,
            pass_fds=(),
            start_new_session=True,
        )
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"{field} command failed") from exc

    streams: dict[int, tuple[Any, int, list[bytes], int]] = {}
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    selector = selectors.DefaultSelector()
    try:
        if stdout is subprocess.PIPE:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            streams[process.stdout.fileno()] = (process.stdout, maximum_stdout_bytes, stdout_chunks, 0)
        assert process.stderr is not None
        selector.register(process.stderr, selectors.EVENT_READ)
        streams[process.stderr.fileno()] = (process.stderr, maximum_stderr_bytes, stderr_chunks, 0)
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            ready = selector.select(remaining)
            if not ready:
                continue
            for key, _events in ready:
                handle, maximum, collected, total = streams[key.fd]
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(handle)
                    handle.close()
                    del streams[key.fd]
                    continue
                if total + len(chunk) > maximum:
                    raise ExactReleaseFrontendBuildError(f"{field} command output exceeds its bound")
                collected.append(chunk)
                streams[key.fd] = (handle, maximum, collected, total + len(chunk))
        return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if return_code:
            raise ExactReleaseFrontendBuildError(f"{field} command failed")
        stdout_payload = b"".join(stdout_chunks) if stdout is subprocess.PIPE else b""
        return subprocess.CompletedProcess(list(argv), return_code, stdout_payload, None)
    except (OSError, TimeoutError, subprocess.SubprocessError, ExactReleaseFrontendBuildError) as exc:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.SubprocessError:  # pragma: no cover - hostile kernel failure.
            pass
        if isinstance(exc, ExactReleaseFrontendBuildError):
            raise
        raise ExactReleaseFrontendBuildError(f"{field} command failed") from exc
    finally:
        selector.close()
        for handle, _maximum, _collected, _total in streams.values():
            try:
                handle.close()
            except OSError:
                pass


def _git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        # Missing objects must make this local primitive fail; Git must never
        # contact a promisor remote while a root process is inspecting source.
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
    }


def _git_command_prefix(git: Path, source_repository: Path) -> list[str]:
    """Return the root-safe Git prefix for an exact, untrusted checkout.

    A release worktree can contain an arbitrary local `.git/config`.  Git
    status may otherwise run its configured fsmonitor process as root before
    the source archive is materialized.  Disable every local-config feature
    that can select an external helper; the release tree still remains the
    sole source for archive content.
    """

    _require_root_controlled_git_metadata(source_repository)
    return [
        str(git),
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.useBuiltinFSMonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        "credential.helper=",
        "-C",
        str(source_repository),
    ]


def _run_git(git: Path, source_repository: Path, arguments: Sequence[str], *, field: str) -> bytes:
    result = _run(
        [*_git_command_prefix(git, source_repository), *arguments],
        field=field,
        env=_git_environment(),
    )
    if not isinstance(result.stdout, bytes):  # pragma: no cover - subprocess API invariant.
        raise ExactReleaseFrontendBuildError(f"{field} command returned no byte output")
    return result.stdout


def _require_clean_exact_source(
    *, git: Path, source_repository: Path, release_sha: str
) -> tuple[str, str]:
    source_repository = _require_root_directory(
        source_repository, field="source_repository", private=False
    )
    top_level = _run_git(git, source_repository, ["rev-parse", "--show-toplevel"], field="Git top-level")
    try:
        top_level_path = Path(top_level.decode("utf-8").strip()).resolve(strict=True)
    except (UnicodeDecodeError, OSError) as exc:
        raise ExactReleaseFrontendBuildError("source_repository Git top-level is invalid") from exc
    if top_level_path != source_repository:
        raise ExactReleaseFrontendBuildError("source_repository must be the Git worktree root")
    status = _run_git(
        git,
        source_repository,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        field="Git source cleanliness",
    )
    if status:
        raise ExactReleaseFrontendBuildError("source_repository must be clean with no untracked files")
    head = _run_git(git, source_repository, ["rev-parse", "--verify", "HEAD^{commit}"], field="Git HEAD").decode("ascii").strip()
    resolved_release = _run_git(
        git,
        source_repository,
        ["rev-parse", "--verify", f"{release_sha}^{{commit}}"],
        field="Git release",
    ).decode("ascii").strip()
    if head != release_sha or resolved_release != release_sha:
        raise ExactReleaseFrontendBuildError("source_repository HEAD must equal the requested exact release_sha")
    tree = _run_git(
        git,
        source_repository,
        ["rev-parse", "--verify", f"{release_sha}^{{tree}}"],
        field="Git release tree",
    ).decode("ascii").strip()
    if not GIT_SHA1_RE.fullmatch(tree):
        raise ExactReleaseFrontendBuildError("Git release tree is not one SHA-1 tree")
    return source_repository.as_posix(), tree


def _safe_relative_path(value: bytes, *, field: str) -> str:
    if not value or len(value) > MAX_PATH_BYTES:
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ExactReleaseFrontendBuildError(f"{field} path must be ASCII") from exc
    if any(ord(item) < 0x20 or ord(item) > 0x7E for item in decoded) or "\\" in decoded:
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    pure = PurePosixPath(decoded)
    if pure.is_absolute() or decoded in {".", ".."} or any(part in {"", ".", ".."} for part in pure.parts):
        raise ExactReleaseFrontendBuildError(f"{field} path is unsafe")
    return decoded


def _git_tree(git: Path, source_repository: Path, release_sha: str) -> dict[str, dict[str, Any]]:
    payload = _run_git(
        git,
        source_repository,
        ["ls-tree", "-r", "-z", "--full-tree", release_sha],
        field="Git release tree listing",
    )
    result: dict[str, dict[str, Any]] = {}
    for record in payload.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            raw_mode, raw_kind, raw_object = header.split(b" ", 2)
            mode = raw_mode.decode("ascii")
            kind = raw_kind.decode("ascii")
            object_id = raw_object.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExactReleaseFrontendBuildError("Git release tree listing is malformed") from exc
        path = _safe_relative_path(raw_path, field="Git release tree")
        if kind != "blob" or mode not in {"100644", "100755"} or not GIT_SHA1_RE.fullmatch(object_id):
            raise ExactReleaseFrontendBuildError("Git release tree contains unsupported entry")
        if path in result or len(result) >= MAX_TREE_ENTRIES:
            raise ExactReleaseFrontendBuildError("Git release tree is duplicated or too large")
        result[path] = {"blob_sha1": object_id, "mode": int(mode[-3:], 8)}
    if not result:
        raise ExactReleaseFrontendBuildError("Git release tree must contain files")
    return result


def _expected_directories(tree: Mapping[str, Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for path in tree:
        parent = PurePosixPath(path).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _write_release_archive(*, git: Path, source_repository: Path, release_sha: str, target: Path) -> tuple[str, int]:
    # Git's default tar.umask can make the archive mode host-config dependent
    # (for example 0644 becomes 0664).  Pin it so member modes remain part of
    # the exact tree check below.
    argv = [
        *_git_command_prefix(git, source_repository),
        "-c",
        "tar.umask=0022",
        "archive",
        "--format=tar",
        release_sha,
    ]
    with _new_private_file(target, field="release archive") as handle:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_git_environment(),
                close_fds=True,
                pass_fds=(),
                start_new_session=True,
            )
        except OSError as exc:
            raise ExactReleaseFrontendBuildError("Git release archive command failed") from exc
        selector = selectors.DefaultSelector()
        stdout_bytes = 0
        stderr_bytes = 0
        try:
            assert process.stdout is not None and process.stderr is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
            streams = {process.stdout.fileno(): "stdout", process.stderr.fileno(): "stderr"}
            while streams:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                for key, _events in selector.select(remaining):
                    chunk = os.read(key.fd, 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        del streams[key.fd]
                        continue
                    if streams[key.fd] == "stdout":
                        stdout_bytes += len(chunk)
                        if stdout_bytes > MAX_ARCHIVE_BYTES:
                            raise ExactReleaseFrontendBuildError("Git release archive exceeds its bound")
                        handle.write(chunk)
                    else:
                        stderr_bytes += len(chunk)
                        if stderr_bytes > MAX_COMMAND_STDERR_BYTES:
                            raise ExactReleaseFrontendBuildError("Git release archive diagnostics exceed their bound")
            if process.wait(timeout=max(0.1, deadline - time.monotonic())):
                raise ExactReleaseFrontendBuildError("Git release archive command failed")
            if stderr_bytes:
                # stderr itself is deliberately never persisted because it can
                # contain untrusted source text.  A warning is an ambiguity.
                raise ExactReleaseFrontendBuildError("Git release archive emitted unexpected diagnostics")
        except (OSError, TimeoutError, subprocess.SubprocessError, ExactReleaseFrontendBuildError) as exc:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.SubprocessError:  # pragma: no cover - hostile kernel failure.
                pass
            if isinstance(exc, ExactReleaseFrontendBuildError):
                raise
            raise ExactReleaseFrontendBuildError("Git release archive command failed") from exc
        finally:
            selector.close()
        handle.flush()
        os.fsync(handle.fileno())
    archive = _require_private_file(target, field="release archive", maximum_bytes=MAX_ARCHIVE_BYTES)
    return sha256_file(archive)


def _mkdir_private_child(parent: Path, relative: str) -> Path:
    target = parent
    for part in PurePosixPath(relative).parts:
        target = target / part
        try:
            target.mkdir(mode=0o700)
        except FileExistsError:
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ExactReleaseFrontendBuildError("archive attempted to traverse a non-directory")
        except OSError as exc:
            raise ExactReleaseFrontendBuildError("cannot materialize archive directory") from exc
        metadata = target.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ExactReleaseFrontendBuildError("materialized archive directory is unsafe")
    return target


def _write_member(
    *, target: Path, member: tarfile.TarInfo, stream: Any, expected_blob_sha1: str, mode: int
) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags, 0o700 if mode == 0o755 else 0o600)
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot materialize release archive file") from exc
    digest = hashlib.sha256()
    git_digest = hashlib.sha1()
    git_digest.update(f"blob {member.size}\0".encode("ascii"))
    total = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > member.size or total > MAX_FILE_BYTES:
                    raise ExactReleaseFrontendBuildError("release archive file exceeds its bounds")
                digest.update(chunk)
                git_digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != member.size or git_digest.hexdigest() != expected_blob_sha1:
            raise ExactReleaseFrontendBuildError("release archive blob does not match the exact Git tree")
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1:
            raise ExactReleaseFrontendBuildError("materialized release archive file is unsafe")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _write_cache_member(*, target: Path, member: tarfile.TarInfo, stream: Any) -> tuple[str, int]:
    """Write one generic offline-cache data member without Git blob semantics."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot materialize offline dependency archive file") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > member.size or total > MAX_FILE_BYTES:
                    raise ExactReleaseFrontendBuildError("offline dependency archive file exceeds its bounds")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != member.size:
            raise ExactReleaseFrontendBuildError("offline dependency archive file is truncated")
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1:
            raise ExactReleaseFrontendBuildError("materialized offline dependency archive file is unsafe")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _validate_pax_headers(member: tarfile.TarInfo, *, release_sha: str, field: str) -> None:
    # Git's normal tar format adds this one harmless provenance comment to each
    # member.  No arbitrary extended headers are accepted.
    if member.pax_headers and member.pax_headers != {"comment": release_sha}:
        raise ExactReleaseFrontendBuildError(f"{field} has unsupported extended tar metadata")


def _verify_and_materialize_release_archive(
    *, archive_path: Path, source_directory: Path, tree: Mapping[str, Mapping[str, Any]], release_sha: str
) -> dict[str, Any]:
    expected_directories = _expected_directories(tree)
    found_files: set[str] = set()
    found_directories: set[str] = set()
    files: list[dict[str, Any]] = []
    before = sha256_file(archive_path)
    _create_new_root_only_directory(source_directory, field="materialized source directory")
    try:
        with tarfile.open(archive_path, "r:") as archive:
            for member in archive:
                _validate_pax_headers(member, release_sha=release_sha, field="release archive")
                path = _safe_relative_path(member.name.encode("ascii"), field="release archive")
                if member.isdir():
                    if path not in expected_directories or path in found_directories:
                        raise ExactReleaseFrontendBuildError("release archive directory does not match the exact Git tree")
                    _mkdir_private_child(source_directory, path)
                    found_directories.add(path)
                    continue
                if (
                    not member.isreg()
                    or member.issparse()
                    or member.linkname
                    or member.size < 0
                    or member.size > MAX_FILE_BYTES
                    or path not in tree
                    or path in found_files
                    or member.mode != tree[path]["mode"]
                ):
                    raise ExactReleaseFrontendBuildError("release archive entry does not match the exact Git tree")
                parent = PurePosixPath(path).parent
                if parent != PurePosixPath("."):
                    _mkdir_private_child(source_directory, parent.as_posix())
                stream = archive.extractfile(member)
                if stream is None:
                    raise ExactReleaseFrontendBuildError("release archive file cannot be read")
                try:
                    payload_sha256, payload_bytes = _write_member(
                        target=source_directory / path,
                        member=member,
                        stream=stream,
                        expected_blob_sha1=str(tree[path]["blob_sha1"]),
                        mode=int(tree[path]["mode"]),
                    )
                finally:
                    stream.close()
                files.append(
                    {
                        "path": path,
                        "blob_sha1": tree[path]["blob_sha1"],
                        "sha256": payload_sha256,
                        "bytes": payload_bytes,
                        "mode": tree[path]["mode"],
                    }
                )
                found_files.add(path)
    except (OSError, tarfile.TarError) as exc:
        raise ExactReleaseFrontendBuildError("cannot inspect or materialize release archive") from exc
    if found_files != set(tree) or found_directories != expected_directories:
        raise ExactReleaseFrontendBuildError("release archive does not completely match the exact Git tree")
    after = sha256_file(archive_path)
    if after != before:
        raise ExactReleaseFrontendBuildError("release archive changed while being verified")
    files.sort(key=lambda item: str(item["path"]))
    return {
        "archive_sha256": before[0],
        "archive_bytes": before[1],
        "files_sha256": sha256_bytes(canonical_json_bytes(files)),
        "file_count": len(files),
        "files": files,
    }


def _safe_cache_member_path(name: str) -> str:
    try:
        encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExactReleaseFrontendBuildError("offline dependency archive path must be ASCII") from exc
    path = _safe_relative_path(encoded, field="offline dependency archive")
    if path != OFFLINE_CACHE_PREFIX and not path.startswith(f"{OFFLINE_CACHE_PREFIX}/"):
        raise ExactReleaseFrontendBuildError("offline dependency archive must contain only npm-cache members")
    return path


def _extract_offline_cache(
    *, archive_path: Path, expected_sha256: str, candidate_directory: Path
) -> dict[str, Any]:
    archive_path = _require_private_file(
        archive_path,
        field="offline dependency archive",
        maximum_bytes=MAX_OFFLINE_CACHE_ARCHIVE_BYTES,
    )
    before = sha256_file(archive_path)
    if before[0] != expected_sha256:
        raise ExactReleaseFrontendBuildError("offline dependency archive SHA-256 does not match its pin")
    cache_root = candidate_directory / OFFLINE_CACHE_DIRECTORY_NAME
    _create_new_root_only_directory(cache_root, field="offline npm-cache directory")
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    directories: set[str] = set()
    total = 0

    def register_directories(relative: PurePosixPath) -> None:
        current = relative
        while current != PurePosixPath("."):
            directories.add(current.as_posix())
            current = current.parent
        if len(directories) > MAX_OFFLINE_CACHE_DIRECTORIES:
            raise ExactReleaseFrontendBuildError("offline dependency archive has too many directories")

    try:
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive:
                if member.pax_headers or member.linkname or member.issparse():
                    raise ExactReleaseFrontendBuildError("offline dependency archive contains unsupported metadata")
                path = _safe_cache_member_path(member.name)
                if path in seen:
                    raise ExactReleaseFrontendBuildError("offline dependency archive has duplicate members")
                if len(seen) >= MAX_OFFLINE_CACHE_MEMBERS:
                    raise ExactReleaseFrontendBuildError("offline dependency archive has too many members")
                seen.add(path)
                relative = PurePosixPath(path).relative_to(OFFLINE_CACHE_PREFIX)
                if member.isdir():
                    if relative != PurePosixPath("."):
                        register_directories(relative)
                        _mkdir_private_child(cache_root, relative.as_posix())
                    continue
                if not member.isreg() or member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise ExactReleaseFrontendBuildError("offline dependency archive contains an unsupported member")
                if relative == PurePosixPath("."):
                    raise ExactReleaseFrontendBuildError("offline dependency archive has an invalid cache member")
                register_directories(relative.parent)
                if total + member.size > MAX_OFFLINE_CACHE_ARCHIVE_BYTES:
                    raise ExactReleaseFrontendBuildError("offline dependency archive exceeds its total expansion bound")
                _mkdir_private_child(cache_root, relative.parent.as_posix()) if relative.parent != PurePosixPath(".") else None
                stream = archive.extractfile(member)
                if stream is None:
                    raise ExactReleaseFrontendBuildError("offline dependency archive member cannot be read")
                try:
                    # Cache files are data, not executable code.  npm can read them
                    # as root but cannot execute a cache member directly.
                    digest, bytes_value = _write_cache_member(
                        target=cache_root / relative.as_posix(), member=member, stream=stream
                    )
                finally:
                    stream.close()
                total += bytes_value
                if total > MAX_OFFLINE_CACHE_ARCHIVE_BYTES:  # pragma: no cover - guarded before extraction.
                    raise ExactReleaseFrontendBuildError("offline dependency archive exceeds its total expansion bound")
                files.append({"path": relative.as_posix(), "sha256": digest, "bytes": bytes_value})
    except (OSError, tarfile.TarError) as exc:
        raise ExactReleaseFrontendBuildError("cannot inspect or extract offline dependency archive") from exc
    if not files:
        raise ExactReleaseFrontendBuildError("offline dependency archive must contain cache files")
    after = sha256_file(archive_path)
    if after != before:
        raise ExactReleaseFrontendBuildError("offline dependency archive changed while being extracted")
    files.sort(key=lambda item: str(item["path"]))
    return {
        "archive_sha256": before[0],
        "archive_bytes": before[1],
        "files_sha256": sha256_bytes(canonical_json_bytes(files)),
        "file_count": len(files),
        "bytes": total,
    }


def _root_owned_tree_files(
    path: Path, *, field: str, maximum_files: int, maximum_bytes: int
) -> tuple[dict[str, Any], ...]:
    """Hash a small fixed runtime tree without accepting links or writable files."""

    root = _require_root_directory(path, field=field, private=False)
    files: list[dict[str, Any]] = []
    total = 0
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        names.sort()
        for directory in directories:
            state = (current_path / directory).lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != 0
                or stat.S_IMODE(state.st_mode) & 0o022
            ):
                raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe directory")
        for name in names:
            candidate = current_path / name
            state = candidate.lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISREG(state.st_mode)
                or state.st_uid != 0
                or state.st_nlink != 1
                or stat.S_IMODE(state.st_mode) & 0o022
                or state.st_size > MAX_FILE_BYTES
            ):
                raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe file")
            relative = _safe_relative_path(candidate.relative_to(root).as_posix().encode("ascii"), field=field)
            digest, bytes_value = sha256_file(candidate)
            total += bytes_value
            if len(files) >= maximum_files or total > maximum_bytes:
                raise ExactReleaseFrontendBuildError(f"{field} exceeds its bounds")
            files.append({"path": relative, "sha256": digest, "bytes": bytes_value})
    if not files:
        raise ExactReleaseFrontendBuildError(f"{field} must contain regular files")
    files.sort(key=lambda item: str(item["path"]))
    return tuple(files)


def _root_owned_tree_sha256(
    path: Path, *, field: str, maximum_files: int, maximum_bytes: int
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            _root_owned_tree_files(
                path,
                field=field,
                maximum_files=maximum_files,
                maximum_bytes=maximum_bytes,
            )
        )
    )


def _npm_runtime_root(npm: Path) -> Path:
    """Accept only npm's canonical package-root layout, not an arbitrary script."""

    if npm.name != "npm-cli.js" or npm.parent.name != "bin":
        raise ExactReleaseFrontendBuildError("npm tool must resolve to npm/bin/npm-cli.js")
    runtime_root = npm.parent.parent
    _require_root_directory(runtime_root, field="npm runtime root", private=False)
    if not (runtime_root / "package.json").is_file() or not (runtime_root / "bin" / "npm-cli.js").is_file():
        raise ExactReleaseFrontendBuildError("npm runtime root is incomplete")
    return runtime_root


def _verify_toolchain(pin: PinnedToolchain) -> VerifiedToolchain:
    node = _require_root_tool(pin.node_path, field="node tool")
    npm = _require_root_tool(pin.npm_path, field="npm tool")
    node_sha256 = _require_sha256(pin.node_sha256, field="node_sha256")
    npm_sha256 = _require_sha256(pin.npm_sha256, field="npm_sha256")
    node_version = _require_simple_version(pin.node_version, field="node_version")
    npm_version = _require_simple_version(pin.npm_version, field="npm_version")
    if sha256_file(node)[0] != node_sha256:
        raise ExactReleaseFrontendBuildError("node tool SHA-256 does not match its pin")
    if sha256_file(npm)[0] != npm_sha256:
        raise ExactReleaseFrontendBuildError("npm tool SHA-256 does not match its pin")
    # Never execute Node or npm in the controller namespace as root.  Their
    # Fixed-policy versions are checked only by the mandatory dropped-UID
    # sandbox probe below, after the full chroot/mount layout is active.
    runtime_root = _npm_runtime_root(npm)
    runtime_files = _root_owned_tree_files(
        runtime_root,
        field="npm runtime root",
        maximum_files=MAX_NPM_RUNTIME_FILES,
        maximum_bytes=MAX_NPM_RUNTIME_BYTES,
    )
    runtime_tree_sha256 = sha256_bytes(canonical_json_bytes(runtime_files))
    # Rehash immediately before use to make replacement of a mutable tool fail.
    if sha256_file(node)[0] != node_sha256 or sha256_file(npm)[0] != npm_sha256:
        raise ExactReleaseFrontendBuildError("pinned local toolchain changed while being verified")
    return VerifiedToolchain(
        node,
        node_sha256,
        node_version,
        npm,
        npm_sha256,
        npm_version,
        runtime_root,
        runtime_tree_sha256,
        runtime_files,
    )


def _validate_build_environment(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - ALLOWED_BUILD_ENVIRONMENT_KEYS:
        raise ExactReleaseFrontendBuildError("build environment contains an unapproved key")
    result: dict[str, str] = {}
    for key in sorted(value):
        item = value[key]
        if not isinstance(key, str) or not isinstance(item, str):
            raise ExactReleaseFrontendBuildError("build environment is invalid")
        encoded = item.encode("utf-8")
        if len(encoded) > MAX_BUILD_ENVIRONMENT_VALUE_BYTES or "\x00" in item or any(ord(char) < 0x20 for char in item):
            raise ExactReleaseFrontendBuildError("build environment value is invalid")
        result[key] = item
    return result


def _sandbox_environment(build_environment: Mapping[str, str]) -> dict[str, str]:
    """The complete environment exposed to npm after chroot and privilege drop."""

    result = {
        "FRONTEND_BUILD_OUT_DIR": "/scratch/source/mini_app_dist",
        "HOME": "/scratch/home",
        "LANG": "C",
        "LC_ALL": "C",
        "NODE_ENV": "production",
        "PATH": "/tool:/scratch/source/frontend/node_modules/.bin",
        "TMPDIR": "/scratch/tmp",
        "npm_config_audit": "false",
        "npm_config_cache": "/scratch/cache",
        "npm_config_fetch_retries": "0",
        "npm_config_fund": "false",
        "npm_config_ignore_scripts": "true",
        "npm_config_logs_max": "0",
        "npm_config_offline": "true",
        "npm_config_progress": "false",
        "npm_config_registry": "http://127.0.0.1:9",
        "npm_config_script_shell": "/tool/sh",
        "npm_config_update_notifier": "false",
    }
    result.update(build_environment)
    return result


def _sandbox_launch_environment(build_environment: Mapping[str, str]) -> dict[str, str]:
    """The namespace bootstrap receives only the approved Vite projection."""

    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        **build_environment,
    }


def _sandbox_policy(*, tmpfs_bytes: int, build_uid: int) -> dict[str, Any]:
    """Fixed no-fallback sandbox policy; callers cannot add host mounts or FDs."""

    return {
        "schema": SANDBOX_POLICY_SCHEMA,
        "build_uid": build_uid,
        "mount_namespace_required": True,
        "network_namespace_required": True,
        "pid_namespace_required": True,
        "mount_propagation_private": True,
        "chroot_required": True,
        "no_host_root_binding": True,
        "no_inherited_file_descriptors": True,
        "explicit_capability_drop": True,
        "no_untrusted_processes_before_output_handoff": True,
        "read_only_bindings": [
            "/input/cache",
            "/input/source",
            "/tool/node",
            "/tool/npm/<individual-file>",
            "runtime-closure/<individual-file>",
            "/dev/null",
            "/dev/random",
            "/dev/urandom",
        ],
        "writable_paths": ["/handoff-output", "/scratch"],
        "offline_cache_copied_to_bounded_tmpfs": True,
        "runtime_closure_individual_files_only": True,
        "host_runtime_directory_bindings_denied": True,
        "system_dynamic_runtime": "root-controlled-read-only-not-hermetic",
        "alternate_node_mounts_denied": True,
        "lifecycle_scripts_enabled": False,
        "tmpfs_bytes": tmpfs_bytes,
        "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "command_stdout_bytes": MAX_CAPTURED_COMMAND_BYTES,
        "command_stderr_bytes": MAX_COMMAND_STDERR_BYTES,
        "rlimit_nproc": MAX_BUILD_PROCESSES,
        "rlimit_as_bytes": MAX_BUILD_ADDRESS_SPACE_BYTES,
        "rlimit_cpu_seconds": MAX_BUILD_CPU_SECONDS,
        "rlimit_fsize_bytes": MAX_OUTPUT_BYTES,
    }


def _extract_simple_version(value: str, *, field: str) -> str:
    # Node reports ``v20.19.5`` while the other fixed tools usually report a
    # bare numeric version.  Capture the numeric portion without accepting a
    # suffix such as ``19.5`` from the middle of Node's leading ``v`` form.
    match = re.search(r"(?<![0-9A-Za-z])v?([0-9]+(?:\.[0-9]+){1,4})(?![0-9A-Za-z])", value)
    if match is None:
        raise ExactReleaseFrontendBuildError(f"{field} did not report a parseable version")
    return match.group(1)


def _verify_sandbox_tool(
    *, path: Path, sha256: str, version: str, expected_name: str, field: str
) -> tuple[Path, str, str]:
    resolved = _require_root_tool(path, field=field)
    if (expected_name == "python3" and not resolved.name.startswith("python3")) or (
        expected_name != "python3" and resolved.name != expected_name
    ):
        raise ExactReleaseFrontendBuildError(f"{field} must resolve to {expected_name}")
    expected_sha = _require_sha256(sha256, field=f"{field}_sha256")
    expected_version = _require_version(version, field=f"{field}_version")
    if sha256_file(resolved)[0] != expected_sha:
        raise ExactReleaseFrontendBuildError(f"{field} SHA-256 does not match its pin")
    actual_version_raw = _run(
        [str(resolved), "--version"],
        field=f"{field} version",
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    ).stdout.decode("ascii", "strict").strip()
    actual_version = _extract_simple_version(actual_version_raw, field=field)
    if actual_version != expected_version:
        raise ExactReleaseFrontendBuildError(f"{field} version does not match its pin")
    if sha256_file(resolved)[0] != expected_sha:
        raise ExactReleaseFrontendBuildError(f"{field} changed while being verified")
    return resolved, expected_sha, actual_version


def _verify_sandbox(
    *, pinned: PinnedSandboxTools, tmpfs_bytes: int, build_uid: int
) -> VerifiedSandbox:
    if (
        isinstance(tmpfs_bytes, bool)
        or not isinstance(tmpfs_bytes, int)
        or not MIN_SANDBOX_TMPFS_BYTES <= tmpfs_bytes <= MAX_SANDBOX_TMPFS_BYTES
    ):
        raise ExactReleaseFrontendBuildError("sandbox_tmpfs_bytes is outside the approved bounded range")
    if isinstance(build_uid, bool) or not isinstance(build_uid, int) or not 1 <= build_uid <= 2**31 - 1:
        raise ExactReleaseFrontendBuildError("sandbox build UID is invalid")
    python, python_sha, python_version = _verify_sandbox_tool(
        path=pinned.python_path,
        sha256=pinned.python_sha256,
        version=pinned.python_version,
        expected_name="python3",
        field="sandbox python tool",
    )
    unshare, unshare_sha, unshare_version = _verify_sandbox_tool(
        path=pinned.unshare_path,
        sha256=pinned.unshare_sha256,
        version=pinned.unshare_version,
        expected_name="unshare",
        field="sandbox unshare tool",
    )
    setpriv, setpriv_sha, setpriv_version = _verify_sandbox_tool(
        path=pinned.setpriv_path,
        sha256=pinned.setpriv_sha256,
        version=pinned.setpriv_version,
        expected_name="setpriv",
        field="sandbox privilege-drop tool",
    )
    mount, mount_sha, mount_version = _verify_sandbox_tool(
        path=pinned.mount_path,
        sha256=pinned.mount_sha256,
        version=pinned.mount_version,
        expected_name="mount",
        field="sandbox mount tool",
    )
    policy = _sandbox_policy(tmpfs_bytes=tmpfs_bytes, build_uid=build_uid)
    return VerifiedSandbox(
        python_path=python,
        python_sha256=python_sha,
        python_version=python_version,
        unshare_path=unshare,
        unshare_sha256=unshare_sha,
        unshare_version=unshare_version,
        setpriv_path=setpriv,
        setpriv_sha256=setpriv_sha,
        setpriv_version=setpriv_version,
        mount_path=mount,
        mount_sha256=mount_sha,
        mount_version=mount_version,
        policy_sha256=sha256_bytes(canonical_json_bytes(policy)),
    )


def _require_root_controlled_file(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_directory_ancestors(path.parent, field=field)
    try:
        initial = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(initial.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or target.st_nlink != 1
        or stat.S_IMODE(target.st_mode) & 0o022
        or not 1 <= target.st_size <= MAX_FILE_BYTES
    ):
        raise ExactReleaseFrontendBuildError(f"{field} must be a root-controlled regular file")
    return resolved


def _require_pinned_root_tool_unchanged(path: Path, sha256: str, *, field: str) -> Path:
    """Re-check a host-side launcher immediately before root executes it."""

    resolved = _require_root_tool(path, field=field)
    expected = _require_sha256(sha256, field=f"{field} SHA-256")
    if sha256_file(resolved)[0] != expected:
        raise ExactReleaseFrontendBuildError(f"{field} changed before execution")
    return resolved


def _read_root_controlled_git_pointer(path: Path, *, field: str, prefix: bytes | None) -> bytes:
    """Read one small Git path pointer without accepting a symlink or include."""

    pointer = _require_root_controlled_file(path, field=field)
    try:
        metadata = pointer.lstat()
        if not 1 <= metadata.st_size <= MAX_PATH_BYTES + 32:
            raise ExactReleaseFrontendBuildError(f"{field} is not one bounded Git path pointer")
        raw = pointer.read_bytes()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot read {field}") from exc
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ExactReleaseFrontendBuildError(f"{field} is not one bounded Git path pointer")
    body = raw[:-1]
    if prefix is not None:
        if not body.startswith(prefix):
            raise ExactReleaseFrontendBuildError(f"{field} is not one Git directory pointer")
        body = body[len(prefix) :]
    if not body or b"\x00" in body:
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    try:
        body.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise ExactReleaseFrontendBuildError(f"{field} path must be ASCII") from exc
    if any(item < 0x20 or item > 0x7E for item in body):
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    return body


def _resolve_root_controlled_git_pointer(
    value: bytes,
    *,
    base: Path,
    expected_kind: str,
    field: str,
) -> Path:
    """Resolve a Git ``gitdir``/``commondir`` pointer without crossing links.

    Linked worktrees use relative ``commondir`` values such as ``../..``.  A
    lexical resolver therefore checks every actual component rather than using
    ``Path.resolve`` and accidentally hiding a symlink behind ``..``.
    """

    if expected_kind not in {"directory", "file"}:  # pragma: no cover - internal invariant.
        raise ExactReleaseFrontendBuildError("Git pointer expected kind is invalid")
    try:
        text = value.decode("ascii", "strict")
    except UnicodeDecodeError as exc:  # pragma: no cover - checked by the reader.
        raise ExactReleaseFrontendBuildError(f"{field} path must be ASCII") from exc
    if text.startswith("//") or "\\" in text:
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    if text.startswith("/"):
        current = _require_root_directory(Path("/"), field=f"{field} root", private=False)
        components = text.split("/")[1:]
    else:
        current = _require_root_directory(base, field=f"{field} base", private=False)
        components = text.split("/")
    if not components:
        raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
    for index, component in enumerate(components):
        if not component or component == ".":
            raise ExactReleaseFrontendBuildError(f"{field} path is invalid")
        if component == "..":
            if current == Path("/"):
                raise ExactReleaseFrontendBuildError(f"{field} path escapes the filesystem root")
            current = _require_root_directory(current.parent, field=f"{field} parent", private=False)
            continue
        target = current / component
        is_final = index == len(components) - 1
        if is_final and expected_kind == "file":
            return _require_root_controlled_file(target, field=field)
        current = _require_root_directory(target, field=field, private=False)
    if expected_kind != "directory":
        raise ExactReleaseFrontendBuildError(f"{field} path does not name a file")
    return current


def _require_root_controlled_git_tree(path: Path, *, field: str) -> None:
    """Fail closed if an object/ref tree contains a link or writable entry."""

    root = _require_root_directory(path, field=field, private=False)
    entries = 0
    try:
        for current, directories, names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            directories.sort()
            names.sort()
            for name in [*directories, *names]:
                entries += 1
                if entries > MAX_GIT_METADATA_ENTRIES:
                    raise ExactReleaseFrontendBuildError(f"{field} exceeds its entry bound")
                candidate = current_path / name
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
                    raise ExactReleaseFrontendBuildError(f"{field} contains a symlink or writable entry")
                if name in directories:
                    if not stat.S_ISDIR(metadata.st_mode):  # pragma: no cover - os.walk invariant.
                        raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe directory")
                elif not stat.S_ISREG(metadata.st_mode):
                    raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe file")
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot inspect {field}") from exc


def _reject_git_config_includes(path: Path, *, field: str) -> None:
    """Do not let a local config redirect this root invocation to another file."""

    config = _require_root_controlled_file(path, field=field)
    try:
        metadata = config.lstat()
        if metadata.st_size > MAX_GIT_CONFIG_BYTES:
            raise ExactReleaseFrontendBuildError(f"{field} exceeds its bound")
        raw = config.read_bytes()
        text = raw.decode("utf-8", "strict")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExactReleaseFrontendBuildError(f"{field} is not valid UTF-8") from exc
    if any(GIT_CONFIG_INCLUDE_SECTION_RE.match(line) for line in text.splitlines()):
        raise ExactReleaseFrontendBuildError(f"{field} must not include another configuration file")


def _require_root_controlled_git_metadata(source_repository: Path) -> None:
    """Validate the full local Git metadata chain before every root Git call.

    Worktree ownership alone is insufficient: a root-owned ``.git`` file may
    point to a separately writable worktree admin directory or common object
    store.  Git reads that metadata before it can prove the requested commit.
    """

    source = _require_root_directory(source_repository, field="source_repository", private=False)
    marker = source / ".git"
    try:
        marker_state = marker.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot inspect source_repository Git metadata") from exc
    linked_worktree = False
    if stat.S_ISDIR(marker_state.st_mode):
        gitdir = _require_root_directory(marker, field="source_repository .git directory", private=False)
    elif stat.S_ISREG(marker_state.st_mode):
        linked_worktree = True
        pointer = _read_root_controlled_git_pointer(
            marker,
            field="source_repository .git pointer",
            prefix=b"gitdir: ",
        )
        gitdir = _resolve_root_controlled_git_pointer(
            pointer,
            base=source,
            expected_kind="directory",
            field="source_repository gitdir",
        )
    else:
        raise ExactReleaseFrontendBuildError("source_repository .git must be a root-controlled directory or pointer file")

    commondir_pointer = gitdir / "commondir"
    if commondir_pointer.exists() or commondir_pointer.is_symlink():
        pointer = _read_root_controlled_git_pointer(
            commondir_pointer,
            field="source_repository commondir pointer",
            prefix=None,
        )
        common = _resolve_root_controlled_git_pointer(
            pointer,
            base=gitdir,
            expected_kind="directory",
            field="source_repository common Git directory",
        )
    else:
        common = gitdir

    if linked_worktree:
        back_pointer = _read_root_controlled_git_pointer(
            gitdir / "gitdir",
            field="source_repository linked gitdir pointer",
            prefix=None,
        )
        back = _resolve_root_controlled_git_pointer(
            back_pointer,
            base=gitdir,
            expected_kind="file",
            field="source_repository linked gitdir target",
        )
        if back != marker:
            raise ExactReleaseFrontendBuildError("source_repository linked gitdir pointer does not return to its .git file")

    _require_root_controlled_git_tree(gitdir, field="source_repository worktree Git metadata")
    if common != gitdir:
        _require_root_controlled_git_tree(common, field="source_repository common Git metadata")
    _reject_git_config_includes(common / "config", field="source_repository common Git config")
    worktree_config = gitdir / "config.worktree"
    if worktree_config.exists() or worktree_config.is_symlink():
        _reject_git_config_includes(worktree_config, field="source_repository worktree Git config")
    for path, field in (
        (gitdir / "HEAD", "source_repository worktree Git HEAD"),
        (gitdir / "index", "source_repository worktree Git index"),
        (common / "HEAD", "source_repository common Git HEAD"),
        (common / "packed-refs", "source_repository packed refs"),
    ):
        if path.exists() or path.is_symlink():
            _require_root_controlled_file(path, field=field)
    objects = _require_root_directory(common / "objects", field="source_repository Git objects", private=False)
    for alternate in (objects / "info" / "alternates", objects / "info" / "http-alternates"):
        if alternate.exists() or alternate.is_symlink():
            raise ExactReleaseFrontendBuildError("source_repository Git object alternates are forbidden")
    refs = common / "refs"
    if refs.exists() or refs.is_symlink():
        _require_root_directory(refs, field="source_repository Git refs", private=False)


def _runtime_closure_target(value: object) -> str:
    if not isinstance(value, str):
        raise ExactReleaseFrontendBuildError("runtime closure target path is invalid")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ExactReleaseFrontendBuildError("runtime closure target path must be ASCII") from exc
    if not value.startswith("/"):
        raise ExactReleaseFrontendBuildError("runtime closure target path must be absolute")
    if value.endswith("/") or "//" in value:
        raise ExactReleaseFrontendBuildError("runtime closure target must name one canonical file")
    relative = _safe_relative_path(encoded.lstrip(b"/"), field="runtime closure target")
    target = f"/{relative}"
    allowed = (
        target in {"/tool/setpriv", "/tool/sh", "/usr/bin/env"}
        or target.startswith("/lib/")
        or target.startswith("/lib64/")
        or target.startswith("/usr/lib/")
    )
    if not allowed:
        raise ExactReleaseFrontendBuildError("runtime closure target is outside the fixed minimal runtime")
    return target


def _load_runtime_closure(
    *,
    manifest_path: Path | None,
    expected_sha256: str | None,
    setpriv_path: Path,
    setpriv_sha256: str,
) -> VerifiedRuntimeClosure:
    """Verify an absent-by-default, individual-file runtime closure.

    A host ``/lib`` or ``/usr`` bind is intentionally never accepted.  The
    manifest is a root-only local input that enumerates exactly the ELF
    interpreter and libraries needed by the pinned node/setpriv/sh/env tools.
    Its availability is an explicit prerequisite, not something this script
    creates, downloads, or infers from the host package set.
    """

    if manifest_path is None or expected_sha256 is None:
        raise ExactReleaseFrontendBuildError("a pinned runtime closure manifest is required before candidate creation")
    expected = _require_sha256(expected_sha256, field="runtime closure manifest SHA-256")
    manifest = _require_private_file(
        manifest_path,
        field="runtime closure manifest",
        maximum_bytes=2 * 1024 * 1024,
    )
    try:
        raw = manifest.read_bytes()
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_reject_duplicate_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExactReleaseFrontendBuildError("runtime closure manifest is invalid") from exc
    if sha256_bytes(raw) != expected or not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise ExactReleaseFrontendBuildError("runtime closure manifest does not match its canonical SHA-256 pin")
    if set(value) != {"schema", "entries"} or value.get("schema") != RUNTIME_CLOSURE_SCHEMA:
        raise ExactReleaseFrontendBuildError("runtime closure manifest fields differ")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list) or not 4 <= len(raw_entries) <= MAX_RUNTIME_CLOSURE_ENTRIES:
        raise ExactReleaseFrontendBuildError("runtime closure manifest entries are invalid")
    entries: list[RuntimeClosureEntry] = []
    targets: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, Mapping) or set(item) != {"host_path", "target_path", "sha256"}:
            raise ExactReleaseFrontendBuildError("runtime closure entry fields differ")
        host_text = item.get("host_path")
        if not isinstance(host_text, str):
            raise ExactReleaseFrontendBuildError("runtime closure host path is invalid")
        host = _require_root_controlled_file(Path(host_text), field="runtime closure host file")
        target = _runtime_closure_target(item.get("target_path"))
        digest = _require_sha256(item.get("sha256"), field="runtime closure entry SHA-256")
        if target in targets:
            raise ExactReleaseFrontendBuildError("runtime closure manifest has duplicate targets")
        targets.add(target)
        before, bytes_value = sha256_file(host)
        # Re-inspect the parent and file after hashing.  A root-only closure
        # should not race, but a replacement still fails closed.
        if before != digest or bytes_value < 1 or _require_root_controlled_file(host, field="runtime closure host file") != host or sha256_file(host)[0] != digest:
            raise ExactReleaseFrontendBuildError("runtime closure host file does not match its pin")
        entries.append(RuntimeClosureEntry(host_path=host, target_path=target, sha256=digest))
    entries.sort(key=lambda item: item.target_path)
    if [item.target_path for item in entries] != [item.get("target_path") for item in raw_entries]:
        raise ExactReleaseFrontendBuildError("runtime closure entries must be ordered by target path")
    required = {"/tool/setpriv", "/tool/sh", "/usr/bin/env"}
    if not required <= targets or not any(target.startswith(("/lib/", "/lib64/", "/usr/lib/")) for target in targets):
        raise ExactReleaseFrontendBuildError("runtime closure omits a required executable or dynamic library")
    setpriv_entry = next(item for item in entries if item.target_path == "/tool/setpriv")
    if setpriv_entry.host_path != setpriv_path or setpriv_entry.sha256 != setpriv_sha256:
        raise ExactReleaseFrontendBuildError("runtime closure setpriv entry does not match the fixed tool policy")
    return VerifiedRuntimeClosure(manifest_path=manifest, manifest_sha256=expected, entries=tuple(entries))


def _runtime_closure_receipt(closure: VerifiedRuntimeClosure) -> dict[str, str]:
    by_target = {entry.target_path: entry.sha256 for entry in closure.entries}
    return {
        "manifest_sha256": closure.manifest_sha256,
        "setpriv_sha256": by_target["/tool/setpriv"],
        "sh_sha256": by_target["/tool/sh"],
        "env_sha256": by_target["/usr/bin/env"],
    }


def _preflight_sandbox(
    sandbox: VerifiedSandbox,
    toolchain: VerifiedToolchain,
    runtime_closure: VerifiedRuntimeClosure,
    *,
    build_uid: int,
    tmpfs_bytes: int,
) -> None:
    """Require the exact combined isolation before any candidate is created."""

    script = _require_root_controlled_file(Path(__file__).resolve(strict=True), field="sandbox helper script")
    unshare = _require_pinned_root_tool_unchanged(
        sandbox.unshare_path,
        sandbox.unshare_sha256,
        field="sandbox preflight unshare tool",
    )
    python = _require_pinned_root_tool_unchanged(
        sandbox.python_path,
        sandbox.python_sha256,
        field="sandbox preflight python tool",
    )
    _run(
        [
            str(unshare),
            "--mount",
            "--net",
            "--pid",
            "--fork",
            "--kill-child=SIGKILL",
            "--mount-proc",
            "--",
            str(python),
            str(script),
            "_sandbox-probe",
            "--setpriv",
            str(sandbox.setpriv_path),
            "--setpriv-sha256",
            sandbox.setpriv_sha256,
            "--mount",
            str(sandbox.mount_path),
            "--mount-sha256",
            sandbox.mount_sha256,
            "--node",
            str(toolchain.node_path),
            "--node-sha256",
            toolchain.node_sha256,
            "--node-version",
            toolchain.node_version,
            "--npm-runtime-root",
            str(toolchain.npm_runtime_root),
            "--npm-runtime-tree-sha256",
            toolchain.npm_runtime_tree_sha256,
            "--npm-cli-sha256",
            toolchain.npm_sha256,
            "--npm-version",
            toolchain.npm_version,
            "--runtime-closure-manifest",
            str(runtime_closure.manifest_path),
            "--runtime-closure-manifest-sha256",
            runtime_closure.manifest_sha256,
            "--build-uid",
            str(build_uid),
            "--tmpfs-bytes",
            str(tmpfs_bytes),
        ],
        field="mandatory mount/network/PID/privilege sandbox preflight",
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL,
    )


def _copy_file_exclusive(*, source: Path, target: Path, mode: int, field: str) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        source_descriptor = os.open(source, flags)
    except OSError as exc:
        raise ExactReleaseFrontendBuildError(f"cannot safely open {field}") from exc
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_FILE_BYTES:
            raise ExactReleaseFrontendBuildError(f"{field} is unsafe")
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            target_descriptor = os.open(target, output_flags, mode)
        except OSError as exc:
            raise ExactReleaseFrontendBuildError(f"cannot copy {field}") from exc
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(target_descriptor, "wb", closefd=False) as output:
                while chunk := os.read(source_descriptor, 1024 * 1024):
                    total += len(chunk)
                    if total > before.st_size or total > MAX_FILE_BYTES:
                        raise ExactReleaseFrontendBuildError(f"{field} exceeds its bound while copying")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(source_descriptor)
            identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if total != before.st_size or any(getattr(before, item) != getattr(after, item) for item in identity):
                raise ExactReleaseFrontendBuildError(f"{field} changed while copying")
            return digest.hexdigest(), total
        finally:
            os.close(target_descriptor)
    finally:
        os.close(source_descriptor)


def _write_sandbox_control(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    with _new_private_file(path, field="sandbox control") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _run_sandboxed_build(
    *,
    candidate_directory: Path,
    source_directory: Path,
    offline_cache: Path,
    output_directory: Path,
    toolchain: VerifiedToolchain,
    sandbox: VerifiedSandbox,
    runtime_closure: VerifiedRuntimeClosure,
    sandbox_tmpfs_bytes: int,
    build_uid: int,
    build_environment: Mapping[str, str],
) -> str:
    """Run npm in a new mount/net/PID namespace, or fail without a fallback."""

    sandbox_root = _create_new_root_only_directory(
        candidate_directory / SANDBOX_ROOT_DIRECTORY_NAME, field="sandbox root directory"
    )
    _create_new_root_only_directory(output_directory, field="static build output directory")
    control = {
        "schema": SANDBOX_CONTROL_SCHEMA,
        "sandbox_root": str(sandbox_root),
        "source_directory": str(source_directory),
        "offline_cache": str(offline_cache),
        "output_directory": str(output_directory),
        "node_path": str(toolchain.node_path),
        "node_sha256": toolchain.node_sha256,
        "npm_runtime_root": str(toolchain.npm_runtime_root),
        "npm_runtime_tree_sha256": toolchain.npm_runtime_tree_sha256,
        "npm_cli_sha256": toolchain.npm_sha256,
        "runtime_closure_manifest_path": str(runtime_closure.manifest_path),
        "runtime_closure_manifest_sha256": runtime_closure.manifest_sha256,
        "setpriv_path": str(sandbox.setpriv_path),
        "setpriv_sha256": sandbox.setpriv_sha256,
        "mount_path": str(sandbox.mount_path),
        "mount_sha256": sandbox.mount_sha256,
        "tmpfs_bytes": sandbox_tmpfs_bytes,
        "build_uid": build_uid,
    }
    control_path = candidate_directory / SANDBOX_CONTROL_NAME
    _write_sandbox_control(control_path, control)
    script = _require_root_controlled_file(Path(__file__).resolve(strict=True), field="sandbox helper script")
    # The preflight may have completed well before the actual namespace is
    # entered.  Rehash the only two host executables run as root here instead
    # of treating the earlier probe as a persistent authorization.
    unshare = _require_pinned_root_tool_unchanged(
        sandbox.unshare_path,
        sandbox.unshare_sha256,
        field="sandbox build unshare tool",
    )
    python = _require_pinned_root_tool_unchanged(
        sandbox.python_path,
        sandbox.python_sha256,
        field="sandbox build python tool",
    )
    _run(
        [
            str(unshare),
            "--mount",
            "--net",
            "--pid",
            "--fork",
            "--kill-child=SIGKILL",
            "--mount-proc",
            "--",
            str(python),
            str(script),
            "_sandbox-child",
            "--control",
            str(control_path),
        ],
        field="isolated offline frontend static build",
        env=_sandbox_launch_environment(build_environment),
        stdout=subprocess.DEVNULL,
    )
    return sha256_bytes(canonical_json_bytes(_sandbox_environment(build_environment)))


def _reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExactReleaseFrontendBuildError("canonical JSON input contains duplicate keys")
        result[key] = value
    return result


def _read_sandbox_control(path: Path) -> dict[str, Any]:
    control_path = _require_private_file(path, field="sandbox control", maximum_bytes=64 * 1024)
    try:
        raw = control_path.read_bytes()
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_reject_duplicate_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExactReleaseFrontendBuildError("sandbox control is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise ExactReleaseFrontendBuildError("sandbox control must be canonical JSON")
    required = {
        "schema",
        "sandbox_root",
        "source_directory",
        "offline_cache",
        "output_directory",
        "node_path",
        "node_sha256",
        "npm_runtime_root",
        "npm_runtime_tree_sha256",
        "npm_cli_sha256",
        "runtime_closure_manifest_path",
        "runtime_closure_manifest_sha256",
        "setpriv_path",
        "setpriv_sha256",
        "mount_path",
        "mount_sha256",
        "tmpfs_bytes",
        "build_uid",
    }
    if set(value) != required or value.get("schema") != SANDBOX_CONTROL_SCHEMA:
        raise ExactReleaseFrontendBuildError("sandbox control fields differ")
    candidate = _require_root_directory(control_path.parent, field="sandbox candidate", private=True)
    expected_paths = {
        "sandbox_root": candidate / SANDBOX_ROOT_DIRECTORY_NAME,
        "source_directory": candidate / SOURCE_DIRECTORY_NAME,
        "offline_cache": candidate / OFFLINE_CACHE_DIRECTORY_NAME,
        "output_directory": candidate / OUTPUT_DIRECTORY_NAME,
    }
    for key, expected in expected_paths.items():
        if value.get(key) != str(expected):
            raise ExactReleaseFrontendBuildError("sandbox control path is not a fixed candidate child")
        _require_root_directory(expected, field=f"sandbox {key}", private=True)
    for key in (
        "node_sha256",
        "npm_runtime_tree_sha256",
        "npm_cli_sha256",
        "runtime_closure_manifest_sha256",
        "setpriv_sha256",
        "mount_sha256",
    ):
        _require_sha256(value.get(key), field=f"sandbox control {key}")
    tmpfs_bytes = value.get("tmpfs_bytes")
    build_uid = value.get("build_uid")
    if (
        isinstance(tmpfs_bytes, bool)
        or not isinstance(tmpfs_bytes, int)
        or not MIN_SANDBOX_TMPFS_BYTES <= tmpfs_bytes <= MAX_SANDBOX_TMPFS_BYTES
        or isinstance(build_uid, bool)
        or not isinstance(build_uid, int)
        or not 1 <= build_uid <= 2**31 - 1
    ):
        raise ExactReleaseFrontendBuildError("sandbox control resource policy is invalid")
    node = _require_root_tool(Path(value["node_path"]), field="sandbox node tool")
    setpriv = _require_root_tool(Path(value["setpriv_path"]), field="sandbox privilege-drop tool")
    mount = _require_root_tool(Path(value["mount_path"]), field="sandbox mount tool")
    runtime_root = _require_root_directory(Path(value["npm_runtime_root"]), field="sandbox npm runtime root", private=False)
    runtime_files = _root_owned_tree_files(
        runtime_root,
        field="sandbox npm runtime root",
        maximum_files=MAX_NPM_RUNTIME_FILES,
        maximum_bytes=MAX_NPM_RUNTIME_BYTES,
    )
    if (
        sha256_file(node)[0] != value["node_sha256"]
        or sha256_file(setpriv)[0] != value["setpriv_sha256"]
        or sha256_file(mount)[0] != value["mount_sha256"]
        or sha256_bytes(canonical_json_bytes(runtime_files)) != value["npm_runtime_tree_sha256"]
    ):
        raise ExactReleaseFrontendBuildError("sandbox tool material changed before entry")
    npm_cli = runtime_root / "bin" / "npm-cli.js"
    if _require_root_tool(npm_cli, field="sandbox npm CLI").as_posix() != npm_cli.as_posix() or sha256_file(npm_cli)[0] != value["npm_cli_sha256"]:
        raise ExactReleaseFrontendBuildError("sandbox npm CLI changed before entry")
    runtime_closure = _load_runtime_closure(
        manifest_path=Path(value["runtime_closure_manifest_path"]),
        expected_sha256=value["runtime_closure_manifest_sha256"],
        setpriv_path=setpriv,
        setpriv_sha256=value["setpriv_sha256"],
    )
    return {
        "candidate": candidate,
        "sandbox_root": expected_paths["sandbox_root"],
        "source_directory": expected_paths["source_directory"],
        "offline_cache": expected_paths["offline_cache"],
        "output_directory": expected_paths["output_directory"],
        "node": node,
        "npm_runtime_root": runtime_root,
        "npm_runtime_files": runtime_files,
        "runtime_closure": runtime_closure.entries,
        "setpriv": setpriv,
        "mount": mount,
        "tmpfs_bytes": tmpfs_bytes,
        "build_uid": build_uid,
    }


def _sandbox_command_environment() -> dict[str, str]:
    return {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}


def _sandbox_mkdir(path: Path, *, mode: int = 0o755) -> None:
    try:
        path.mkdir(mode=mode)
    except FileExistsError as exc:
        raise ExactReleaseFrontendBuildError("sandbox mount layout is unexpectedly occupied") from exc
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot create sandbox mount layout") from exc


def _sandbox_new_file(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot create sandbox mount target") from exc
    os.close(descriptor)


def _sandbox_mount(mount: Path, argv: Sequence[str], *, field: str) -> None:
    _run([str(mount), *argv], field=field, env=_sandbox_command_environment(), stdout=subprocess.DEVNULL)


def _sandbox_bind(mount: Path, source: Path, target: Path, *, read_only: bool, field: str) -> None:
    _sandbox_mount(mount, ["--bind", str(source), str(target)], field=field)
    if read_only:
        _sandbox_mount(
            mount,
            ["-o", "remount,bind,ro,nosuid,nodev", str(target)],
            field=f"{field} read-only remount",
        )


def _sandbox_ensure_directory(path: Path) -> None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        _sandbox_mkdir(path)
        return
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot inspect sandbox mount parent") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise ExactReleaseFrontendBuildError("sandbox mount parent is unsafe")


def _sandbox_prepare_file_target(root: Path, target_path: str) -> Path:
    relative = PurePosixPath(target_path.lstrip("/"))
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        _sandbox_ensure_directory(current)
    target = root / relative
    _sandbox_new_file(target)
    return target


def _sandbox_bind_runtime_closure(
    *, mount: Path, sandbox_root: Path, entries: Sequence[RuntimeClosureEntry], field: str
) -> None:
    for entry in entries:
        # Re-hash every file immediately before its bind.  The manifest is
        # root-only input, but it must not authorize a changed host runtime.
        if (
            _require_root_controlled_file(entry.host_path, field=f"{field} source") != entry.host_path
            or sha256_file(entry.host_path)[0] != entry.sha256
        ):
            raise ExactReleaseFrontendBuildError("sandbox runtime closure changed before its individual mount")
        target = _sandbox_prepare_file_target(sandbox_root, entry.target_path)
        _sandbox_bind(
            mount,
            entry.host_path,
            target,
            read_only=True,
            field=f"{field} {entry.target_path}",
        )


def _sandbox_bind_npm_runtime(
    *, mount: Path, sandbox_root: Path, runtime_root: Path, files: Sequence[Mapping[str, Any]]
) -> None:
    for item in files:
        relative = _safe_relative_path(str(item["path"]).encode("ascii"), field="sandbox npm runtime")
        source = runtime_root / relative
        expected_sha = _require_sha256(item.get("sha256"), field="sandbox npm runtime SHA-256")
        digest, bytes_value = sha256_file(_require_root_controlled_file(source, field="sandbox npm runtime file"))
        if digest != expected_sha or bytes_value != item.get("bytes"):
            raise ExactReleaseFrontendBuildError("sandbox npm runtime changed before its individual mount")
        target = _sandbox_prepare_file_target(sandbox_root, f"/tool/npm/{relative}")
        _sandbox_bind(mount, source, target, read_only=True, field=f"sandbox npm runtime mount {relative}")


def _sandbox_prepare_mounts(control: Mapping[str, Any]) -> Path:
    mount = control["mount"]
    if not isinstance(mount, Path):  # pragma: no cover - internal control invariant.
        raise ExactReleaseFrontendBuildError("sandbox mount tool is invalid")
    sandbox_root = control["sandbox_root"]
    if not isinstance(sandbox_root, Path):  # pragma: no cover - internal control invariant.
        raise ExactReleaseFrontendBuildError("sandbox root is invalid")
    _sandbox_mount(mount, ["--make-rprivate", "/"], field="sandbox mount propagation")
    _sandbox_mount(
        mount,
        ["-t", "tmpfs", "-o", f"mode=0755,nosuid,nodev,size={control['tmpfs_bytes']}", "tmpfs", str(sandbox_root)],
        field="sandbox bounded tmpfs",
    )
    for relative in ("input", "tool", "handoff-output", "scratch", "dev", "proc"):
        _sandbox_mkdir(sandbox_root / relative)
    _sandbox_mkdir(sandbox_root / "input" / "source")
    _sandbox_mkdir(sandbox_root / "input" / "cache")
    _sandbox_bind(
        mount,
        control["source_directory"],
        sandbox_root / "input" / "source",
        read_only=True,
        field="sandbox verified source mount",
    )
    _sandbox_bind(
        mount,
        control["offline_cache"],
        sandbox_root / "input" / "cache",
        read_only=True,
        field="sandbox trusted offline cache mount",
    )
    _sandbox_new_file(sandbox_root / "tool" / "node")
    _sandbox_bind(
        mount,
        control["node"],
        sandbox_root / "tool" / "node",
        read_only=True,
        field="sandbox pinned node mount",
    )
    _sandbox_bind_npm_runtime(
        mount=mount,
        sandbox_root=sandbox_root,
        runtime_root=control["npm_runtime_root"],
        files=control["npm_runtime_files"],
    )
    _sandbox_bind_runtime_closure(
        mount=mount,
        sandbox_root=sandbox_root,
        entries=control["runtime_closure"],
        field="sandbox runtime closure mount",
    )
    _sandbox_bind(
        mount,
        control["output_directory"],
        sandbox_root / "handoff-output",
        read_only=False,
        field="sandbox output handoff mount",
    )
    for source_text in ("/dev/null", *FIXED_READ_ONLY_DEVICE_FILES):
        target = sandbox_root / source_text.lstrip("/")
        _sandbox_new_file(target)
        _sandbox_bind(
            mount,
            Path(source_text),
            target,
            read_only=source_text != "/dev/null",
            field=f"sandbox device mount {source_text}",
        )
    _sandbox_mount(mount, ["-t", "proc", "proc", str(sandbox_root / "proc")], field="sandbox proc mount")
    return sandbox_root


def _sandbox_copy_tree(
    *, source: Path, target: Path, build_uid: int, writable: bool, field: str
) -> None:
    source_state = source.lstat()
    if stat.S_ISLNK(source_state.st_mode) or not stat.S_ISDIR(source_state.st_mode) or source_state.st_uid != 0:
        raise ExactReleaseFrontendBuildError(f"{field} source tree is unsafe")
    _sandbox_mkdir(target, mode=0o700 if writable else 0o755)
    os.chown(target, build_uid, build_uid)
    for current, directories, names in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(source)
        target_current = target / relative_current
        directories.sort()
        names.sort()
        for directory in directories:
            source_directory = current_path / directory
            state = source_directory.lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != 0
                or stat.S_IMODE(state.st_mode) & 0o022
            ):
                raise ExactReleaseFrontendBuildError(f"{field} source tree contains an unsafe directory")
            target_directory = target_current / directory
            _sandbox_mkdir(target_directory, mode=0o700 if writable else 0o555)
            os.chown(target_directory, build_uid, build_uid)
        for name in names:
            source_file = current_path / name
            state = source_file.lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISREG(state.st_mode)
                or state.st_uid != 0
                or state.st_nlink != 1
                or stat.S_IMODE(state.st_mode) & 0o022
                or state.st_size > MAX_FILE_BYTES
            ):
                raise ExactReleaseFrontendBuildError(f"{field} source tree contains an unsafe file")
            target_file = target_current / name
            _copy_file_exclusive(
                source=source_file,
                target=target_file,
                mode=0o600 if writable else (0o500 if state.st_mode & stat.S_IXUSR else 0o400),
                field=f"{field} source file",
            )
            os.chown(target_file, build_uid, build_uid)


def _sandbox_prepare_build_tree(*, build_uid: int) -> None:
    source = Path("/input/source")
    cache = Path("/input/cache")
    scratch = Path("/scratch")
    _sandbox_mkdir(scratch / "home", mode=0o700)
    os.chown(scratch / "home", build_uid, build_uid)
    _sandbox_mkdir(scratch / "tmp", mode=0o700)
    os.chown(scratch / "tmp", build_uid, build_uid)
    _sandbox_copy_tree(
        source=source,
        target=scratch / "source",
        build_uid=build_uid,
        writable=False,
        field="verified release source",
    )
    _sandbox_copy_tree(
        source=cache,
        target=scratch / "cache",
        build_uid=build_uid,
        writable=True,
        field="trusted offline npm cache",
    )
    frontend = scratch / "source" / "frontend"
    if not frontend.is_dir():
        raise ExactReleaseFrontendBuildError("exact release does not contain frontend source")
    for generated in (frontend / "node_modules", scratch / "source" / STATIC_OUTPUT_RELATIVE):
        if generated.exists():
            raise ExactReleaseFrontendBuildError("exact release unexpectedly contains generated frontend output")
        _sandbox_mkdir(generated, mode=0o700)
        os.chown(generated, build_uid, build_uid)
    # npm may replace node_modules and Vite empties its fixed output directory.
    os.chmod(scratch / "source", 0o755)
    os.chmod(frontend, 0o755)


def _sandbox_drop_prefix(setpriv: Path, build_uid: int) -> list[str]:
    return [
        str(setpriv),
        "--reuid",
        str(build_uid),
        "--regid",
        str(build_uid),
        "--clear-groups",
        "--no-new-privs",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--bounding-set=-all",
        "--",
    ]


def _sandbox_run_npm(*, control: Mapping[str, Any], build_environment: Mapping[str, str]) -> None:
    setpriv = control["setpriv"]
    build_uid = control["build_uid"]
    if not isinstance(setpriv, Path) or not isinstance(build_uid, int):  # pragma: no cover - internal invariant.
        raise ExactReleaseFrontendBuildError("sandbox privilege policy is invalid")
    environment = _sandbox_environment(build_environment)
    common = _sandbox_drop_prefix(Path("/tool/setpriv"), build_uid) + ["/tool/node", "/tool/npm/bin/npm-cli.js"]
    cwd = Path("/scratch/source/frontend")
    _run(
        # Vite and its plugins are build-time devDependencies.  Keep the
        # production build environment while explicitly retaining them so npm
        # cannot infer ``omit=dev`` from NODE_ENV=production.
        [
            *common,
            "ci",
            "--offline",
            "--include=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        field="offline npm dependency installation",
        cwd=cwd,
        env=environment,
    )
    _run(
        [*common, "run", "build", "--ignore-scripts"],
        field="offline npm static build",
        cwd=cwd,
        env=environment,
    )


def _scan_build_uid_tree(path: Path, *, build_uid: int) -> list[dict[str, Any]]:
    try:
        root = path.lstat()
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("sandbox static output is unavailable") from exc
    if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode) or root.st_uid != build_uid:
        raise ExactReleaseFrontendBuildError("sandbox static output root is unsafe")
    files: list[dict[str, Any]] = []
    total = 0
    for current, directories, names in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        names.sort()
        for directory in directories:
            state = (current_path / directory).lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISDIR(state.st_mode)
                or state.st_uid != build_uid
                or stat.S_IMODE(state.st_mode) & 0o022
            ):
                raise ExactReleaseFrontendBuildError("sandbox static output contains an unsafe directory")
        for name in names:
            candidate = current_path / name
            state = candidate.lstat()
            if (
                stat.S_ISLNK(state.st_mode)
                or not stat.S_ISREG(state.st_mode)
                or state.st_uid != build_uid
                or state.st_nlink != 1
                or stat.S_IMODE(state.st_mode) & 0o022
                or state.st_size > MAX_FILE_BYTES
            ):
                raise ExactReleaseFrontendBuildError("sandbox static output contains an unsafe file")
            relative = _safe_relative_path(candidate.relative_to(path).as_posix().encode("ascii"), field="sandbox static output")
            digest, bytes_value = sha256_file(candidate)
            total += bytes_value
            if len(files) >= MAX_OUTPUT_FILES or total > MAX_OUTPUT_BYTES:
                raise ExactReleaseFrontendBuildError("sandbox static output exceeds its bounds")
            files.append({"path": relative, "sha256": digest, "bytes": bytes_value})
    if not files:
        raise ExactReleaseFrontendBuildError("sandbox static output must contain files")
    files.sort(key=lambda item: str(item["path"]))
    return files


def _sandbox_copy_static_output(*, build_uid: int) -> None:
    source = Path("/scratch/source") / STATIC_OUTPUT_RELATIVE
    output = Path("/handoff-output")
    files = _scan_build_uid_tree(source, build_uid=build_uid)
    for item in files:
        relative = str(item["path"])
        parent = PurePosixPath(relative).parent
        if parent != PurePosixPath("."):
            _mkdir_private_child(output, parent.as_posix())
        digest, bytes_value = _copy_file_exclusive(
            source=source / relative,
            target=output / relative,
            mode=0o600,
            field="sandbox static output file",
        )
        if digest != item["sha256"] or bytes_value != item["bytes"]:
            raise ExactReleaseFrontendBuildError("sandbox static output changed during handoff")


def _reap_sandbox_children() -> None:
    """Reap exited descendants while this process is the sandbox PID 1."""

    while True:
        try:
            child, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        except OSError as exc:
            raise ExactReleaseFrontendBuildError("cannot reap sandbox child processes") from exc
        if child == 0:
            return


def _remaining_sandbox_pids() -> set[int]:
    """List every live process except this namespace's trusted PID 1."""

    own_pid = os.getpid()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("cannot inspect sandbox process namespace") from exc
    result: set[int] = set()
    for entry in entries:
        if not entry.name.isascii() or not entry.name.isdecimal():
            continue
        try:
            pid = int(entry.name, 10)
        except ValueError:  # pragma: no cover - guarded by isdecimal.
            continue
        if pid > 0 and pid != own_pid:
            result.add(pid)
    return result


def _require_quiescent_sandbox_before_output_handoff() -> None:
    """Kill and reap all build descendants before root reads their output.

    A malicious build can background a process after npm returns.  The outer
    sandbox process is PID 1 of a dedicated PID namespace, so it can enumerate
    and kill every remaining descendant without touching host processes.  Two
    consecutive empty scans make the subsequent root-owned output copy free of
    a concurrent untrusted writer.
    """

    empty_scans = 0
    for _attempt in range(MAX_SANDBOX_QUIESCENCE_PASSES):
        _reap_sandbox_children()
        remaining = _remaining_sandbox_pids()
        if not remaining:
            empty_scans += 1
            if empty_scans >= 2:
                return
            time.sleep(0.01)
            continue
        empty_scans = 0
        for pid in sorted(remaining):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except PermissionError as exc:  # pragma: no cover - PID namespace invariant.
                raise ExactReleaseFrontendBuildError("cannot terminate sandbox build descendant") from exc
            except OSError as exc:
                raise ExactReleaseFrontendBuildError("cannot terminate sandbox build descendant") from exc
        time.sleep(0.01)
    raise ExactReleaseFrontendBuildError("sandbox build descendants did not quiesce before output handoff")


def _apply_build_rlimits() -> None:
    """Constrain the dropped-UID build process tree beyond namespace isolation."""

    limits = (
        (resource.RLIMIT_NPROC, MAX_BUILD_PROCESSES),
        (resource.RLIMIT_AS, MAX_BUILD_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_CPU, MAX_BUILD_CPU_SECONDS),
        (resource.RLIMIT_FSIZE, MAX_OUTPUT_BYTES),
    )
    try:
        for resource_id, bound in limits:
            resource.setrlimit(resource_id, (bound, bound))
    except (OSError, ValueError) as exc:
        raise ExactReleaseFrontendBuildError("sandbox resource-limit policy cannot be applied") from exc


def _sandbox_child_main(control_path: Path) -> int:
    _require_root_execution()
    control = _read_sandbox_control(control_path)
    sandbox_root = _sandbox_prepare_mounts(control)
    try:
        os.chdir(sandbox_root)
        os.chroot(sandbox_root)
        os.chdir("/")
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("sandbox chroot failed") from exc
    _apply_build_rlimits()
    build_environment = _validate_build_environment(
        {key: os.environ[key] for key in ALLOWED_BUILD_ENVIRONMENT_KEYS if key in os.environ}
    )
    _sandbox_prepare_build_tree(build_uid=control["build_uid"])
    _sandbox_run_npm(control=control, build_environment=build_environment)
    _require_quiescent_sandbox_before_output_handoff()
    _sandbox_copy_static_output(build_uid=control["build_uid"])
    return 0


def _sandbox_probe_layout(
    *,
    root: Path,
    mount: Path,
    node: Path,
    runtime_root: Path,
    runtime_files: Sequence[Mapping[str, Any]],
    closure: VerifiedRuntimeClosure,
    tmpfs_bytes: int,
) -> None:
    """Create the same file-by-file chroot tool layout used by a real build."""

    _sandbox_mount(mount, ["--make-rprivate", "/"], field="sandbox probe mount propagation")
    _sandbox_mount(
        mount,
        ["-t", "tmpfs", "-o", f"mode=0755,nosuid,nodev,size={tmpfs_bytes}", "tmpfs", str(root)],
        field="sandbox probe bounded tmpfs",
    )
    for relative in ("tool", "scratch", "dev", "proc"):
        _sandbox_mkdir(root / relative)
    _sandbox_new_file(root / "tool" / "node")
    _sandbox_bind(mount, node, root / "tool" / "node", read_only=True, field="sandbox probe pinned node mount")
    _sandbox_bind_npm_runtime(
        mount=mount,
        sandbox_root=root,
        runtime_root=runtime_root,
        files=runtime_files,
    )
    _sandbox_bind_runtime_closure(
        mount=mount,
        sandbox_root=root,
        entries=closure.entries,
        field="sandbox probe runtime closure mount",
    )
    for source_text in ("/dev/null", *FIXED_READ_ONLY_DEVICE_FILES):
        target = root / source_text.lstrip("/")
        _sandbox_prepare_file_target(root, source_text)
        _sandbox_bind(
            mount,
            Path(source_text),
            target,
            read_only=source_text != "/dev/null",
            field=f"sandbox probe device mount {source_text}",
        )
    _sandbox_mount(mount, ["-t", "proc", "proc", str(root / "proc")], field="sandbox probe proc mount")


def _sandbox_probe_main(
    *,
    setpriv: Path,
    setpriv_sha256: str,
    mount: Path,
    mount_sha256: str,
    node: Path,
    node_sha256: str,
    node_version: str,
    npm_runtime_root: Path,
    npm_runtime_tree_sha256: str,
    npm_cli_sha256: str,
    npm_version: str,
    runtime_closure_manifest: Path,
    runtime_closure_manifest_sha256: str,
    build_uid: int,
    tmpfs_bytes: int,
) -> int:
    """Run pinned node/npm only after full file-by-file chroot and UID drop."""

    _require_root_execution()
    setpriv = _require_root_tool(setpriv, field="sandbox probe privilege-drop tool")
    mount = _require_root_tool(mount, field="sandbox probe mount tool")
    node = _require_root_tool(node, field="sandbox probe node tool")
    if (
        sha256_file(setpriv)[0] != _require_sha256(setpriv_sha256, field="sandbox probe setpriv SHA-256")
        or sha256_file(mount)[0] != _require_sha256(mount_sha256, field="sandbox probe mount SHA-256")
        or sha256_file(node)[0] != _require_sha256(node_sha256, field="sandbox probe node SHA-256")
    ):
        raise ExactReleaseFrontendBuildError("sandbox probe fixed tool changed before entry")
    node_version = _require_simple_version(node_version, field="sandbox probe node version")
    npm_version = _require_simple_version(npm_version, field="sandbox probe npm version")
    runtime_root = _require_root_directory(npm_runtime_root, field="sandbox probe npm runtime root", private=False)
    runtime_files = _root_owned_tree_files(
        runtime_root,
        field="sandbox probe npm runtime root",
        maximum_files=MAX_NPM_RUNTIME_FILES,
        maximum_bytes=MAX_NPM_RUNTIME_BYTES,
    )
    if sha256_bytes(canonical_json_bytes(runtime_files)) != _require_sha256(
        npm_runtime_tree_sha256, field="sandbox probe npm runtime tree SHA-256"
    ):
        raise ExactReleaseFrontendBuildError("sandbox probe npm runtime changed before entry")
    npm_cli = runtime_root / "bin" / "npm-cli.js"
    if sha256_file(_require_root_tool(npm_cli, field="sandbox probe npm CLI"))[0] != _require_sha256(
        npm_cli_sha256, field="sandbox probe npm CLI SHA-256"
    ):
        raise ExactReleaseFrontendBuildError("sandbox probe npm CLI changed before entry")
    closure = _load_runtime_closure(
        manifest_path=runtime_closure_manifest,
        expected_sha256=runtime_closure_manifest_sha256,
        setpriv_path=setpriv,
        setpriv_sha256=setpriv_sha256,
    )
    root = Path("/run")
    _sandbox_probe_layout(
        root=root,
        mount=mount,
        node=node,
        runtime_root=runtime_root,
        runtime_files=runtime_files,
        closure=closure,
        tmpfs_bytes=tmpfs_bytes,
    )
    try:
        os.chdir(root)
        os.chroot(root)
        os.chdir("/")
    except OSError as exc:
        raise ExactReleaseFrontendBuildError("sandbox probe chroot failed") from exc
    _apply_build_rlimits()
    for relative in ("/scratch/home", "/scratch/tmp"):
        _sandbox_mkdir(Path(relative), mode=0o700)
        os.chown(relative, build_uid, build_uid)
    environment = _sandbox_environment({})
    node_result = _run(
        [*_sandbox_drop_prefix(Path("/tool/setpriv"), build_uid), "/tool/node", "--version"],
        field="sandbox dropped-UID node version",
        env=environment,
    )
    npm_result = _run(
        [
            *_sandbox_drop_prefix(Path("/tool/setpriv"), build_uid),
            "/tool/node",
            "/tool/npm/bin/npm-cli.js",
            "--version",
        ],
        field="sandbox dropped-UID npm version",
        env=environment,
    )
    try:
        actual_node = _extract_simple_version(
            node_result.stdout.decode("ascii").strip(), field="sandbox dropped-UID node version"
        )
        actual_npm = _extract_simple_version(
            npm_result.stdout.decode("ascii").strip(), field="sandbox dropped-UID npm version"
        )
    except UnicodeDecodeError as exc:
        raise ExactReleaseFrontendBuildError("sandbox tool version output is not ASCII") from exc
    if actual_node != node_version or actual_npm != npm_version:
        raise ExactReleaseFrontendBuildError("sandbox dropped-UID tool version does not match the fixed policy")
    return 0


def _scan_regular_tree(path: Path, *, field: str, maximum_files: int, maximum_bytes: int) -> dict[str, Any]:
    path = _require_root_directory(path, field=field, private=True)
    files: list[dict[str, Any]] = []
    total = 0
    for current, directories, names in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        directories.sort()
        names.sort()
        for directory in directories:
            metadata = (current_path / directory).lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe directory")
        for name in names:
            candidate = current_path / name
            metadata = candidate.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != 0
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or metadata.st_size > MAX_FILE_BYTES
            ):
                raise ExactReleaseFrontendBuildError(f"{field} contains an unsafe file")
            relative = _safe_relative_path(candidate.relative_to(path).as_posix().encode("ascii"), field=field)
            digest, bytes_value = sha256_file(candidate)
            total += bytes_value
            if len(files) >= maximum_files or total > maximum_bytes:
                raise ExactReleaseFrontendBuildError(f"{field} exceeds its bounds")
            files.append({"path": relative, "sha256": digest, "bytes": bytes_value})
    if not files:
        raise ExactReleaseFrontendBuildError(f"{field} must contain files")
    files.sort(key=lambda item: str(item["path"]))
    return {
        "files_sha256": sha256_bytes(canonical_json_bytes(files)),
        "file_count": len(files),
        "bytes": total,
        "files": files,
    }


def _assert_receipt_is_public(value: object, *, field: str = "receipt") -> None:
    """Keep receipt evidence safe to hand to a different role.

    The build can receive one public Vite URL only in its transient process
    environment.  Receipt evidence records a hash of that projection, never
    the value.  This structural guard makes an accidental future field fail
    closed instead of turning a durable receipt into a capability carrier.
    """

    forbidden_key_parts = (
        "access_key",
        "authorization",
        "credential",
        "password",
        "private",
        "secret",
        "token",
        "url",
        "uri",
        "endpoint",
    )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or any(part in key.lower() for part in forbidden_key_parts):
                raise ExactReleaseFrontendBuildError(f"{field} contains a prohibited key")
            _assert_receipt_is_public(item, field=f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_receipt_is_public(item, field=f"{field}[{index}]")
        return
    if isinstance(value, (bool, int)) or value is None:
        return
    if not isinstance(value, str):
        raise ExactReleaseFrontendBuildError(f"{field} contains an invalid value")
    lowered = value.lower()
    if (
        "://" in lowered
        or "presigned" in lowered
        or "-----begin" in lowered
        or lowered.startswith(("sk-", "akia", "age-secret-"))
    ):
        raise ExactReleaseFrontendBuildError(f"{field} contains a URL or secret-shaped value")


def _write_receipt(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    authority = value.get("receipt_authority")
    if (
        not isinstance(authority, Mapping)
        or set(authority) != {"unsigned", "provenance", "integration_status"}
        or authority.get("unsigned") is not True
        or authority.get("provenance") != LOCAL_RECEIPT_PROVENANCE
        or authority.get("integration_status") != LOCAL_RECEIPT_INTEGRATION_STATUS
    ):
        raise ExactReleaseFrontendBuildError("receipt must retain the unsigned local-only integration block")
    _assert_receipt_is_public(value)
    unsigned = dict(value)
    receipt_sha256 = sha256_bytes(canonical_json_bytes(unsigned))
    receipt = {**unsigned, "receipt_sha256": receipt_sha256}
    payload = canonical_json_bytes(receipt) + b"\n"
    with _new_private_file(path, field="exact-release frontend build receipt") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return receipt


def _path_digest(path: Path) -> str:
    return sha256_bytes(str(path).encode("utf-8"))


def prepare_exact_release_frontend_static_build(
    *,
    source_repository: Path,
    release_sha: str,
    candidate_directory: Path,
    offline_dependency_archive: Path,
    offline_dependency_archive_sha256: str,
    expected_package_lock_sha256: str,
    sandbox_tmpfs_bytes: int = 512 * 1024 * 1024,
    build_uid: int = DEFAULT_BUILD_UID,
    build_environment: Mapping[str, str] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Plan or prepare one exact-release candidate without transport side effects."""

    _require_root_execution()
    release_sha = _require_release(release_sha)
    expected_package_lock_sha256 = _require_sha256(
        expected_package_lock_sha256, field="expected_package_lock_sha256"
    )
    offline_dependency_archive_sha256 = _require_sha256(
        offline_dependency_archive_sha256, field="offline_dependency_archive_sha256"
    )
    validated_build_environment = _validate_build_environment(build_environment)
    fixed_policy = _load_fixed_tool_policy()
    git, git_sha256, git_version = _verify_sandbox_tool(
        path=fixed_policy.git_path,
        sha256=fixed_policy.git_sha256,
        version=fixed_policy.git_version,
        expected_name="git",
        field="fixed Git tool",
    )
    source_text, release_tree = _require_clean_exact_source(
        git=git, source_repository=source_repository, release_sha=release_sha
    )
    source = Path(source_text)
    candidate_directory = _require_absolute(candidate_directory, field="candidate_directory")
    _require_root_directory(candidate_directory.parent, field="candidate_directory parent", private=True)
    try:
        candidate_directory.relative_to(source)
    except ValueError:
        pass
    else:
        raise ExactReleaseFrontendBuildError("candidate_directory must not be below source_repository")
    tree = _git_tree(git, source, release_sha)
    verified_toolchain = _verify_toolchain(fixed_policy.toolchain)
    verified_sandbox = _verify_sandbox(
        pinned=fixed_policy.sandbox,
        tmpfs_bytes=sandbox_tmpfs_bytes,
        build_uid=build_uid,
    )
    runtime_closure = _load_runtime_closure(
        manifest_path=fixed_policy.runtime_closure_manifest_path,
        expected_sha256=fixed_policy.runtime_closure_manifest_sha256,
        setpriv_path=verified_sandbox.setpriv_path,
        setpriv_sha256=verified_sandbox.setpriv_sha256,
    )
    offline_archive = _require_private_file(
        offline_dependency_archive,
        field="offline dependency archive",
        maximum_bytes=MAX_OFFLINE_CACHE_ARCHIVE_BYTES,
    )
    offline_sha256, offline_bytes = sha256_file(offline_archive)
    if offline_sha256 != offline_dependency_archive_sha256:
        raise ExactReleaseFrontendBuildError("offline dependency archive SHA-256 does not match its pin")

    lock_path = source / "frontend" / "package-lock.json"
    package_path = source / "frontend" / "package.json"
    if not lock_path.is_file() or not package_path.is_file():
        raise ExactReleaseFrontendBuildError("exact release must contain frontend package.json and package-lock.json")
    package_lock_sha256, package_lock_bytes = sha256_file(lock_path)
    package_sha256, package_bytes = sha256_file(package_path)
    if package_lock_sha256 != expected_package_lock_sha256:
        raise ExactReleaseFrontendBuildError("exact release package-lock SHA-256 does not match its pin")
    # This deliberately happens before ``candidate_directory`` exists.  An
    # unavailable namespace, mount, or privilege drop therefore leaves no
    # partial release/output candidate and never falls back to root npm.
    _preflight_sandbox(
        verified_sandbox,
        verified_toolchain,
        runtime_closure,
        build_uid=build_uid,
        tmpfs_bytes=sandbox_tmpfs_bytes,
    )

    plan = {
        "schema": SCHEMA,
        "status": "prepared" if apply else "planned",
        "release_sha": release_sha,
        "release_tree": release_tree,
        "source": {"tree_file_count": len(tree), "repository_path_sha256": _path_digest(source)},
        "toolchain": {
            "fixed_policy_sha256": fixed_policy.policy_sha256,
            "git": {"path_sha256": _path_digest(git), "sha256": git_sha256, "version": git_version},
            "node": {"path_sha256": _path_digest(verified_toolchain.node_path), "sha256": verified_toolchain.node_sha256, "version": verified_toolchain.node_version},
            "npm": {
                "path_sha256": _path_digest(verified_toolchain.npm_path),
                "sha256": verified_toolchain.npm_sha256,
                "version": verified_toolchain.npm_version,
                "runtime_path_sha256": _path_digest(verified_toolchain.npm_runtime_root),
                "runtime_tree_sha256": verified_toolchain.npm_runtime_tree_sha256,
            },
            "sandbox": {
                "python": {"path_sha256": _path_digest(verified_sandbox.python_path), "sha256": verified_sandbox.python_sha256, "version": verified_sandbox.python_version},
                "unshare": {"path_sha256": _path_digest(verified_sandbox.unshare_path), "sha256": verified_sandbox.unshare_sha256, "version": verified_sandbox.unshare_version},
                "setpriv": {"path_sha256": _path_digest(verified_sandbox.setpriv_path), "sha256": verified_sandbox.setpriv_sha256, "version": verified_sandbox.setpriv_version},
                "mount": {"path_sha256": _path_digest(verified_sandbox.mount_path), "sha256": verified_sandbox.mount_sha256, "version": verified_sandbox.mount_version},
                "policy_sha256": verified_sandbox.policy_sha256,
            },
        },
        "lock": {"package_json_sha256": package_sha256, "package_json_bytes": package_bytes, "package_lock_sha256": package_lock_sha256, "package_lock_bytes": package_lock_bytes},
        "offline_dependency_input": {"archive_sha256": offline_sha256, "archive_bytes": offline_bytes},
        "runtime_closure": _runtime_closure_receipt(runtime_closure),
        "build_environment_sha256": sha256_bytes(canonical_json_bytes(validated_build_environment)),
        "sandbox_preflight": {"mount_network_pid_namespace": "passed", "privilege_drop": "passed"},
        "network_action": False,
        "object_storage_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
        "current_changed": False,
        "receipt_authority": {
            "unsigned": True,
            "provenance": LOCAL_RECEIPT_PROVENANCE,
            "integration_status": LOCAL_RECEIPT_INTEGRATION_STATUS,
        },
        "transport_authority": {
            "local_receipt_only": True,
            "external_controller_signature_required": True,
            "transport_or_install_authorized": False,
        },
    }
    if not apply:
        return plan

    candidate = _create_new_root_only_directory(candidate_directory, field="candidate_directory")
    archive_path = candidate / RELEASE_ARCHIVE_NAME
    archive_sha256, archive_bytes = _write_release_archive(
        git=git, source_repository=source, release_sha=release_sha, target=archive_path
    )
    release_material = _verify_and_materialize_release_archive(
        archive_path=archive_path,
        source_directory=candidate / SOURCE_DIRECTORY_NAME,
        tree=tree,
        release_sha=release_sha,
    )
    if release_material["archive_sha256"] != archive_sha256 or release_material["archive_bytes"] != archive_bytes:
        raise ExactReleaseFrontendBuildError("verified release archive descriptor changed")
    # Archive extraction itself is the package/lock source of truth, so pin it
    # again after materialization rather than trusting the source worktree.
    materialized_package_sha256, _ = sha256_file(candidate / SOURCE_DIRECTORY_NAME / "frontend" / "package.json")
    materialized_lock_sha256, _ = sha256_file(candidate / SOURCE_DIRECTORY_NAME / "frontend" / "package-lock.json")
    if materialized_package_sha256 != package_sha256:
        raise ExactReleaseFrontendBuildError("materialized release package.json SHA-256 does not match the exact source")
    if materialized_lock_sha256 != expected_package_lock_sha256:
        raise ExactReleaseFrontendBuildError("materialized release package-lock SHA-256 does not match its pin")
    offline_material = _extract_offline_cache(
        archive_path=offline_archive,
        expected_sha256=offline_dependency_archive_sha256,
        candidate_directory=candidate,
    )
    environment_sha256 = _run_sandboxed_build(
        toolchain=verified_toolchain,
        source_directory=candidate / SOURCE_DIRECTORY_NAME,
        offline_cache=candidate / OFFLINE_CACHE_DIRECTORY_NAME,
        output_directory=candidate / OUTPUT_DIRECTORY_NAME,
        candidate_directory=candidate,
        sandbox=verified_sandbox,
        runtime_closure=runtime_closure,
        sandbox_tmpfs_bytes=sandbox_tmpfs_bytes,
        build_uid=build_uid,
        build_environment=validated_build_environment,
    )
    output = _scan_regular_tree(
        candidate / OUTPUT_DIRECTORY_NAME,
        field="static build output directory",
        maximum_files=MAX_OUTPUT_FILES,
        maximum_bytes=MAX_OUTPUT_BYTES,
    )
    # The source must still be an exact clean source when preparation returns.
    # A drift in the source worktree invalidates the candidate rather than being
    # ignored after the archive was made.
    _require_clean_exact_source(git=git, source_repository=source, release_sha=release_sha)
    receipt = _write_receipt(
        candidate / RECEIPT_NAME,
        {
            **plan,
            "status": "prepared",
            "release_archive": {"sha256": archive_sha256, "bytes": archive_bytes},
            "materialized_source": {
                "files_sha256": release_material["files_sha256"],
                "file_count": release_material["file_count"],
                "package_json_sha256": materialized_package_sha256,
                "package_lock_sha256": materialized_lock_sha256,
            },
            "offline_dependency_input": {**offline_material},
            "build": {
                "environment_sha256": environment_sha256,
                "lifecycle_scripts_enabled": False,
                "mount_namespace_required": True,
                "network_namespace_required": True,
                "pid_namespace_required": True,
                "privilege_drop_required": True,
                "rlimit_nproc": MAX_BUILD_PROCESSES,
                "rlimit_as_bytes": MAX_BUILD_ADDRESS_SPACE_BYTES,
                "rlimit_cpu_seconds": MAX_BUILD_CPU_SECONDS,
                "rlimit_fsize_bytes": MAX_OUTPUT_BYTES,
            },
            "output": output,
        },
    )
    return {**receipt, "candidate_directory": str(candidate)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare local-only frontend build evidence from one exact release. "
            "Executable pins come exclusively from the fixed root-only policy; "
            "the unsigned result cannot authorize transport or installation."
        )
    )
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--candidate-directory", type=Path, required=True)
    parser.add_argument("--offline-dependency-archive", type=Path, required=True)
    parser.add_argument("--offline-dependency-archive-sha256", required=True)
    parser.add_argument("--expected-package-lock-sha256", required=True)
    parser.add_argument("--sandbox-tmpfs-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--build-uid", type=int, default=DEFAULT_BUILD_UID)
    parser.add_argument("--apply", action="store_true")
    return parser


def _internal_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    if command == "_sandbox-child":
        parser.add_argument("--control", type=Path, required=True)
    elif command == "_sandbox-probe":
        parser.add_argument("--setpriv", type=Path, required=True)
        parser.add_argument("--setpriv-sha256", required=True)
        parser.add_argument("--mount", type=Path, required=True)
        parser.add_argument("--mount-sha256", required=True)
        parser.add_argument("--node", type=Path, required=True)
        parser.add_argument("--node-sha256", required=True)
        parser.add_argument("--node-version", required=True)
        parser.add_argument("--npm-runtime-root", type=Path, required=True)
        parser.add_argument("--npm-runtime-tree-sha256", required=True)
        parser.add_argument("--npm-cli-sha256", required=True)
        parser.add_argument("--npm-version", required=True)
        parser.add_argument("--runtime-closure-manifest", type=Path, required=True)
        parser.add_argument("--runtime-closure-manifest-sha256", required=True)
        parser.add_argument("--build-uid", type=int, required=True)
        parser.add_argument("--tmpfs-bytes", type=int, required=True)
    else:
        raise ExactReleaseFrontendBuildError("unknown internal sandbox command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {
        "_sandbox-child",
        "_sandbox-probe",
    }:
        command = arguments.pop(0)
        try:
            internal = _internal_parser(command).parse_args(arguments)
            if command == "_sandbox-child":
                return _sandbox_child_main(internal.control)
            if command == "_sandbox-probe":
                return _sandbox_probe_main(
                    setpriv=internal.setpriv,
                    setpriv_sha256=internal.setpriv_sha256,
                    mount=internal.mount,
                    mount_sha256=internal.mount_sha256,
                    node=internal.node,
                    node_sha256=internal.node_sha256,
                    node_version=internal.node_version,
                    npm_runtime_root=internal.npm_runtime_root,
                    npm_runtime_tree_sha256=internal.npm_runtime_tree_sha256,
                    npm_cli_sha256=internal.npm_cli_sha256,
                    npm_version=internal.npm_version,
                    runtime_closure_manifest=internal.runtime_closure_manifest,
                    runtime_closure_manifest_sha256=internal.runtime_closure_manifest_sha256,
                    build_uid=internal.build_uid,
                    tmpfs_bytes=internal.tmpfs_bytes,
                )
        except ExactReleaseFrontendBuildError as exc:
            print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
            return 2
    args = _parser().parse_args(arguments)
    try:
        result = prepare_exact_release_frontend_static_build(
            source_repository=args.source_repository,
            release_sha=args.release_sha,
            candidate_directory=args.candidate_directory,
            offline_dependency_archive=args.offline_dependency_archive,
            offline_dependency_archive_sha256=args.offline_dependency_archive_sha256,
            expected_package_lock_sha256=args.expected_package_lock_sha256,
            sandbox_tmpfs_bytes=args.sandbox_tmpfs_bytes,
            build_uid=args.build_uid,
            apply=args.apply,
        )
    except ExactReleaseFrontendBuildError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
