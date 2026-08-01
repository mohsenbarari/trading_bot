#!/usr/bin/env python3
"""Collect one bounded, read-only dedicated-host preflight receipt.

This agent intentionally has a very small interface: it accepts one canonical
JSON request from standard input and writes one canonical JSON result to
standard output.  The request cannot select a path, command, URL, credential,
or destination.  Every filesystem location, executable, child environment,
and probe is source-owned below.

The probes are observational only.  They do not contact Object Storage or a
remote host, create files, change Docker state, or invoke a shell.  Docker is
queried only through its default local Unix-socket context with a clean child
environment.  The receipt contains normalized booleans, counts, and fixed
metadata rather than command output.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any


# This agent is intended to inspect an immutable staged release.  Project
# imports must not create ``__pycache__`` residue in that release.
sys.dont_write_bytecode = True


# The agent is installed inside the immutable release tree.  It needs that
# source root when invoked directly as ``python3 scripts/<name>.py``.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from core.dedicated_host_preflight_receipt import (  # noqa: E402
    DedicatedHostPreflightReceiptError,
    PREFLIGHT_RECEIPT_SCHEMA,
    canonical_json_bytes,
    validate_preflight_receipt,
)
from scripts.dedicated_host_preflight_manifest import (  # noqa: E402
    EXPECTED_HOSTS,
    READONLY_REQUEST_SCHEMA,
)


# Keep the agent-facing name stable while the source-owned contract owns the
# literal schema value.
REQUEST_SCHEMA = READONLY_REQUEST_SCHEMA
REJECTION_SCHEMA = "three-site-dedicated-host-readonly-preflight-rejection-v1"
MAX_REQUEST_BYTES = 4 * 1024
COMMAND_TIMEOUT_SECONDS = 8

REQUEST_FIELDS = frozenset(
    {"schema", "campaign_id", "operation_id", "release_sha", "role", "manifest_sha256"}
)
FIXED_RELEASE_ROOT = Path("/srv/trading-bot-three-site/releases")
FIXED_CURRENT_LINK = Path("/srv/trading-bot/current")
FIXED_STAGING_MOUNT = Path("/srv/trading-bot-three-site-staging-data")

GIT_BINARY = "/usr/bin/git"
DOCKER_BINARY = "/usr/bin/docker"
PGREP_BINARY = "/usr/bin/pgrep"
FINDMNT_BINARY = "/usr/bin/findmnt"

# No inherited Git, Docker, proxy, locale, or home-directory configuration is
# available to child probes.  Git optional locks are disabled so the read-only
# checks cannot refresh an index lock or prompt for a credential.
FIXED_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "file",
    "GIT_PROTOCOL_FROM_USER": "0",
    "DOCKER_CONTEXT": "default",
}
# Repository-local Git configuration remains readable so the probe can inspect
# the exact release, but none of its optional external helpers may run as root.
# Keep these overrides before every Git subcommand rather than relying on the
# ambient environment or on optional-lock behavior alone.
GIT_FIXED_PREFIX = (
    "--no-pager",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.preloadIndex=false",
    "-c",
    "maintenance.auto=false",
    "-c",
    "gc.auto=0",
    "-c",
    "diff.external=false",
    "-c",
    "core.pager=cat",
)
MATRIX_PROCESS_PATTERN = (
    r"(?:full[-_]?matrix|acceptance[-_]?matrix|"
    r"stage[0-9]+(?:[-_][a-z0-9]+)*[-_]matrix)"
)
HEX40 = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
DECIMAL_OUTPUT = re.compile(r"^[0-9]+\n?$", re.ASCII)


class DedicatedHostReadOnlyPreflightError(RuntimeError):
    """The agent could not safely produce a bounded observation."""


class FixedProbeUnavailable(DedicatedHostReadOnlyPreflightError):
    """A fixed local read-only probe could not be executed."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DedicatedHostReadOnlyPreflightError("request has duplicate JSON fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise DedicatedHostReadOnlyPreflightError("request has an unsupported JSON constant")


def canonical_request_bytes(value: object) -> bytes:
    """Encode only canonical ASCII JSON; this helper performs no I/O."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DedicatedHostReadOnlyPreflightError("request is not canonical JSON") from exc


def _source_owned_instance(role: object) -> dict[str, str]:
    if not isinstance(role, str) or role not in EXPECTED_HOSTS:
        raise DedicatedHostReadOnlyPreflightError("request role is not source-owned")
    binding = EXPECTED_HOSTS[role]
    return {
        "provider": "arvan_ecc",
        "server_id": binding["instance_id"],
        "public_ipv4": binding["public_ip"],
    }


def validate_request(value: object) -> dict[str, str]:
    """Normalize a capability-free request using the receipt's strict types."""

    if not isinstance(value, Mapping) or set(value) != REQUEST_FIELDS:
        raise DedicatedHostReadOnlyPreflightError("request fields are invalid")
    if value.get("schema") != REQUEST_SCHEMA:
        raise DedicatedHostReadOnlyPreflightError("request schema is invalid")
    role = value.get("role")
    instance = _source_owned_instance(role)
    # Reuse the receipt validator for every untrusted identifier.  The static
    # observation is only a type-validation envelope and is never emitted.
    envelope = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": value.get("campaign_id"),
        "operation_id": value.get("operation_id"),
        "release_sha": value.get("release_sha"),
        "role": role,
        "instance": instance,
        "manifest_sha256": value.get("manifest_sha256"),
        "observed_at": "2000-01-01T00:00:00Z",
        "observation": {
            "role_marker": role,
            "release": {"state": "missing", "release_sha": None, "clean": None},
            "runtime": {
                "docker_state": "inactive",
                "container_count": 0,
                "matrix_process_count": 0,
                "current_link_present": False,
            },
            "staging_mount": {
                "present": False,
                "filesystem": None,
                "available_bytes": None,
                "options": [],
            },
        },
    }
    try:
        normalized = validate_preflight_receipt(envelope)
    except DedicatedHostPreflightReceiptError as exc:
        raise DedicatedHostReadOnlyPreflightError("request identity is invalid") from exc
    return {
        "campaign_id": normalized["campaign_id"],
        "operation_id": normalized["operation_id"],
        "release_sha": normalized["release_sha"],
        "role": normalized["role"],
        "manifest_sha256": normalized["manifest_sha256"],
    }


def parse_request_payload(payload: bytes) -> dict[str, str]:
    """Parse one small canonical request; URLs and capabilities are impossible."""

    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_REQUEST_BYTES:
        raise DedicatedHostReadOnlyPreflightError("request size is invalid")
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise DedicatedHostReadOnlyPreflightError("request is not strict ASCII JSON") from exc
    if payload != canonical_request_bytes(document) + b"\n":
        raise DedicatedHostReadOnlyPreflightError("request is not canonical JSON")
    return validate_request(document)


def _read_request_stdin() -> bytes:
    """Read at most one bounded request from stdin, without a temp file."""

    return os.read(0, MAX_REQUEST_BYTES + 1)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DedicatedHostReadOnlyPreflightError("preflight agent must run as root")


def _root_controlled_directory_chain(path: Path, *, label: str) -> bool:
    """Check a lexical directory chain without resolving symlinks.

    ``False`` means that some component is genuinely absent, which permits a
    normal ``missing`` release observation.  Any existing unsafe component is
    an error rather than an absence claim: a root-run probe must not treat a
    substituted release layout as an ordinary missing release.
    """

    if not path.is_absolute():
        raise DedicatedHostReadOnlyPreflightError(f"{label} path is not absolute")
    current = Path(path.anchor)
    components = (None, *path.parts[1:])
    for component in components:
        if component is not None:
            current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise DedicatedHostReadOnlyPreflightError(f"{label} path cannot be observed") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise DedicatedHostReadOnlyPreflightError(f"{label} path is not root-controlled")
    return True


def _release_directory(release_sha: str) -> Path | None:
    """Return only an existing root-controlled, non-symlink release directory."""

    if HEX40.fullmatch(release_sha) is None:
        raise DedicatedHostReadOnlyPreflightError("release SHA is invalid")
    if not _root_controlled_directory_chain(FIXED_RELEASE_ROOT, label="release root"):
        return None
    release_path = FIXED_RELEASE_ROOT / release_sha
    try:
        release_status = os.lstat(release_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DedicatedHostReadOnlyPreflightError("release cannot be observed") from exc
    if (
        stat.S_ISLNK(release_status.st_mode)
        or not stat.S_ISDIR(release_status.st_mode)
        or release_status.st_uid != 0
        or stat.S_IMODE(release_status.st_mode) & 0o022
    ):
        raise DedicatedHostReadOnlyPreflightError("release directory is not root-controlled")
    return release_path


def _fixed_command(name: str, *, release_sha: str | None = None) -> tuple[str, ...]:
    """Construct a whitelisted local query with no caller command or path."""

    if name in {"git_head", "git_tracked", "git_untracked"}:
        if release_sha is None or HEX40.fullmatch(release_sha) is None:
            raise DedicatedHostReadOnlyPreflightError("fixed Git probe binding is invalid")
        prefix = (
            GIT_BINARY,
            *GIT_FIXED_PREFIX,
            "-C",
            str(FIXED_RELEASE_ROOT / release_sha),
        )
        if name == "git_head":
            return (*prefix, "rev-parse", "--verify", "HEAD^{commit}")
        if name == "git_tracked":
            return (
                *prefix,
                "diff-index",
                "--quiet",
                "--no-ext-diff",
                "--no-renames",
                "--ignore-submodules=none",
                "HEAD",
                "--",
            )
        return (*prefix, "ls-files", "--others", "--exclude-standard", "-z")
    if release_sha is not None:
        raise DedicatedHostReadOnlyPreflightError("fixed non-Git probe has an invalid binding")
    if name == "docker_info":
        return (DOCKER_BINARY, "info", "--format", "{{.ServerVersion}}")
    if name == "docker_ps":
        return (DOCKER_BINARY, "ps", "--all", "--quiet")
    if name == "matrix_count":
        return (PGREP_BINARY, "--full", "--count", "--", MATRIX_PROCESS_PATTERN)
    if name == "staging_mount":
        return (
            FINDMNT_BINARY,
            "--noheadings",
            "--raw",
            "--output",
            "TARGET,FSTYPE,OPTIONS",
            "--mountpoint",
            str(FIXED_STAGING_MOUNT),
        )
    raise DedicatedHostReadOnlyPreflightError("fixed probe is unknown")


def _run_fixed_command(name: str, *, release_sha: str | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run exactly one fixed query with no inherited environment or shell."""

    command = _fixed_command(name, release_sha=release_sha)
    try:
        return subprocess.run(
            command,
            check=False,
            close_fds=True,
            cwd="/",
            env=dict(FIXED_ENV),
            shell=False,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FixedProbeUnavailable("fixed local probe is unavailable") from exc


def _ascii_single_line(value: bytes, *, label: str) -> str:
    try:
        text = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DedicatedHostReadOnlyPreflightError(f"{label} output is not ASCII") from exc
    if "\r" in text:
        raise DedicatedHostReadOnlyPreflightError(f"{label} output is malformed")
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise DedicatedHostReadOnlyPreflightError(f"{label} output is malformed")
    return lines[0]


def _observe_release(release_sha: str) -> dict[str, object]:
    """Observe exactly the requested release and its working-tree cleanliness."""

    if _release_directory(release_sha) is None:
        return {"state": "missing", "release_sha": None, "clean": None}
    head = _run_fixed_command("git_head", release_sha=release_sha)
    if head.returncode != 0:
        raise DedicatedHostReadOnlyPreflightError("existing release Git head cannot be observed")
    observed_sha = _ascii_single_line(head.stdout, label="Git head")
    if HEX40.fullmatch(observed_sha) is None or observed_sha != release_sha:
        raise DedicatedHostReadOnlyPreflightError("existing release Git head does not match the requested release")
    tracked = _run_fixed_command("git_tracked", release_sha=release_sha)
    if tracked.returncode == 0:
        clean = True
    elif tracked.returncode == 1:
        clean = False
    else:
        raise DedicatedHostReadOnlyPreflightError("Git cleanliness cannot be observed")
    untracked = _run_fixed_command("git_untracked", release_sha=release_sha)
    if untracked.returncode != 0:
        raise DedicatedHostReadOnlyPreflightError("Git untracked state cannot be observed")
    if untracked.stdout:
        clean = False
    return {"state": "present", "release_sha": release_sha, "clean": clean}


def _observe_docker() -> tuple[str, int]:
    """Return only the local Docker state and number of existing containers."""

    try:
        state = _run_fixed_command("docker_info")
    except FixedProbeUnavailable:
        return "unavailable", 0
    if state.returncode != 0:
        return "inactive", 0
    try:
        containers = _run_fixed_command("docker_ps")
    except FixedProbeUnavailable:
        return "unavailable", 0
    if containers.returncode != 0:
        return "unavailable", 0
    count = sum(1 for line in containers.stdout.splitlines() if line)
    if count > 1_000_000:
        raise DedicatedHostReadOnlyPreflightError("Docker container count is invalid")
    return "active", count


def _observe_matrix_process_count() -> int:
    """Count fixed-name Matrix processes without returning their arguments."""

    result = _run_fixed_command("matrix_count")
    if result.returncode == 1:
        return 0
    if result.returncode != 0:
        raise DedicatedHostReadOnlyPreflightError("Matrix process count cannot be observed")
    text = _ascii_single_line(result.stdout, label="Matrix process count")
    if DECIMAL_OUTPUT.fullmatch(text + "\n") is None:
        raise DedicatedHostReadOnlyPreflightError("Matrix process count is malformed")
    count = int(text)
    if count > 1_000_000:
        raise DedicatedHostReadOnlyPreflightError("Matrix process count is invalid")
    return count


def _observe_current_link_present() -> bool:
    """Check only whether the fixed current path itself is a symbolic link."""

    try:
        metadata = os.lstat(FIXED_CURRENT_LINK)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DedicatedHostReadOnlyPreflightError("current link cannot be observed") from exc
    return stat.S_ISLNK(metadata.st_mode)


def _observe_staging_mount() -> dict[str, object]:
    """Observe capacity and the fixed safe mount-option projection."""

    if not _root_controlled_directory_chain(FIXED_STAGING_MOUNT, label="staging mount"):
        return {"present": False, "filesystem": None, "available_bytes": None, "options": []}
    mount = _run_fixed_command("staging_mount")
    if mount.returncode == 1:
        return {"present": False, "filesystem": None, "available_bytes": None, "options": []}
    if mount.returncode != 0:
        raise DedicatedHostReadOnlyPreflightError("staging mount cannot be observed")
    description = _ascii_single_line(mount.stdout, label="staging mount")
    fields = description.split(maxsplit=2)
    if len(fields) != 3:
        raise DedicatedHostReadOnlyPreflightError("staging mount output is malformed")
    target, filesystem, raw_options = fields
    if target != str(FIXED_STAGING_MOUNT):
        raise DedicatedHostReadOnlyPreflightError("staging mount is not the fixed mountpoint")
    observed_options = set(raw_options.split(","))
    if "rw" not in observed_options or {"ro", "suid", "dev", "exec"}.intersection(observed_options):
        raise DedicatedHostReadOnlyPreflightError("staging mount is not writable")
    options = sorted(observed_options & {"rw", "nosuid", "nodev", "noexec"})
    try:
        capacity = os.statvfs(FIXED_STAGING_MOUNT)
    except OSError as exc:
        raise DedicatedHostReadOnlyPreflightError("staging capacity cannot be observed") from exc
    available_bytes = capacity.f_frsize * capacity.f_bavail
    if available_bytes < 0:
        raise DedicatedHostReadOnlyPreflightError("staging capacity is invalid")
    return {
        "present": True,
        "filesystem": filesystem,
        "available_bytes": available_bytes,
        "options": options,
    }


def _observed_at() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_normalized_receipt(selection: Mapping[str, str]) -> dict[str, Any]:
    """Collect one receipt after strict request parsing has completed."""

    _require_root()
    docker_state, container_count = _observe_docker()
    receipt = {
        "schema": PREFLIGHT_RECEIPT_SCHEMA,
        "status": "observed",
        "observation_mode": "read-only",
        "campaign_id": selection["campaign_id"],
        "operation_id": selection["operation_id"],
        "release_sha": selection["release_sha"],
        "role": selection["role"],
        "instance": _source_owned_instance(selection["role"]),
        "manifest_sha256": selection["manifest_sha256"],
        "observed_at": _observed_at(),
        "observation": {
            "role_marker": selection["role"],
            "release": _observe_release(selection["release_sha"]),
            "runtime": {
                "docker_state": docker_state,
                "container_count": container_count,
                "matrix_process_count": _observe_matrix_process_count(),
                "current_link_present": _observe_current_link_present(),
            },
            "staging_mount": _observe_staging_mount(),
        },
    }
    try:
        return validate_preflight_receipt(receipt)
    except DedicatedHostPreflightReceiptError as exc:
        raise DedicatedHostReadOnlyPreflightError("read-only receipt is invalid") from exc


def collect_preflight_receipt(request: object) -> dict[str, Any]:
    """Collect and validate the sole receipt that this agent can emit."""

    return _collect_normalized_receipt(validate_request(request))


def _rejection_payload() -> bytes:
    return canonical_request_bytes({"schema": REJECTION_SCHEMA, "status": "rejected"}) + b"\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Run without command-line options; stdin is the only request channel."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments:
            raise DedicatedHostReadOnlyPreflightError("command-line arguments are forbidden")
        selection = parse_request_payload(_read_request_stdin())
        receipt = _collect_normalized_receipt(selection)
    except DedicatedHostReadOnlyPreflightError:
        sys.stdout.buffer.write(_rejection_payload())
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
