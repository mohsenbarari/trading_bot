"""Host-controller test runner: disposable source, empty credentials, no network.

Not an arbitrary shell gateway. Only explicit pytest selectors are accepted; the
caller has already bound its operation to an Assignment/fence and actor role.
"""
from __future__ import annotations

import hashlib
import os
import re
import selectors as io_selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


class SandboxError(ValueError):
    pass


SELECTOR = re.compile(r"tests/test[A-Za-z0-9_./-]*\.py(?:::[A-Za-z0-9_]+)*\Z")
EXCLUDED_ROOTS = {".git", ".local", ".venv", "venv", "node_modules", "logs", "log",
                  "tmp", "temp", "backups", "backup", "state", "sessions", "uploads", "media"}
SOURCE_SUFFIXES = {".py", ".pyi", ".md", ".json", ".yaml", ".yml", ".toml", ".ini",
                   ".cfg", ".txt", ".sql", ".sh", ".html", ".css", ".js", ".ts",
                   ".tsx", ".jsx", ".vue", ".csv", ".feature", ".example"}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_OUTPUT_BYTES = 8_000_000
MAX_ANCESTRY_COMMITS = 4096
MAX_ANCESTRY_BYTES = 8_000_000
READ_CHUNK = 64 * 1024
PROCESS_CLEANUP_GRACE = 2.0
DEADLINE_CLEANUP_RESERVE = 2 * PROCESS_CLEANUP_GRACE + 1.0
PRE_STOP_DEADLINE_SECONDS = 600
POST_DEADLINE_SECONDS = 2280
HOOK_TIMEOUT_MARGIN_SECONDS = 120
RECOVERY_DEADLINE_SECONDS = 600
MAX_TEST_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
MAX_TEST_PROCESSES = 128
MAX_TEST_TMP_BYTES = 512 * 1024 * 1024
MAX_TEST_FILE_BYTES = 128 * 1024 * 1024
MAX_TEST_OPEN_FILES = 512
MAX_TEST_CPU_SECONDS = 1200


@dataclass(frozen=True)
class GitAncestryEvidence:
    """Exact trusted Git boundary to reproduce without exposing the host object DB."""

    ancestor_sha: str
    head_sha: str


_ACTIVE_DEADLINE: ContextVar[float | None] = ContextVar(
    "two_site_agent_end_to_end_deadline", default=None
)


class EndToEndDeadlineExceeded(SandboxError):
    """The protected hook exhausted its shared wall-clock budget."""


def hook_deadline_seconds(phase: str) -> int:
    if phase in {"pre", "stop"}:
        return PRE_STOP_DEADLINE_SECONDS
    if phase == "post":
        return POST_DEADLINE_SECONDS
    raise SandboxError("unknown protected hook phase")


def hook_timeout_seconds(phase: str) -> int:
    """Return the outer Antigravity timeout derived from the enforced deadline."""

    return hook_deadline_seconds(phase) + HOOK_TIMEOUT_MARGIN_SECONDS


def _deadline_timeout(requested: float) -> float:
    """Cap one child process while preserving time to kill all descendants."""

    deadline = _ACTIVE_DEADLINE.get()
    if deadline is None:
        return requested
    remaining = deadline - time.monotonic() - DEADLINE_CLEANUP_RESERVE
    if remaining <= 0:
        raise EndToEndDeadlineExceeded("protected operation exhausted its end-to-end deadline")
    return min(requested, remaining)


@contextmanager
def operation_deadline(seconds: float):
    """Enforce one shared deadline across preparation, children, and cleanup.

    The protected hook is a dedicated main-thread process. Refusing a pre-existing
    interval timer keeps ownership unambiguous and fail-closed. Every bounded child
    also receives the remaining budget minus a cleanup reserve, while SIGALRM covers
    pure-Python preparation, recovery, observations, and filesystem cleanup.
    """

    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        raise SandboxError("end-to-end deadline must be positive")
    if threading.current_thread() is not threading.main_thread():
        raise SandboxError("end-to-end deadline requires the protected main thread")
    previous_deadline = _ACTIVE_DEADLINE.get()
    if previous_deadline is not None:
        raise SandboxError("nested end-to-end deadlines are unsupported")
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise SandboxError("pre-existing interval timer prevents deadline ownership")
    previous_handler = signal.getsignal(signal.SIGALRM)
    deadline = time.monotonic() + float(seconds)
    token = _ACTIVE_DEADLINE.set(deadline)

    def expired(_signum, _frame):
        raise EndToEndDeadlineExceeded("protected operation exhausted its end-to-end deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        _ACTIVE_DEADLINE.reset(token)


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the isolated command and all descendants in its host process group."""

    # The leader may already have exited while a descendant still holds the capture
    # pipes open.  Never use leader liveness as a proxy for process-group liveness.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    # Reap a cooperative leader promptly. Without this, a zombie leader can keep
    # killpg(..., 0) positive for the whole grace period even when no descendants
    # survived. If descendants still exist, the group probe below continues to
    # enforce their bounded cleanup.
    try:
        process.wait(timeout=min(0.1, PROCESS_CLEANUP_GRACE))
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + PROCESS_CLEANUP_GRACE
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=PROCESS_CLEANUP_GRACE)
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("isolated process group could not be cleaned up") from exc


def _run_bounded_process(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float,
    output_limit: int,
    memory_limit: int | None = None,
    process_limit: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Capture stdout/stderr incrementally and kill the process group on any budget breach."""

    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise SandboxError("bounded process requires fixed non-empty argv")
    if (timeout <= 0 or output_limit <= 0
            or memory_limit is not None and memory_limit <= 0
            or process_limit is not None and process_limit <= 0):
        raise SandboxError("bounded process budgets must be positive")
    timeout = _deadline_timeout(timeout)
    process = subprocess.Popen(
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = io_selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout
    selector.register(process.stdout, io_selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, io_selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxError("isolated process exceeded its timeout")
            events = selector.select(min(remaining, 0.1))
            if memory_limit is not None or process_limit is not None:
                count, memory = _descendant_usage(process.pid)
                if process_limit is not None and count > process_limit:
                    raise SandboxError("isolated process count exceeded its resource budget")
                if memory_limit is not None and memory > memory_limit:
                    raise SandboxError("isolated process memory exceeded its resource budget")
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), READ_CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if total + len(chunk) > output_limit:
                    raise SandboxError("isolated process output exceeded its evidence budget")
                buffers[key.data].extend(chunk)
                total += len(chunk)
        returncode = process.wait(timeout=PROCESS_CLEANUP_GRACE)
        return subprocess.CompletedProcess(
            list(argv),
            returncode,
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
        )
    except BaseException as exc:
        try:
            _stop_process_group(process)
        except SandboxError as cleanup_error:
            raise cleanup_error from exc
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _descendant_usage(root_pid: int) -> tuple[int, int]:
    """Count the host-visible descendant tree and aggregate resident memory."""

    pending, seen, resident = [root_pid], set(), 0
    page = os.sysconf("SC_PAGE_SIZE")
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            values = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="ascii").split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, UnicodeError, ValueError) as exc:
            raise SandboxError("cannot account isolated process resources") from exc
        if len(values) < 2 or not values[1].isdigit() or any(not item.isdigit() for item in children):
            raise SandboxError("unknown isolated process accounting layout")
        resident += int(values[1]) * page
        pending.extend(int(item) for item in children)
    return len(seen), resident


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SandboxError("invalid source path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise SandboxError("source path escapes the assigned worktree")
    return path


def selectors(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > 200:
        raise SandboxError("explicit bounded pytest selectors required")
    for value in values:
        if not isinstance(value, str) or not SELECTOR.fullmatch(value):
            raise SandboxError("unsupported pytest selector or option")
        safe_relative(value.split("::", 1)[0])
    if len(values) != len(set(values)):
        raise SandboxError("duplicate pytest selector")
    return values


def source_files(worktree: Path, extra_paths: list[str]) -> list[str]:
    result = _run_bounded_process(
        ["git", "-C", str(worktree), "ls-files", "-z"],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        timeout=30,
        output_limit=MAX_OUTPUT_BYTES,
    )
    if result.returncode != 0:
        raise SandboxError("trusted source inventory failed")
    try:
        candidates = set(result.stdout.decode("utf-8").split("\0")) - {""}
    except UnicodeDecodeError as exc:
        raise SandboxError("source inventory contains a non-UTF-8 path") from exc
    candidates.update(extra_paths)
    selected = []
    for value in sorted(candidates):
        path = safe_relative(value)
        if path.parts[0] in EXCLUDED_ROOTS or "__pycache__" in path.parts:
            continue
        if any(part.startswith(".env") and not part.endswith(".example") for part in path.parts):
            continue
        if any(part.lower() in {"credentials", "secrets", "private", "dumps"} for part in path.parts):
            continue
        if path.parts[0] == "data" or str(path).startswith("docs/memory/archive/"):
            continue
        if path.suffix not in SOURCE_SUFFIXES and path.name not in {"Makefile", "Dockerfile"}:
            continue
        original = worktree / path
        if not original.exists():  # A legitimate tracked deletion in the candidate.
            continue
        # No symlink or changed parent may expose host credentials or outside data.
        if original.is_symlink() or original.resolve() != original or not original.is_file():
            raise SandboxError("source snapshot refuses symlinks/nonfiles")
        if not original.resolve().is_relative_to(worktree):
            raise SandboxError("source file outside registered worktree")
        selected.append(value)
    return selected


def _sha40(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA40.fullmatch(value):
        raise SandboxError(f"{label} must be a full lowercase Git SHA")
    return value


def _git_read(worktree: Path, arguments: Sequence[str], *, output_limit: int) -> bytes:
    result = _run_bounded_process(
        ["git", "--no-replace-objects", "-C", str(worktree), *arguments],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        timeout=30,
        output_limit=output_limit,
    )
    if result.returncode != 0:
        raise SandboxError("trusted Git ancestry query failed")
    return result.stdout


def _write_loose_commit(git_dir: Path, object_id: str, body: bytes) -> int:
    canonical = b"commit " + str(len(body)).encode("ascii") + b"\0" + body
    try:
        observed = hashlib.sha1(canonical, usedforsecurity=False).hexdigest()
    except TypeError:  # pragma: no cover - compatibility for Python without this keyword.
        observed = hashlib.sha1(canonical).hexdigest()
    if observed != object_id:
        raise SandboxError("Git returned commit bytes that do not match their object ID")
    destination = git_dir / "objects" / object_id[:2] / object_id[2:]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(zlib.compress(canonical))
    return len(canonical)


def materialize_git_ancestry(
    worktree: Path,
    snapshot: Path,
    evidence: GitAncestryEvidence,
    *,
    byte_limit: int = MAX_ANCESTRY_BYTES,
) -> dict[str, int | str]:
    """Create a genuine commit-only shallow repo for one exact ancestry assertion.

    Commit object bytes come from the trusted registered worktree and are re-hashed
    before writing.  No host Git config, refs, tree, blob, index, log or credential is
    copied.  Standard shallow boundaries only remove unavailable parents; they cannot
    invent an ancestor edge.
    """

    if not isinstance(byte_limit, int) or isinstance(byte_limit, bool) or byte_limit <= 0:
        raise SandboxError("Git ancestry byte budget must be positive")
    ancestor = _sha40(evidence.ancestor_sha, "ancestry ancestor")
    head = _sha40(evidence.head_sha, "ancestry head")
    actual_head = _git_read(worktree, ("rev-parse", "--verify", "HEAD^{commit}"), output_limit=128).decode().strip()
    if actual_head != head:
        raise SandboxError("trusted ancestry head differs from registered worktree HEAD")
    _git_read(worktree, ("merge-base", "--is-ancestor", ancestor, head), output_limit=128)

    if ancestor == head:
        object_ids = [head]
        boundary_ids = [head]
    else:
        raw = _git_read(worktree, ("rev-list", "--boundary", f"{ancestor}..{head}"), output_limit=MAX_ANCESTRY_COMMITS * 42)
        lines = raw.decode("ascii").splitlines()
        if not lines or len(lines) > MAX_ANCESTRY_COMMITS:
            raise SandboxError("Git ancestry slice exceeds the reviewed commit budget")
        object_ids = []
        boundary_ids = []
        for line in lines:
            boundary = line.startswith("-")
            object_id = _sha40(line[1:] if boundary else line, "ancestry object")
            if object_id not in object_ids:
                object_ids.append(object_id)
            if boundary and object_id not in boundary_ids:
                boundary_ids.append(object_id)
        if head not in object_ids or ancestor not in boundary_ids:
            raise SandboxError("Git ancestry slice lacks its exact head or ancestor boundary")

    git_dir = snapshot / ".git"
    if git_dir.exists():
        raise SandboxError("source snapshot unexpectedly contains Git metadata")
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)
    total = 0
    for object_id in object_ids:
        body = _git_read(worktree, ("cat-file", "commit", object_id), output_limit=min(byte_limit, 512_000))
        total += _write_loose_commit(git_dir, object_id, body)
        if total > byte_limit:
            raise SandboxError("Git ancestry objects exceed the reviewed byte budget")
    (git_dir / "HEAD").write_text("ref: refs/heads/sandbox\n", encoding="ascii")
    (git_dir / "refs" / "heads" / "sandbox").write_text(head + "\n", encoding="ascii")
    (git_dir / "shallow").write_text("".join(f"{value}\n" for value in sorted(boundary_ids)), encoding="ascii")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n",
        encoding="ascii",
    )
    return {
        "ancestor_sha": ancestor,
        "head_sha": head,
        "commit_objects": len(object_ids),
        "commit_object_bytes": total,
    }


def _awk_alternative_args() -> list[str]:
    """Recreate only the public awk alternatives link required by /usr/bin/awk."""

    awk = Path("/usr/bin/awk")
    if not awk.is_symlink():
        if not awk.is_file() or not os.access(awk, os.X_OK):
            raise SandboxError("host awk executable is unavailable")
        return []
    first_target = os.readlink(awk)
    if first_target != "/etc/alternatives/awk":
        raise SandboxError("/usr/bin/awk has an unsupported symlink target")
    alternative = Path(first_target)
    if not alternative.is_symlink():
        raise SandboxError("awk alternatives entry is unavailable")
    executable = os.readlink(alternative)
    if not executable.startswith("/usr/bin/"):
        raise SandboxError("awk alternative escapes the read-only /usr mount")
    resolved = Path(executable)
    if resolved.is_symlink() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise SandboxError("awk alternative is not a direct executable in /usr/bin")
    return ["--dir", "/etc/alternatives", "--symlink", executable, first_target]


def sandbox_argv(snapshot: Path, selected_tests: list[str], bwrap: str = "/usr/bin/bwrap") -> list[str]:
    tests = selectors(selected_tests)
    args = ["/usr/bin/prlimit", "--core=0", f"--as={MAX_TEST_MEMORY_BYTES}",
            f"--cpu={MAX_TEST_CPU_SECONDS}",
            f"--nproc={MAX_TEST_PROCESSES}", f"--nofile={MAX_TEST_OPEN_FILES}",
            f"--fsize={MAX_TEST_FILE_BYTES}", "--", bwrap,
            "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session",
            "--uid", "65534", "--gid", "65534", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--symlink", "/proc/self/fd", "/dev/fd",
            "--symlink", "/proc/self/fd/0", "/dev/stdin", "--symlink", "/proc/self/fd/1", "/dev/stdout",
            "--symlink", "/proc/self/fd/2", "/dev/stderr",
            "--size", str(MAX_TEST_TMP_BYTES), "--tmpfs", "/tmp", "--chmod", "1777", "/tmp", "--dir", "/root",
            "--dir", "/etc", *_awk_alternative_args(), "--ro-bind", str(snapshot), "/work", "--chdir", "/work",
            "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "PYTHONPATH", "/work", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "APP_ENV_FILE", "config/unit-test.env.example", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "GIT_CONFIG_COUNT", "1", "--setenv", "GIT_CONFIG_KEY_0", "safe.directory",
            "--setenv", "GIT_CONFIG_VALUE_0", "/work",
            "--setenv", "LANG", "C.UTF-8"]
    # Only public runtime lookup files, never /etc as a whole or a home directory.
    for value in ("/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group"):
        if Path(value).is_file():
            args += ["--ro-bind", value, value]
    return args + ["/usr/bin/python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]


def run_tests(worktree: Path, selected_tests: list[str], extra_paths: list[str] | None = None,
              *, timeout: int = 1200, snapshot_limit: int = 300_000_000,
              git_ancestry: GitAncestryEvidence | None = None) -> dict:
    worktree = worktree.resolve(strict=True)
    selectors(selected_tests)
    if not 1 <= timeout <= 3600:
        raise SandboxError("test timeout outside controller budget")
    files = source_files(worktree, extra_paths or [])
    with tempfile.TemporaryDirectory(prefix="two-site-agent-tests-") as temporary:
        snapshot = Path(temporary) / "source"
        snapshot.mkdir()
        total = 0
        for value in files:
            original = worktree / value
            total += original.stat().st_size
            if total > snapshot_limit:
                raise SandboxError("source snapshot exceeds reviewed capacity")
            destination = snapshot / value
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination, follow_symlinks=False)
        for selector in selected_tests:
            if not (snapshot / selector.split("::", 1)[0]).is_file():
                raise SandboxError("test selector not in authorized source snapshot")
        if not (snapshot / "config/unit-test.env.example").is_file():
            raise SandboxError("hermetic unit-test environment example missing")
        ancestry = None
        if git_ancestry is not None:
            ancestry = materialize_git_ancestry(
                worktree,
                snapshot,
                git_ancestry,
                byte_limit=min(MAX_ANCESTRY_BYTES, snapshot_limit - total),
            )
            total += int(ancestry["commit_object_bytes"])
        # Source is read-only inside a separate mount/user/process/network namespace.
        # No host Git object DB/config, home, provider credentials, host /tmp, or
        # inherited environment is exposed. Optional .git contains commit objects only.
        result = _run_bounded_process(
            sandbox_argv(snapshot, selected_tests),
            timeout=timeout,
            output_limit=MAX_OUTPUT_BYTES,
            memory_limit=MAX_TEST_MEMORY_BYTES,
            process_limit=MAX_TEST_PROCESSES,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        return {"returncode": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "snapshot_files": len(files), "snapshot_bytes": total,
                "git_ancestry": ancestry,
                "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"}
