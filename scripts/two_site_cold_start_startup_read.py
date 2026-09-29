#!/usr/bin/env python3
"""Dormant read-only initial terminal-epoch cold-adoption startup gate under AE03.

Precondition: Caller MUST already hold the exclusive Supervisor lock.
This module does NOT acquire or verify the Supervisor lock.

Authority and boundary constraints:
1. Dormant and read-only: contains NO publication, NO startup hook,
   NO Broker/Provider execution, NO wake, NO retry, and NO replay authority.
2. Does NOT authorize startup; this is a narrow startup read gate, not the
   complete Supervisor startup lifecycle.
3. Zero mutation: strictly read-only audit. Does not create, modify, chmod,
   or delete any state, queue, or adoption record (normal existing lock flock/fchmod
   on client.lock by the Broker lock context is preserved), and does not edit
   Git/Assignment/STOP/status/queues.
4. Fail-closed: validates canonical absolute root and state_root, verifies no STOP
   condition before and after read, runs bounded pending artifact scan before and
   after read, and holds Broker client lock across all operations.
5. Preserves Broker quiescence post-body ambiguity errors and process interrupts.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import stat
from typing import Any

try:
    from scripts.two_site_cold_start_adoption_storage import (
        ColdStartAdoptionStorageError,
        read_terminal_adoption_record_locked,
    )
except ImportError:
    from two_site_cold_start_adoption_storage import (  # type: ignore[no-redef]
        ColdStartAdoptionStorageError,
        read_terminal_adoption_record_locked,
    )

try:
    from scripts.two_site_cold_start_pending_scan import (
        assert_no_pending_cold_adoption_artifacts_locked,
    )
except ImportError:
    from two_site_cold_start_pending_scan import (  # type: ignore[no-redef]
        assert_no_pending_cold_adoption_artifacts_locked,
    )

try:
    from scripts.two_site_cold_start_broker_quiescence import (
        hold_idle_broker_client_lock_for_cold_adoption,
    )
except ImportError:
    from two_site_cold_start_broker_quiescence import (  # type: ignore[no-redef]
        hold_idle_broker_client_lock_for_cold_adoption,
    )

EXPECTED_STATE_REL: Path = Path(".local") / "two-site-refactor" / "autopilot"
OLD_EPOCH_BASENAME_RE: re.Pattern[str] = re.compile(r"^rescope-[0-9a-f]{64}$")


# Fixed nonsecret error messages under AE03 cold startup contract.
class ColdStartStartupReadError(Exception):
    """Raised when terminal cold-adoption startup read validation fails."""


__all__ = [
    "ColdStartStartupReadError",
    "require_terminal_cold_adoption_startup_locked",
]


def _validate_root_and_state_root(root: Path, state_root: Path) -> None:
    if not isinstance(root, Path):
        raise ColdStartStartupReadError("root must be a pathlib.Path instance")
    if not root.is_absolute():
        raise ColdStartStartupReadError("root must be an absolute path")
    root_str = str(root)
    if root_str != os.path.normpath(root_str):
        raise ColdStartStartupReadError("root must be canonical without redundant segments")

    try:
        st_root = os.lstat(root)
    except FileNotFoundError:
        raise ColdStartStartupReadError("root directory does not exist") from None
    except OSError:
        raise ColdStartStartupReadError("Failed to stat root") from None

    if stat.S_ISLNK(st_root.st_mode):
        raise ColdStartStartupReadError("root must not be a symlink")
    if not stat.S_ISDIR(st_root.st_mode):
        raise ColdStartStartupReadError("root must be a directory")

    try:
        real_root = root.resolve(strict=True)
    except OSError:
        raise ColdStartStartupReadError("Failed to resolve root") from None
    if real_root != root:
        raise ColdStartStartupReadError("root contains symlinks")

    if not isinstance(state_root, Path):
        raise ColdStartStartupReadError("state_root must be a pathlib.Path instance")

    expected_state_root = root / EXPECTED_STATE_REL
    if state_root != expected_state_root:
        raise ColdStartStartupReadError("state_root does not match expected relative path")

    if not state_root.is_absolute():
        raise ColdStartStartupReadError("state_root must be an absolute path")
    state_root_str = str(state_root)
    if state_root_str != os.path.normpath(state_root_str):
        raise ColdStartStartupReadError("state_root must be canonical without redundant segments")

    try:
        st_state = os.lstat(state_root)
    except FileNotFoundError:
        raise ColdStartStartupReadError("state_root directory does not exist") from None
    except OSError:
        raise ColdStartStartupReadError("Failed to stat state_root") from None

    if stat.S_ISLNK(st_state.st_mode):
        raise ColdStartStartupReadError("state_root must not be a symlink")
    if not stat.S_ISDIR(st_state.st_mode):
        raise ColdStartStartupReadError("state_root must be a directory")
    if st_state.st_uid != os.geteuid():
        raise ColdStartStartupReadError("state_root owner mismatch")
    if (st_state.st_mode & 0o777) != 0o700 or (st_state.st_mode & 0o7000) != 0:
        raise ColdStartStartupReadError("state_root mode must be 0700")

    try:
        real_state = state_root.resolve(strict=True)
    except OSError:
        raise ColdStartStartupReadError("Failed to resolve state_root") from None
    if real_state != state_root:
        raise ColdStartStartupReadError("state_root contains symlinks")


def _check_no_stop_lexists(root: Path, state_root: Path) -> None:
    for candidate in (state_root / "STOP", root / "STOP"):
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        except (KeyboardInterrupt, SystemExit):
            raise
        except OSError:
            raise ColdStartStartupReadError("Failed to check STOP file") from None
        raise ColdStartStartupReadError("STOP file detected")


def require_terminal_cold_adoption_startup_locked(
    root: Path,
    state_root: Path,
    new_repository: dict[str, Any],
    expected_old_epoch_basename: str,
) -> dict[str, Any]:
    """Read and validate the terminal cold-adoption record during locked cold startup.

    Precondition: Caller MUST already hold the exclusive Supervisor lock.
    This function does NOT acquire or verify that lock and grants NO startup,
    publication, Provider, wake, retry, or replay authority.
    """
    _validate_root_and_state_root(root, state_root)

    if type(new_repository) is not dict:
        raise ColdStartStartupReadError("new_repository must be a dict")

    if (
        type(expected_old_epoch_basename) is not str
        or not OLD_EPOCH_BASENAME_RE.fullmatch(expected_old_epoch_basename)
    ):
        raise ColdStartStartupReadError(
            "expected_old_epoch_basename must match rescope-[0-9a-f]{64}"
        )

    with hold_idle_broker_client_lock_for_cold_adoption(root):
        _check_no_stop_lexists(root, state_root)
        assert_no_pending_cold_adoption_artifacts_locked(state_root)

        try:
            record = read_terminal_adoption_record_locked(state_root, new_repository)
        except (KeyboardInterrupt, SystemExit):
            raise
        except ColdStartAdoptionStorageError:
            raise ColdStartStartupReadError("Adoption record read failed") from None
        except Exception:
            raise ColdStartStartupReadError("Adoption record read failed") from None

        _check_no_stop_lexists(root, state_root)
        assert_no_pending_cold_adoption_artifacts_locked(state_root)

        if record is None:
            raise ColdStartStartupReadError("Adoption record missing")
        if type(record) is not dict:
            raise ColdStartStartupReadError("Adoption record is not a dict")
        if record.get("old_epoch_basename") != expected_old_epoch_basename:
            raise ColdStartStartupReadError("Adoption record old epoch mismatch")

    return copy.deepcopy(record)
