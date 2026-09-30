"""Read-only Git chain verification for reviewed maintenance transitions under AE03.

Verifies that a codec-validated maintenance-transition record accurately reflects
actual on-disk Git commit ancestry, exact changed paths, and regular tracked blob modes.
Enforces strict security boundaries:
1. Read-only Git inspection only: no repository, filesystem, or state mutation.
2. Root validation: absolute canonical directory, no symlinks, matching euid, not group/world writable.
3. Bounded execution: absolute /usr/bin/git, fixed argv, no shell, scrubbed environment
   without inherited credentials, GIT_NO_LAZY_FETCH=1, GIT_CONFIG_NOSYSTEM=1,
   GIT_OPTIONAL_LOCKS=0, --no-replace-objects, process timeouts, and raw output limits
   enforced before and after accumulation.
4. Input limits: canonical JSON bytes <= 16384 bytes checked pre-decode, or dict.
   No bytearray or unbounded input accepted.
5. Budget caps: public timeout (max 10.0s) and output_limit (max 65536 bytes)
   are validated and strictly capped; callers cannot widen them.
6. Error hygiene: error strings do not echo untrusted paths, hashes, or payloads.
7. Grants NO restart, replay, or promotion authority.
"""

from __future__ import annotations

import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.two_site_agent_sandbox import SandboxError, _run_bounded_process
except ImportError:
    from two_site_agent_sandbox import (  # type: ignore[no-redef]
        SandboxError,
        _run_bounded_process,
    )

try:
    from scripts.two_site_maintenance_transition_record import (
        MAX_MAINTENANCE_COMMITS,
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES,
        MaintenanceTransitionRecordError,
        parse_maintenance_transition_record,
        validate_maintenance_transition_record,
    )
except ImportError:
    from two_site_maintenance_transition_record import (  # type: ignore[no-redef]
        MAX_MAINTENANCE_COMMITS,
        MAX_MAINTENANCE_TRANSITION_RECORD_BYTES,
        MaintenanceTransitionRecordError,
        parse_maintenance_transition_record,
        validate_maintenance_transition_record,
    )

MAX_DECLARED_COMMIT_PATHS: int = 32
DEFAULT_GIT_TIMEOUT: float = 10.0
DEFAULT_GIT_OUTPUT_LIMIT: int = 65536
GIT_BIN: str = "/usr/bin/git"

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")

_GIT_ENV: dict[str, str] = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}


class MaintenanceGitProofError(ValueError):
    """Raised when maintenance Git chain verification fails."""


__all__ = [
    "DEFAULT_GIT_OUTPUT_LIMIT",
    "DEFAULT_GIT_TIMEOUT",
    "GIT_BIN",
    "MAX_DECLARED_COMMIT_PATHS",
    "MaintenanceGitProofError",
    "verify_declared_maintenance_git_chain",
]


def _validate_budget(timeout: float, output_limit: int) -> tuple[float, int]:
    """Validate that budgets are finite positive non-boolean values and enforce caps."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise MaintenanceGitProofError("timeout must be a finite positive number")
    if math.isnan(timeout) or math.isinf(timeout) or timeout <= 0:
        raise MaintenanceGitProofError("timeout must be a finite positive number")
    capped_timeout = min(float(timeout), DEFAULT_GIT_TIMEOUT)

    if isinstance(output_limit, bool) or not isinstance(output_limit, int):
        raise MaintenanceGitProofError("output_limit must be a positive integer")
    if output_limit <= 0:
        raise MaintenanceGitProofError("output_limit must be a positive integer")
    capped_output_limit = min(int(output_limit), DEFAULT_GIT_OUTPUT_LIMIT)

    return capped_timeout, capped_output_limit


def _run_git_query(
    root: Path,
    arguments: Sequence[str],
    *,
    timeout: float = DEFAULT_GIT_TIMEOUT,
    output_limit: int = DEFAULT_GIT_OUTPUT_LIMIT,
) -> bytes:
    """Run a bounded, read-only Git query with strict security flags and output limits."""
    capped_timeout, capped_output_limit = _validate_budget(timeout, output_limit)
    argv = [GIT_BIN, "--no-replace-objects", "-C", str(root), *arguments]
    try:
        proc = _run_bounded_process(
            argv,
            cwd=root,
            env=_GIT_ENV,
            timeout=capped_timeout,
            output_limit=capped_output_limit,
        )
    except SandboxError as exc:
        raise MaintenanceGitProofError(f"bounded git query failed: {exc}") from exc
    except Exception as exc:
        raise MaintenanceGitProofError(f"git process invocation failed: {exc}") from exc

    if proc.returncode != 0:
        cmd_name = arguments[0] if arguments else "git"
        raise MaintenanceGitProofError(
            f"git command '{cmd_name}' failed with exit code {proc.returncode}"
        )
    if len(proc.stdout) > capped_output_limit:
        raise MaintenanceGitProofError("git query output exceeded limit")
    return proc.stdout


def _validate_root(root: Path) -> Path:
    """Verify that root is an absolute canonical existing directory with safe permissions."""
    if not isinstance(root, Path):
        raise MaintenanceGitProofError("root must be a pathlib.Path instance")
    if not root.is_absolute():
        raise MaintenanceGitProofError("root must be an absolute path")
    try:
        st = root.stat()
    except (OSError, ValueError) as exc:
        raise MaintenanceGitProofError(f"root cannot be accessed: {exc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise MaintenanceGitProofError("root must be a directory")
    if root.is_symlink():
        raise MaintenanceGitProofError("root must not be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise MaintenanceGitProofError(f"root cannot be resolved: {exc}") from exc
    if resolved != root:
        raise MaintenanceGitProofError("root must be canonical and contain no symlinks")
    try:
        euid = os.geteuid()
        if st.st_uid != euid:
            raise MaintenanceGitProofError("root must be owned by current effective UID")
    except AttributeError:
        pass
    if (st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)) != 0:
        raise MaintenanceGitProofError("root must not be group- or world-writable")
    return root


def verify_declared_maintenance_git_chain(
    root: Path,
    record: object,
    *,
    timeout: float = DEFAULT_GIT_TIMEOUT,
    output_limit: int = DEFAULT_GIT_OUTPUT_LIMIT,
) -> None:
    """Verify actual Git commit ancestry, paths, and modes against a declared maintenance transition record.

    Args:
        root: Absolute canonical Path to repository root.
        record: Candidate record dict or raw canonical JSON bytes (<= 16384 bytes).
        timeout: Per-query execution timeout in seconds (strictly capped at 10.0s).
        output_limit: Per-query combined output limit in bytes (strictly capped at 65536).

    Raises:
        MaintenanceGitProofError: If verification of root, record, or Git facts fails.
    """
    effective_timeout, effective_output_limit = _validate_budget(timeout, output_limit)
    root = _validate_root(root)

    if type(record) is bytes:
        if len(record) > MAX_MAINTENANCE_TRANSITION_RECORD_BYTES:
            raise MaintenanceGitProofError("record bytes exceed maximum allowed length")
        if len(record) == 0:
            raise MaintenanceGitProofError("record bytes cannot be empty")
        try:
            norm_record = parse_maintenance_transition_record(record)
        except MaintenanceTransitionRecordError as exc:
            raise MaintenanceGitProofError(f"record failed codec validation: {exc}") from exc
    elif type(record) is dict:
        try:
            norm_record = validate_maintenance_transition_record(record)
        except MaintenanceTransitionRecordError as exc:
            raise MaintenanceGitProofError(f"record failed codec validation: {exc}") from exc
    else:
        raise MaintenanceGitProofError("record must be a dict or canonical JSON bytes")

    # Prove show-toplevel == root
    toplevel_raw = _run_git_query(
        root,
        ["rev-parse", "--show-toplevel"],
        timeout=effective_timeout,
        output_limit=effective_output_limit,
    )
    try:
        toplevel = toplevel_raw.decode("utf-8").strip("\r\n")
    except UnicodeDecodeError as exc:
        raise MaintenanceGitProofError("git show-toplevel output is not valid UTF-8") from exc
    if toplevel != str(root):
        raise MaintenanceGitProofError("git show-toplevel does not match root")

    # Prove HEAD == record.new_repository.head
    head_raw = _run_git_query(
        root,
        ["rev-parse", "--verify", "HEAD"],
        timeout=effective_timeout,
        output_limit=effective_output_limit,
    )
    try:
        actual_head = head_raw.decode("utf-8").strip("\r\n")
    except UnicodeDecodeError as exc:
        raise MaintenanceGitProofError("git rev-parse HEAD output is not valid UTF-8") from exc
    if not _HEX_40_RE.fullmatch(actual_head):
        raise MaintenanceGitProofError("git rev-parse HEAD returned invalid commit hash")

    expected_head = norm_record["new_repository"]["head"]
    if actual_head != expected_head:
        raise MaintenanceGitProofError(
            "actual HEAD does not match record new_repository head"
        )

    commits = norm_record["maintenance_commits"]
    if not (1 <= len(commits) <= MAX_MAINTENANCE_COMMITS):
        raise MaintenanceGitProofError(
            f"maintenance_commits count out of bounds (1..{MAX_MAINTENANCE_COMMITS})"
        )

    prior_head = norm_record["prior_head"]

    for idx, commit in enumerate(commits):
        declared_paths = commit["paths"]
        if not (1 <= len(declared_paths) <= MAX_DECLARED_COMMIT_PATHS):
            raise MaintenanceGitProofError(
                f"commit changed paths count {len(declared_paths)} "
                f"exceeds maximum allowed {MAX_DECLARED_COMMIT_PATHS} or is empty"
            )

        head = commit["head"]
        declared_parent = commit["parent"]

        # 1. Bounded rev-list --parents -n 1 <head>
        parent_raw = _run_git_query(
            root,
            ["rev-list", "--parents", "-n", "1", head],
            timeout=effective_timeout,
            output_limit=effective_output_limit,
        )
        try:
            parent_text = parent_raw.decode("utf-8").strip("\r\n")
        except UnicodeDecodeError as exc:
            raise MaintenanceGitProofError("rev-list output is not valid UTF-8") from exc

        tokens = parent_text.split()
        if not tokens or tokens[0] != head:
            raise MaintenanceGitProofError("rev-list output does not match query commit")
        actual_parents = tokens[1:]
        if len(actual_parents) != 1:
            raise MaintenanceGitProofError(
                f"commit has {len(actual_parents)} parents; exactly one required"
            )
        actual_parent = actual_parents[0]
        if not _HEX_40_RE.fullmatch(actual_parent):
            raise MaintenanceGitProofError("actual parent is not a valid 40-character hex SHA")
        if actual_parent != declared_parent:
            raise MaintenanceGitProofError(
                "actual parent does not match declared parent"
            )

        if idx == 0:
            if actual_parent != prior_head:
                raise MaintenanceGitProofError("first commit parent does not match prior_head")
        else:
            prev_head = commits[idx - 1]["head"]
            if actual_parent != prev_head:
                raise MaintenanceGitProofError("commit parent does not match previous commit head")

        # 2. Bounded diff-tree --no-commit-id --name-only -r -z <head>
        diff_raw = _run_git_query(
            root,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", head],
            timeout=effective_timeout,
            output_limit=effective_output_limit,
        )
        if diff_raw == b"":
            actual_paths: list[str] = []
        else:
            if not diff_raw.endswith(b"\x00"):
                raise MaintenanceGitProofError("diff-tree output is not NUL-terminated")
            raw_parts = diff_raw[:-1].split(b"\x00")
            actual_paths = []
            seen_diff_paths: set[str] = set()
            for part in raw_parts:
                if len(part) == 0:
                    raise MaintenanceGitProofError("diff-tree output contains empty path entry")
                try:
                    p_str = part.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise MaintenanceGitProofError("diff-tree path is not valid UTF-8") from exc
                if p_str in seen_diff_paths:
                    raise MaintenanceGitProofError("diff-tree contains duplicate path")
                seen_diff_paths.add(p_str)
                actual_paths.append(p_str)

        if actual_paths != declared_paths:
            raise MaintenanceGitProofError(
                "actual changed paths do not match declared paths"
            )

        # 3. For each changed path: ls-tree -z <head> -- <path>
        for path in declared_paths:
            lstree_raw = _run_git_query(
                root,
                ["ls-tree", "-z", head, "--", path],
                timeout=effective_timeout,
                output_limit=effective_output_limit,
            )
            if lstree_raw == b"":
                raise MaintenanceGitProofError(
                    "path not found in tree for commit (deleted or missing)"
                )
            if not lstree_raw.endswith(b"\x00"):
                raise MaintenanceGitProofError("ls-tree output is not NUL-terminated")
            entries = lstree_raw[:-1].split(b"\x00")
            if len(entries) != 1:
                raise MaintenanceGitProofError("ls-tree returned unexpected entry count")
            entry = entries[0]
            tab_pos = entry.find(b"\t")
            if tab_pos == -1:
                raise MaintenanceGitProofError("malformed ls-tree output (missing tab delimiter)")
            meta_bytes = entry[:tab_pos]
            path_bytes = entry[tab_pos + 1:]
            try:
                entry_path = path_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MaintenanceGitProofError("ls-tree path is not valid UTF-8") from exc
            if entry_path != path:
                raise MaintenanceGitProofError("ls-tree returned unexpected path")
            try:
                meta_str = meta_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MaintenanceGitProofError("ls-tree metadata is not valid UTF-8") from exc
            meta_parts = meta_str.split()
            if len(meta_parts) != 3:
                raise MaintenanceGitProofError("malformed ls-tree metadata fields")
            mode, obj_type, obj_sha = meta_parts
            if not _HEX_40_RE.fullmatch(obj_sha):
                raise MaintenanceGitProofError("ls-tree object hash is not a valid 40-character hex SHA")
            if obj_type != "blob":
                raise MaintenanceGitProofError("path object type is not blob")
            if mode not in ("100644", "100755"):
                raise MaintenanceGitProofError(
                    "path mode is not regular file (100644 or 100755)"
                )

    if commits[-1]["head"] != actual_head:
        raise MaintenanceGitProofError(
            "final commit head does not match actual HEAD"
        )
