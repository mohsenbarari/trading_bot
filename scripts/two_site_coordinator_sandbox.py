"""Filesystem containment for the coordinator, with the parent's network.

This wrapper does not isolate child networking. Native tests run in the trusted
outside Broker's existing unshare-all sandbox; nested bwrap network setup is not
supported under the coordinator's dropped capabilities. Host authentication is
read-only; the Supervisor owns any normal-client refresh preflight.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
from typing import Mapping


class CoordinatorSandboxError(ValueError):
    """The requested containment could not be constructed safely."""


_BWRAP = Path("/usr/bin/bwrap")
_AUTH_ROOT = Path("/root/.qoder/.auth")
_PUBLIC_ETC = (
    "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/gai.conf",
    "/etc/passwd", "/etc/group", "/etc/ld.so.cache", "/etc/localtime",
    "/etc/ssl/certs", "/etc/ssl/openssl.cnf",
)
_SCOPE_ENV = frozenset({
    "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED", "TWO_SITE_ASSIGNMENT_ID",
    "TWO_SITE_ASSIGNMENT_SHA256", "TWO_SITE_AUTOPILOT_RUN_ID",
    "TWO_SITE_PREPARATION_STARTUP_SHA256",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
})
_HIDDEN_PROJECT_NAMES = frozenset({
    "live.env", ".aws", ".ssh", "production-data", "data", "uploads",
})
_FORBIDDEN_MOUNTS = tuple(Path(path) for path in (
    "/srv", "/var", "/etc", "/root/.ssh", "/root/.aws", "/root/secure-envs",
))


def _need(condition: object, code: str) -> None:
    if not condition:
        raise CoordinatorSandboxError(code)


def _canonical(path: Path, *, directory: bool) -> Path:
    path = Path(path)
    _need(path.is_absolute() and not any(ord(c) < 32 for c in str(path)), "SANDBOX_PATH")
    _need(".." not in path.parts, "SANDBOX_PATH_TRAVERSAL")
    try:
        for item in (path, *path.parents):
            _need(not item.is_symlink(), "SANDBOX_SYMLINK_PATH")
        resolved = path.resolve(strict=True)
        mode = resolved.stat().st_mode
        _need(stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode), "SANDBOX_PATH_TYPE")
        return resolved
    except OSError as exc:
        raise CoordinatorSandboxError("SANDBOX_PATH_UNREADABLE") from exc


def _git_path(root: Path, option: str) -> Path:
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), "rev-parse", "--path-format=absolute", option],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                 "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_OPTIONAL_LOCKS": "0"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        _need(result.returncode == 0 and 0 < len(result.stdout) <= 4096, "SANDBOX_GIT_ROOT")
        return _canonical(Path(os.fsdecode(result.stdout).strip()), directory=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CoordinatorSandboxError("SANDBOX_GIT_METADATA") from exc


def _private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        _canonical(path, directory=True)
        info = path.stat()
        _need(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
              "SANDBOX_CACHE_CUSTODY")
    except OSError as exc:
        raise CoordinatorSandboxError("SANDBOX_CACHE_CREATE") from exc


def _commit_signing_required(root: Path) -> bool:
    """Query one public policy bit, without exporting the user's Git config."""
    try:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(root), "config", "--type=bool", "--get", "commit.gpgsign"],
            env={"PATH": "/usr/bin:/bin", "HOME": "/root"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        if result.returncode == 1 and not result.stdout:
            return False
        _need(result.returncode == 0 and result.stdout in (b"true\n", b"false\n"),
              "SANDBOX_SIGNING_POLICY")
        return result.stdout == b"true\n"
    except (OSError, subprocess.SubprocessError) as exc:
        raise CoordinatorSandboxError("SANDBOX_SIGNING_POLICY") from exc


def buildwrap_coordinator_command(
    root: Path, run_dir: Path, argv: list[str], *,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the complete bwrap command; refuse malformed roots and escapes.

    Only the integration checkout, its containing repository's Git common
    directory, and a per-run Qoder cache are writable. No host home, /srv,
    secure-envs, Docker socket, SSH configuration, or credential environment is
    inherited. The two existing Qoder login files are the only host auth mounts.
    """
    root = _canonical(root, directory=True)
    run_dir = _canonical(run_dir, directory=True)
    _need(root not in {Path("/"), Path("/root"), Path("/tmp"), Path("/usr"), Path("/srv")},
          "SANDBOX_ROOT_SCOPE")
    _need(not any(root.is_relative_to(path) for path in _FORBIDDEN_MOUNTS), "SANDBOX_ROOT_SCOPE")
    _need(run_dir.is_relative_to(root) and run_dir != root, "SANDBOX_RUN_ESCAPE")
    _need(type(argv) is list and bool(argv)
          and all(type(value) is str and "\0" not in value for value in argv), "SANDBOX_ARGV")
    executable = Path(argv[0])
    _need(executable.is_absolute(), "SANDBOX_EXECUTABLE_ABSOLUTE")
    # System executable aliases are already covered by the read-only /usr bind.
    if executable.is_relative_to(Path("/usr")) or executable.is_relative_to(Path("/bin")):
        executable = executable.resolve(strict=True)
        _need(executable.is_relative_to(Path("/usr")) and executable.is_file(), "SANDBOX_EXECUTABLE")
    else:
        executable = _canonical(executable, directory=False)
    _need(not any(executable.is_relative_to(path) for path in _FORBIDDEN_MOUNTS),
          "SANDBOX_EXECUTABLE_SCOPE")
    _need(os.access(executable, os.X_OK) and _BWRAP.is_file(), "SANDBOX_EXECUTABLE")
    _need(_git_path(root, "--show-toplevel") == root, "SANDBOX_NOT_WORKTREE_ROOT")
    common = _git_path(root, "--git-common-dir")
    _need(common.name == ".git" and root.is_relative_to(common.parent), "SANDBOX_GIT_COMMON_SCOPE")
    signing_required = _commit_signing_required(root)

    scope_env = {}
    environment = os.environ if environment is None else environment
    _need(isinstance(environment, Mapping), "SANDBOX_ENVIRONMENT")
    for name in sorted(_SCOPE_ENV):
        if name in environment:
            value = environment[name]
            _need(type(value) is str and 0 < len(value) <= 2048
                  and all(ord(c) >= 32 for c in value), "SANDBOX_SCOPE_ENV")
            scope_env[name] = value

    state = run_dir / "coordinator-qoder-state"
    _private_directory(state)
    for relative in (".auth", "cache", ".cache", "logs", "projects", "tmp"):
        _private_directory(state / relative)

    command = [str(_BWRAP), "--die-with-parent", "--new-session",
               "--unshare-all", "--share-net", "--cap-drop", "ALL", "--clearenv",
               "--ro-bind", "/usr", "/usr"]
    for alias in ("bin", "sbin", "lib", "lib64"):
        source = Path("/") / alias
        if source.is_symlink():
            command += ["--symlink", os.readlink(source), str(source)]
        elif source.is_dir():
            command += ["--ro-bind", str(source), str(source)]
    command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
    for value in _PUBLIC_ETC:
        path = Path(value)
        if path.exists():
            command += ["--ro-bind", str(path.resolve(strict=True)), value]
    command += ["--bind", str(root), str(root)]
    if not common.is_relative_to(root):
        command += ["--bind", str(common), str(common)]
    # A pinned CLI inside the writable worktree must remain read-only too.
    if not executable.is_relative_to(Path("/usr")):
        command += ["--ro-bind", str(executable), str(executable)]
    for item in root.iterdir():
        if item.name in _HIDDEN_PROJECT_NAMES or item.name.startswith(".env"):
            _need(not item.is_symlink(), "SANDBOX_PROTECTED_SYMLINK")
            if item.is_dir():
                command += ["--tmpfs", str(item), "--remount-ro", str(item)]
            elif item.is_file():
                command += ["--ro-bind", "/dev/null", str(item)]
            else:
                raise CoordinatorSandboxError("SANDBOX_PROTECTED_TYPE")
    # Parent repository data is not exposed by the sparse rootfs. This mount
    # contains per-run state only; never mount ~/.qoder settings/MCP/plugins.
    command += ["--bind", str(state), "/root/.qoder"]
    for name in ("user", "machine_id"):
        source = _AUTH_ROOT / name
        if source.exists():
            source = _canonical(source, directory=False)
            _need(source.stat().st_uid == os.geteuid() and source.stat().st_nlink == 1,
                  "SANDBOX_AUTH_CUSTODY")
            command += ["--ro-bind", str(source), f"/root/.qoder/.auth/{name}"]
    fixed_env = {
        "HOME": "/root", "USER": "root", "LOGNAME": "root", "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/tmp",
        "APP_ENV_FILE": "config/unit-test.env.example", "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1", "QODER_CONFIG_DIR": "/root/.qoder",
        "TWO_SITE_COORDINATOR_SANDBOX": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    }
    if signing_required:
        # Private signing material stays hidden. A required signer that is not
        # available must fail, rather than silently producing unsigned commits.
        fixed_env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="commit.gpgsign",
                         GIT_CONFIG_VALUE_0="true")
    for name, value in (fixed_env | scope_env).items():
        command += ["--setenv", name, value]
    return command + ["--chdir", str(root), "--", str(executable), *argv[1:]]
