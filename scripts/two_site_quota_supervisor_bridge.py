"""Bridge between Supervisor cycle completion and durable quota wait admission."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Any

from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    canonical_root,
)
from scripts.two_site_quota_disposition_probe import (
    QuotaProbeError,
    probe_cycle_quota_receipt,
)
from scripts.two_site_quota_wait_admission import (
    WaitAdmissionError,
    admit_initial_quota_wait_locked,
)

__all__ = [
    "QuotaSupervisorBridgeError",
    "accept_cycle_quota_disposition_locked",
]

_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class QuotaSupervisorBridgeError(ValueError):
    """Raised when supervisor bridge operations fail or input is invalid."""


def accept_cycle_quota_disposition_locked(
    root: Path,
    run_dir: Path,
    result: object,
    *,
    enabled: bool = False,
) -> dict | None:
    """Evaluate completed cycle quota disposition and admit initial quota wait if gated.

    Precondition: Caller MUST already own the existing exclusive Supervisor lock.
    This bridge does NOT acquire another lock, call Provider, authorize replay,
    schedule wakeups, or wire runtime hooks.
    """
    if type(enabled) is not bool:
        raise QuotaSupervisorBridgeError("enabled must be an exact bool")
    if not enabled:
        return None

    if not isinstance(root, Path):
        raise QuotaSupervisorBridgeError("root must be a Path")
    if not isinstance(run_dir, Path):
        raise QuotaSupervisorBridgeError("run_dir must be a Path")
    if not isinstance(result, dict):
        raise QuotaSupervisorBridgeError("result must be a dict")

    try:
        c_root = canonical_root(root)
        if c_root != root:
            raise QuotaSupervisorBridgeError("root must be a canonical path")

        runs_dir = root / ".local" / "two-site-refactor" / "autopilot" / "runs"
        cid = run_dir.name
        if not cid or cid in {".", ".."}:
            raise QuotaSupervisorBridgeError("run_dir has invalid cycle run ID")
        if run_dir.parent != runs_dir or run_dir != runs_dir / cid:
            raise QuotaSupervisorBridgeError(
                "run_dir must be directly under root/.local/two-site-refactor/autopilot/runs"
            )

        with _open_existing_cycle_run_directory(root, cid) as (
            pinned_path,
            run_fd,
            verify_identity,
        ):
            if pinned_path != run_dir:
                raise QuotaSupervisorBridgeError(
                    "pinned path does not match supplied run_dir"
                )

            st_before = os.lstat(run_dir)
            if stat.S_ISLNK(st_before.st_mode) or not stat.S_ISDIR(st_before.st_mode):
                raise QuotaSupervisorBridgeError(
                    "run_dir must be a directory and not a symlink"
                )
            if st_before.st_uid != os.geteuid():
                raise QuotaSupervisorBridgeError("run_dir owner mismatch")
            if stat.S_IMODE(st_before.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("run_dir mode must be 0700")

            fd_stat = os.fstat(run_fd)
            if not stat.S_ISDIR(fd_stat.st_mode):
                raise QuotaSupervisorBridgeError("pinned descriptor is not a directory")
            if fd_stat.st_uid != os.geteuid():
                raise QuotaSupervisorBridgeError("pinned descriptor owner mismatch")
            if stat.S_IMODE(fd_stat.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("pinned descriptor mode must be 0700")
            if (st_before.st_dev, st_before.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                raise QuotaSupervisorBridgeError(
                    "run_dir stat does not match pinned descriptor inode"
                )

            verify_identity()

            st_after = os.lstat(run_dir)
            if stat.S_ISLNK(st_after.st_mode) or not stat.S_ISDIR(st_after.st_mode):
                raise QuotaSupervisorBridgeError(
                    "run_dir must remain a directory and not a symlink"
                )
            if st_after.st_uid != os.geteuid() or stat.S_IMODE(st_after.st_mode) != 0o700:
                raise QuotaSupervisorBridgeError("run_dir mode or owner changed")
            if (st_after.st_dev, st_after.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                raise QuotaSupervisorBridgeError(
                    "run_dir inode changed during verification"
                )

        disposition = probe_cycle_quota_receipt(root, cid)
        if disposition in ("NO_RESERVATION", "NO_TERMINAL_QUOTA"):
            return None

        if disposition != "TERMINAL_QUOTA":
            raise QuotaSupervisorBridgeError(
                f"unexpected quota disposition: {disposition}"
            )

        if result.get("state") != "BLOCKED":
            raise QuotaSupervisorBridgeError(
                "result state must be BLOCKED for terminal quota"
            )

        if result.get("head_changed") is not False:
            raise QuotaSupervisorBridgeError(
                "result head_changed must be exactly False for terminal quota"
            )

        if result.get("commit") is not None:
            raise QuotaSupervisorBridgeError(
                "result commit must be None for terminal quota"
            )

        head_before = result.get("head_before")
        head_after = result.get("head_after")
        if (
            not isinstance(head_before, str)
            or not isinstance(head_after, str)
            or not _HEX_SHA_RE.fullmatch(head_before)
            or head_before != head_after
        ):
            raise QuotaSupervisorBridgeError(
                "result head_before and head_after must be identical 40-character lowercase hex SHAs"
            )

        if (
            result.get("gemini_worker_run_id") is not None
            or result.get("gemini_reviewer_run_id") is not None
            or result.get("proposal_sha256") is not None
        ):
            raise QuotaSupervisorBridgeError(
                "worker run ID, reviewer run ID, and proposal SHA must be None for terminal quota"
            )

        wait_record = admit_initial_quota_wait_locked(root, cid)
        if (
            not isinstance(wait_record, dict)
            or wait_record.get("state") != "WAITING_QUOTA"
        ):
            raise QuotaSupervisorBridgeError(
                "admitted quota record is invalid or not in WAITING_QUOTA state"
            )

        return wait_record

    except (KeyboardInterrupt, SystemExit):
        raise
    except QuotaSupervisorBridgeError:
        raise
    except (BrokerError, QuotaProbeError, WaitAdmissionError, OSError) as exc:
        raise QuotaSupervisorBridgeError(str(exc)) from exc
