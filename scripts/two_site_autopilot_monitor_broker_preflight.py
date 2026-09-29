#!/usr/bin/env python3
"""Read-only autopilot monitor broker preflight validation helper for live broker recovery."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys

from scripts.two_site_codex_autopilot import (
    AutopilotError,
    _accepted_checkpoint_matches_repository,
    _load_accepted_checkpoint,
    validate_repository,
)

STATE_ROOT_REL = Path(".local/two-site-refactor/autopilot")
STOP_FILENAME = "STOP"


def run_broker_preflight(root: Path) -> tuple[bool, str]:
    """Execute bounded read-only preflight checks for live broker recovery without supervisor lock."""
    if not isinstance(root, Path):
        try:
            root = Path(root)
        except Exception:
            return False, "INVALID_ROOT"

    if not root.is_absolute():
        return False, "INVALID_ROOT"

    try:
        st_root = os.lstat(root)
        if stat.S_ISLNK(st_root.st_mode) or not stat.S_ISDIR(st_root.st_mode):
            return False, "INVALID_ROOT"
        resolved_root = root.resolve(strict=True)
        if resolved_root != root or str(resolved_root) != str(root):
            return False, "INVALID_ROOT"
    except Exception:
        return False, "INVALID_ROOT"

    state_root = root / STATE_ROOT_REL
    curr = root
    for part in STATE_ROOT_REL.parts:
        curr = curr / part
        if not os.path.lexists(curr):
            return False, "STATE_ROOT_UNSAFE"
        try:
            st_part = os.lstat(curr)
        except OSError:
            return False, "STATE_ROOT_UNSAFE"
        if stat.S_ISLNK(st_part.st_mode) or not stat.S_ISDIR(st_part.st_mode):
            return False, "STATE_ROOT_UNSAFE"

    try:
        st = os.lstat(state_root)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False, "STATE_ROOT_UNSAFE"
        if st.st_uid != os.geteuid() or (st.st_mode & 0o777) != 0o700:
            return False, "STATE_ROOT_UNSAFE"
        if state_root.resolve(strict=True) != state_root:
            return False, "STATE_ROOT_UNSAFE"
    except Exception:
        return False, "STATE_ROOT_UNSAFE"

    stop_state = state_root / STOP_FILENAME
    stop_root = root / STOP_FILENAME
    if os.path.lexists(stop_state) or os.path.lexists(stop_root):
        return False, "STOP_PRESENT"

    try:
        repo = validate_repository(root, require_clean=True)
    except AutopilotError:
        return False, "REPOSITORY_INVALID"
    except Exception:
        return False, "REPOSITORY_INVALID"

    try:
        checkpoint = _load_accepted_checkpoint(state_root)
        if checkpoint is None or not _accepted_checkpoint_matches_repository(checkpoint, repo):
            return False, "STALE_CHECKPOINT"
    except AutopilotError:
        return False, "STALE_CHECKPOINT"
    except Exception:
        return False, "STALE_CHECKPOINT"

    # Recheck STOP, full repository equality, and checkpoint immediately before READY
    if os.path.lexists(stop_state) or os.path.lexists(stop_root):
        return False, "STOP_PRESENT"

    try:
        repo_recheck = validate_repository(root, require_clean=True)
        if repo_recheck != repo:
            return False, "REPOSITORY_DRIFT"
    except AutopilotError:
        return False, "REPOSITORY_DRIFT"
    except Exception:
        return False, "REPOSITORY_DRIFT"

    try:
        cp_recheck = _load_accepted_checkpoint(state_root)
        if cp_recheck is None or not _accepted_checkpoint_matches_repository(cp_recheck, repo_recheck):
            return False, "STALE_CHECKPOINT"
    except AutopilotError:
        return False, "STALE_CHECKPOINT"
    except Exception:
        return False, "STALE_CHECKPOINT"

    return True, "READY"


run_preflight = run_broker_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="two_site_autopilot_monitor_broker_preflight",
        description="Two-site autopilot monitor broker preflight verification.",
    )
    parser.add_argument("--root", required=True, type=Path, help="Canonical workspace root directory.")
    args = parser.parse_args(argv)

    try:
        ok, reason = run_broker_preflight(args.root)
    except Exception:
        ok, reason = False, "PREFLIGHT_ERROR"

    sys.stdout.write(f"{reason}\n")
    sys.stdout.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
