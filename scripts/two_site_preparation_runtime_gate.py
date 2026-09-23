"""Bounded read-only preparation runtime gate (AE01-40).

Validates broker status, empty queues, STOP file absence, and executes
accepted preparation boundary proof while holding client and runner locks.

PRECONDITION:
Caller ALREADY HOLDS the existing exclusive supervisor lock
(scripts.two_site_codex_autopilot.exclusive_supervisor_lock).
This function does not reacquire supervisor lock.
This module is UNWIRED; Supervisor CLI wiring is dependent later work.
Standalone and package import compatibility supported.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any

if __name__.startswith("scripts."):
    from scripts import two_site_provider_broker as broker
    from scripts.two_site_preparation_boundary import validate_resolved_preparation_boundary
else:
    try:
        import two_site_provider_broker as broker
        from two_site_preparation_boundary import validate_resolved_preparation_boundary
    except ImportError:
        from scripts import two_site_provider_broker as broker
        from scripts.two_site_preparation_boundary import validate_resolved_preparation_boundary

__all__ = ["validate_legacy_preparation_startup"]


def _get_autopilot():
    if __name__.startswith("scripts."):
        if "scripts.two_site_codex_autopilot" in sys.modules:
            return sys.modules["scripts.two_site_codex_autopilot"]
        from scripts import two_site_codex_autopilot as ap
        return ap
    if "two_site_codex_autopilot" in sys.modules:
        return sys.modules["two_site_codex_autopilot"]
    try:
        import two_site_codex_autopilot as ap
        return ap
    except ImportError:
        from scripts import two_site_codex_autopilot as ap
        return ap


def validate_legacy_preparation_startup(root: Path, state_root: Path) -> None:
    """Validate broker and boundary conditions before legacy preparation startup.

    Precondition:
        Caller must already hold exclusive supervisor lock.
    """
    ap = _get_autopilot()

    if not isinstance(root, Path) or not isinstance(state_root, Path):
        raise ap.AutopilotError("root and state_root must be Path instances")

    if not root.is_absolute():
        raise ap.AutopilotError("root must be an absolute path")

    try:
        if not root.is_dir() or root.resolve() != root or os.path.islink(root):
            raise ap.AutopilotError("root must be an existing canonical directory")
    except (OSError, ValueError):
        raise ap.AutopilotError("root path verification failed")

    expected_state_root = root / ".local" / "two-site-refactor" / "autopilot"
    if state_root != expected_state_root:
        raise ap.AutopilotError("state_root does not match expected exact path")

    if not state_root.is_absolute():
        raise ap.AutopilotError("state_root must be an absolute path")

    try:
        if not state_root.is_dir() or state_root.resolve() != state_root or os.path.islink(state_root):
            raise ap.AutopilotError("state_root must be an existing canonical directory")
    except (OSError, ValueError):
        raise ap.AutopilotError("state_root path verification failed")

    stop_path = state_root / "STOP"
    try:
        if os.path.lexists(stop_path):
            raise ap.AutopilotError("STOP path present")
    except (OSError, ValueError):
        raise ap.AutopilotError("STOP path verification failed")

    broker_dir = root / broker.STATE_REL
    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"

    for d_path, d_name in ((broker_dir, "broker directory"), (proposals_dir, "proposals directory")):
        try:
            st = os.lstat(d_path)
        except OSError:
            raise ap.AutopilotError(f"{d_name} is missing or unreadable")
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
            raise ap.AutopilotError(f"{d_name} is not a trusted regular directory")
        try:
            if d_path.resolve() != d_path:
                raise ap.AutopilotError(f"{d_name} path is not canonical")
        except (OSError, ValueError):
            raise ap.AutopilotError(f"{d_name} path verification failed")

    client_lock_path = broker_dir / "client.lock"
    runner_lock_path = proposals_dir / ".runner.lock"
    proposal_err = getattr(broker, "ProposalError", Exception)

    try:
        with broker.exclusive_broker_lock(client_lock_path):
            with broker.exclusive_broker_lock(runner_lock_path):
                for q_name in ("requests", "claimed", "responses"):
                    q_dir = broker_dir / q_name
                    try:
                        st = os.lstat(q_dir)
                    except OSError:
                        raise ap.AutopilotError(f"queue directory {q_name} is missing or unreadable")
                    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid():
                        raise ap.AutopilotError(f"queue directory {q_name} is not a trusted regular directory")
                    try:
                        if q_dir.resolve() != q_dir:
                            raise ap.AutopilotError(f"queue directory {q_name} path is not canonical")
                    except (OSError, ValueError):
                        raise ap.AutopilotError(f"queue directory {q_name} path verification failed")

                    try:
                        with os.scandir(q_dir) as it:
                            first_entry = next(it, None)
                            if first_entry is not None:
                                raise ap.AutopilotError(f"queue directory {q_name} is not empty")
                    except ap.AutopilotError:
                        raise
                    except OSError:
                        raise ap.AutopilotError(f"cannot inspect queue directory {q_name}")

                status_path = broker_dir / "status.json"
                try:
                    st = os.lstat(status_path)
                except OSError:
                    raise ap.AutopilotError("broker status is missing or unreadable")

                if stat.S_ISLNK(st.st_mode) or os.path.islink(status_path):
                    raise ap.AutopilotError("broker status is a symlink")
                if not stat.S_ISREG(st.st_mode):
                    raise ap.AutopilotError("broker status is not a regular file")
                if st.st_nlink != 1:
                    raise ap.AutopilotError("broker status has multiple hard links")
                if st.st_size > broker.MAX_REQUEST_BYTES:
                    raise ap.AutopilotError("broker status exceeds maximum allowed size")
                try:
                    if status_path.resolve() != status_path:
                        raise ap.AutopilotError("broker status path is not canonical")
                except (OSError, ValueError):
                    raise ap.AutopilotError("broker status path is not canonical")

                try:
                    status_data = broker._read_broker_status(root, status_path)
                except ap.AutopilotError:
                    raise
                except (broker.BrokerError, proposal_err, OSError):
                    raise ap.AutopilotError("broker status verification failed") from None

                if not isinstance(status_data, dict):
                    raise ap.AutopilotError("broker status content is not a JSON object")

                if status_data.get("state") != "IDLE":
                    raise ap.AutopilotError("broker status state is not IDLE")

                updated_at = status_data.get("updated_at")
                if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
                    raise ap.AutopilotError("broker status updated_at must be an exact int or float")

                if isinstance(updated_at, float):
                    if not math.isfinite(updated_at):
                        raise ap.AutopilotError("broker status updated_at float is not finite")

                now = time.time()
                min_time = now - broker.STATUS_MAX_AGE_SECONDS
                try:
                    if updated_at > now:
                        raise ap.AutopilotError("broker status updated_at is in the future")
                    if updated_at < min_time:
                        raise ap.AutopilotError("broker status is stale")
                except OverflowError:
                    raise ap.AutopilotError("broker status updated_at numeric overflow")

                validate_resolved_preparation_boundary(root, state_root)
    except ap.AutopilotError:
        raise
    except (broker.BrokerError, proposal_err, OSError):
        raise ap.AutopilotError("preparation runtime gate verification failed") from None
