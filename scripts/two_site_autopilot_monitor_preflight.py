#!/usr/bin/env python3
"""Read-only autopilot monitor preflight validation helper."""
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
    _RESCOPE_INTENT_FILENAMES,
    _RESCOPE_RECEIPT_FILENAMES,
    exclusive_supervisor_lock,
    load_latest_rescope_receipt,
    rescope_epoch_basename,
    rescope_intent_exists,
    validate_repository,
    validate_rescope_epoch_startup,
)
from scripts.two_site_proposal_runner import bounded_file_text, unique_json

STATE_ROOT_REL = Path(".local/two-site-refactor/autopilot")
LOCK_FILENAME = "autopilot.lock"
STATUS_FILENAME = "status.json"
STOP_FILENAME = "STOP"
MAX_STATUS_BYTES = 65536
MAX_EPOCH_LEAVES = 16
ELIGIBLE_STATUS_STATES = frozenset({"CONTINUE", "RUNNING", "RETRYABLE_FAILURE"})
KNOWN_EPOCH_LEAVES = frozenset(
    set(_RESCOPE_RECEIPT_FILENAMES.keys()) | set(_RESCOPE_INTENT_FILENAMES.values())
)


def run_preflight(root: Path) -> tuple[bool, str]:
    """Execute bounded read-only preflight checks and return (status_ok, reason_code)."""
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

    lock_path = state_root / LOCK_FILENAME
    try:
        with exclusive_supervisor_lock(lock_path):
            stop_state = state_root / STOP_FILENAME
            stop_root = root / STOP_FILENAME
            if os.path.lexists(stop_state) or os.path.lexists(stop_root):
                return False, "STOP_PRESENT"

            try:
                repo = validate_repository(root, require_clean=True)
            except Exception:
                return False, "REPOSITORY_INVALID"

            try:
                checkpoint = _load_accepted_checkpoint(state_root)
                if checkpoint is None or not _accepted_checkpoint_matches_repository(checkpoint, repo):
                    from scripts.two_site_maintenance_transition_startup import (
                        require_reviewed_maintenance_transition_locked,
                    )
                    require_reviewed_maintenance_transition_locked(root, state_root, repo)
            except Exception:
                return False, "STALE_CHECKPOINT"

            status_path = state_root / STATUS_FILENAME
            try:
                if not status_path.is_file() or status_path.is_symlink():
                    return False, "STATUS_INVALID"
                st_status = status_path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(st_status.st_mode)
                    or st_status.st_nlink != 1
                    or st_status.st_uid != os.geteuid()
                    or st_status.st_size > MAX_STATUS_BYTES
                ):
                    return False, "STATUS_INVALID"
                raw_text = bounded_file_text(state_root, status_path, MAX_STATUS_BYTES, "status.json")
                status_data = unique_json(raw_text)
                if not isinstance(status_data, dict):
                    return False, "STATUS_INVALID"
                if status_data.get("head") != repo.get("head"):
                    return False, "STATUS_INVALID"
                state_val = status_data.get("state")
                if state_val not in ELIGIBLE_STATUS_STATES:
                    return False, "STATUS_INVALID"
            except Exception:
                return False, "STATUS_INVALID"

            try:
                validate_rescope_epoch_startup(state_root, repo, root=root)
            except Exception:
                return False, "CROSS_EPOCH_GATE_FAILED"

            try:
                current_epoch = rescope_epoch_basename(repo)
            except Exception:
                return False, "CROSS_EPOCH_GATE_FAILED"

            epoch_dir = state_root / current_epoch
            if os.path.lexists(epoch_dir):
                try:
                    st_epoch = os.lstat(epoch_dir)
                except OSError:
                    return False, "UNSAFE_LEAF"
                if stat.S_ISLNK(st_epoch.st_mode) or not stat.S_ISDIR(st_epoch.st_mode):
                    return False, "UNSAFE_LEAF"
                if st_epoch.st_uid != os.geteuid() or (st_epoch.st_mode & 0o777) != 0o700:
                    return False, "UNSAFE_LEAF"

                leaf_count = 0
                try:
                    with os.scandir(epoch_dir) as scanner:
                        for entry in scanner:
                            leaf_count += 1
                            if leaf_count > MAX_EPOCH_LEAVES:
                                return False, "UNSAFE_LEAF"
                            if entry.name not in KNOWN_EPOCH_LEAVES:
                                if "receipt" in entry.name:
                                    return False, "WRONG_RECEIPT_FILENAME"
                                return False, "UNSAFE_LEAF"
                            st_entry = entry.stat(follow_symlinks=False)
                            if (
                                not stat.S_ISREG(st_entry.st_mode)
                                or stat.S_ISLNK(st_entry.st_mode)
                                or st_entry.st_nlink != 1
                                or st_entry.st_uid != os.geteuid()
                                or (st_entry.st_mode & 0o777) != 0o600
                            ):
                                return False, "UNSAFE_LEAF"
                except Exception:
                    return False, "UNSAFE_LEAF"

                try:
                    latest_receipt = load_latest_rescope_receipt(epoch_dir, repo)
                except Exception:
                    return False, "RECEIPT_INVALID"

                if latest_receipt is not None:
                    if latest_receipt.get("terminal") is True or latest_receipt.get("attempt") == 3:
                        return False, "TERMINAL_RECEIPT"
                    latest_attempt = latest_receipt.get("attempt")
                    if not isinstance(latest_attempt, int) or latest_attempt not in (1, 2):
                        return False, "RECEIPT_INVALID"
                else:
                    latest_attempt = 0

                for att in (1, 2, 3):
                    try:
                        intent_present = rescope_intent_exists(epoch_dir, att)
                    except Exception:
                        return False, "UNRESOLVED_INTENT"
                    if att <= latest_attempt:
                        if not intent_present:
                            return False, "UNRESOLVED_INTENT"
                    else:
                        if intent_present:
                            return False, "UNRESOLVED_INTENT"

            # Recheck STOP, repo, checkpoint before READY
            if os.path.lexists(stop_state) or os.path.lexists(stop_root):
                return False, "STOP_PRESENT"
            try:
                repo_recheck = validate_repository(root, require_clean=True)
                if repo_recheck != repo:
                    return False, "REPOSITORY_DRIFT"
            except Exception:
                return False, "REPOSITORY_DRIFT"

            try:
                cp_recheck = _load_accepted_checkpoint(state_root)
                if cp_recheck is None or not _accepted_checkpoint_matches_repository(cp_recheck, repo_recheck):
                    from scripts.two_site_maintenance_transition_startup import (
                        require_reviewed_maintenance_transition_locked,
                    )
                    require_reviewed_maintenance_transition_locked(root, state_root, repo_recheck)
            except Exception:
                return False, "STALE_CHECKPOINT"

            return True, "READY"
    except AutopilotError as exc:
        if "lock" in str(exc).lower():
            return False, "SUPERVISOR_LOCKED"
        return False, "PREFLIGHT_ERROR"
    except Exception:
        return False, "PREFLIGHT_ERROR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="two_site_autopilot_monitor_preflight",
        description="Two-site autopilot monitor preflight verification.",
    )
    parser.add_argument("--root", required=True, type=Path, help="Canonical workspace root directory.")
    args = parser.parse_args(argv)

    try:
        ok, reason = run_preflight(args.root)
    except Exception:
        ok, reason = False, "PREFLIGHT_ERROR"

    sys.stdout.write(f"{reason}\n")
    sys.stdout.flush()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
