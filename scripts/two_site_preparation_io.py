"""Two-site preparation consumer I/O and telemetry emission for AE01-40.

Provides immutable startup loading and verification bound to Git HEAD
and active assignment scope, alongside bounded source-free stderr telemetry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

if __package__:
    from .two_site_assignment_scope import (
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
    )
    from .two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
        assert_same_startup,
        preparation_telemetry,
    )
    from .two_site_proposal_runner import (
        ProposalError,
        bounded_file_bytes,
        git,
        unique_json,
    )
else:
    from two_site_assignment_scope import (
        AssignmentScope,
        AssignmentScopeError,
        load_active_assignment_scope,
    )
    from two_site_preparation_contract import (
        PreparationConfigError,
        PreparationStartup,
        assert_same_startup,
        preparation_telemetry,
    )
    from two_site_proposal_runner import (
        ProposalError,
        bounded_file_bytes,
        git,
        unique_json,
    )

__all__ = [
    "emit_preparation_telemetry",
    "load_preparation_startup",
    "verify_preparation_startup",
]

_CYCLE_ID_PATTERN = re.compile(r"^cycle-[0-9]{4}-[0-9]{8}T[0-9]{6}Z-[0-9]+\Z")
_HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}\Z")
# Canonical environment locator variable names.
_RUN_ID_ENV = "TWO_SITE_AUTOPILOT_RUN_ID"
_SHA_ENV = "TWO_SITE_PREPARATION_STARTUP_SHA256"


def load_preparation_startup(
    root: Path,
    environment=None,
) -> PreparationStartup | None:
    """Load and validate immutable startup identity from supervisor record."""
    env = os.environ if environment is None else environment

    has_run_id = _RUN_ID_ENV in env
    has_sha = _SHA_ENV in env

    if not has_run_id and not has_sha:
        return None

    if not (has_run_id and has_sha):
        raise PreparationConfigError("Both locator environment variables must be present or absent")

    run_id = env[_RUN_ID_ENV]
    sha = env[_SHA_ENV]

    if type(run_id) is not str or type(sha) is not str:
        raise PreparationConfigError("Locator environment variables must be exact str")

    if len(run_id) > 80 or not _CYCLE_ID_PATTERN.fullmatch(run_id):
        raise PreparationConfigError("Invalid cycle ID locator format")

    if not _HEX64_PATTERN.fullmatch(sha):
        raise PreparationConfigError("Invalid startup digest locator format")

    if not isinstance(root, Path) or not root.is_absolute():
        raise PreparationConfigError("root must be an absolute Path")

    try:
        st_root = root.lstat()
    except OSError:
        raise PreparationConfigError("Cannot access root directory") from None

    if not stat.S_ISDIR(st_root.st_mode) or stat.S_ISLNK(st_root.st_mode):
        raise PreparationConfigError("root must be a real directory")

    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        raise PreparationConfigError("Cannot resolve root directory") from None

    if resolved_root != root:
        raise PreparationConfigError("root must be a canonical real directory")

    startup_path = root / ".local" / "two-site-refactor" / "autopilot" / "runs" / run_id / "preparation-startup.json"

    try:
        st = startup_path.lstat()
    except OSError:
        raise PreparationConfigError("Cannot access startup file") from None

    if not stat.S_ISREG(st.st_mode):
        raise PreparationConfigError("Startup file is not a regular file")

    if st.st_nlink != 1:
        raise PreparationConfigError("Startup file is hardlinked")

    if st.st_size > 4096:
        raise PreparationConfigError("Startup file exceeds maximum size")

    try:
        raw_bytes = bounded_file_bytes(root, startup_path, 4096, "preparation-startup.json")
    except (ProposalError, OSError):
        raise PreparationConfigError("Failed to read startup file") from None

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeError:
        raise PreparationConfigError("Startup file is not valid UTF-8") from None

    try:
        raw_mapping = unique_json(raw_text)
    except (ProposalError, json.JSONDecodeError, ValueError):
        raise PreparationConfigError("Startup file contains invalid JSON") from None

    try:
        startup = PreparationStartup.from_mapping(raw_mapping)
    except PreparationConfigError:
        raise PreparationConfigError("Invalid startup data") from None

    canonical_bytes = json.dumps(
        startup.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")

    if raw_bytes != canonical_bytes:
        raise PreparationConfigError("Startup file is not canonically formatted")

    if startup.fingerprint != sha:
        raise PreparationConfigError("Startup fingerprint mismatch")

    if startup.cycle_id != run_id:
        raise PreparationConfigError("Startup cycle ID mismatch")

    try:
        git_head = git(root, "rev-parse", "HEAD")
    except (ProposalError, subprocess.SubprocessError, OSError):
        raise PreparationConfigError("Failed to resolve git HEAD") from None

    if git_head != startup.source_head:
        raise PreparationConfigError("Git HEAD mismatch")

    try:
        scope = load_active_assignment_scope(root)
    except (AssignmentScopeError, OSError):
        raise PreparationConfigError("Failed to load active assignment scope") from None

    if type(scope) is not AssignmentScope:
        raise PreparationConfigError("Invalid assignment scope type")

    if scope.sha256 != startup.assignment_sha256:
        raise PreparationConfigError("Assignment scope digest mismatch")

    return startup


def verify_preparation_startup(
    root: Path,
    expected: PreparationStartup | None,
    environment=None,
) -> None:
    """Verify that reloaded startup identity matches expected without drift."""
    if not isinstance(root, Path) or not root.is_absolute():
        raise PreparationConfigError("root must be an absolute Path")

    if expected is not None and type(expected) is not PreparationStartup:
        raise PreparationConfigError("expected must be None or an exact PreparationStartup instance")

    observed = load_preparation_startup(root, environment=environment)

    if expected is None:
        if observed is not None:
            raise PreparationConfigError("Expected unbound startup but observed bound startup")
        return None

    if observed is None:
        raise PreparationConfigError("Expected bound startup but observed unbound startup")

    assert_same_startup(expected, observed)
    if expected.fingerprint != observed.fingerprint:
        raise PreparationConfigError("Startup identity drift detected")
    return None


def emit_preparation_telemetry(
    startup,
    *,
    outcome,
    elapsed_seconds=None,
    source_file_count=None,
    source_bytes=None,
    task_bytes=None,
    issue_count=None,
) -> bool:
    """Emit single-line bounded source-free telemetry to existing stderr capture."""
    try:
        data = preparation_telemetry(
            startup,
            outcome=outcome,
            elapsed_seconds=elapsed_seconds,
            source_file_count=source_file_count,
            source_bytes=source_bytes,
            task_bytes=task_bytes,
            issue_count=issue_count,
        )
        serialized = json.dumps(
            data,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        line = "AE01_PREPARATION_TELEMETRY_JSON:" + serialized + "\n"
        encoded = line.encode("ascii")
        if len(encoded) > 4096:
            return False
        written = sys.stderr.write(line)
        if written != len(line):
            return False
        return True
    except Exception:
        return False
