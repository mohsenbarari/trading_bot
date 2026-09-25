"""Strict read-only classification of completed Supervisor cycle quota disposition.

Caller MUST hold existing Supervisor exclusive lock. This module performs strictly
read-only inspection of pinned run directories, reservations, terminal quota records,
and Broker queue directories to classify the cycle's quota disposition into one of:
'NO_RESERVATION', 'NO_TERMINAL_QUOTA', or 'TERMINAL_QUOTA'.

This module does NOT publish wait, call Provider, authorize replay, or wire runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from scripts.two_site_provider_broker import (
    BrokerError,
    RESERVATION_KEYS,
    STATE_REL,
    _open_existing_cycle_run_directory,
    canonical_root,
    find_terminal_quota_for_reservation,
    read_cycle_reservation,
    read_terminal_quota_record,
    scan_private_record_directory,
)

__all__ = ["QuotaProbeError", "probe_cycle_quota_receipt"]


class QuotaProbeError(ValueError):
    """Raised when quota disposition probing fails or encounters ambiguity."""


def _canonical_json_bytes(data: Any) -> bytes:
    try:
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise QuotaProbeError(f"response cannot be canonically encoded: {exc}") from exc


def probe_cycle_quota_receipt(root: Path, controller_run_id: str) -> str:
    """Classify cycle quota disposition into NO_RESERVATION, NO_TERMINAL_QUOTA, or TERMINAL_QUOTA.

    Caller MUST hold existing exclusive Supervisor lock. Performs read-only inspection:
    1. Resolves canonical root via Broker canonical_root.
    2. Opens pinned cycle run directory via _open_existing_cycle_run_directory and stats
       broker-reservation.json no-follow via run fd. If FileNotFoundError, returns 'NO_RESERVATION'.
    3. Reads strict cycle reservation via read_cycle_reservation and validates request_id + 7 keys.
    4. Validates Broker state directory.
    5. Checks terminal-quota directory via lstat. If present, strictly scans directory and entries.
    6. If target request_id + '.json' exists in terminal-quota:
       Calls find_terminal_quota_for_reservation and read_terminal_quota_record.
       Compares canonical response bytes and returns 'TERMINAL_QUOTA'.
    7. If target is absent in terminal-quota (or terminal-quota is absent):
       Inspects requests, claimed, and responses queue directories using pinned no-follow descriptors.
       If target exists in any queue directory, raises QuotaProbeError('ambiguous in-flight or unread Broker response').
       If no target exists in any queue directory and no terminal receipt, returns 'NO_TERMINAL_QUOTA'.
    """
    if not isinstance(root, Path):
        raise QuotaProbeError("root must be a Path")
    if not isinstance(controller_run_id, str):
        raise QuotaProbeError("controller_run_id must be a string")

    try:
        c_root = canonical_root(root)
    except (BrokerError, OSError) as exc:
        raise QuotaProbeError(f"failed to resolve canonical root: {exc}") from exc

    try:
        with _open_existing_cycle_run_directory(c_root, controller_run_id) as (
            _run_path,
            run_fd,
            verify_identity,
        ):
            try:
                os.stat("broker-reservation.json", dir_fd=run_fd, follow_symlinks=False)
            except FileNotFoundError:
                return "NO_RESERVATION"
            except OSError as exc:
                raise QuotaProbeError(f"cannot stat broker-reservation.json: {exc}") from exc

            verify_identity()
    except (BrokerError, OSError) as exc:
        raise QuotaProbeError(f"failed to open cycle run directory: {exc}") from exc

    try:
        reservation_record = read_cycle_reservation(c_root, controller_run_id)
    except (BrokerError, OSError) as exc:
        raise QuotaProbeError(f"failed to read cycle reservation: {exc}") from exc

    if not isinstance(reservation_record, dict):
        raise QuotaProbeError("broker reservation record is malformed")

    request_id = reservation_record.get("request_id")
    if not isinstance(request_id, str):
        raise QuotaProbeError("broker reservation request_id is not a string")

    reservation = reservation_record.get("reservation")
    if not isinstance(reservation, dict):
        raise QuotaProbeError("broker reservation field 'reservation' is not a dict")

    if set(reservation) != RESERVATION_KEYS:
        raise QuotaProbeError("broker reservation does not match exact RESERVATION_KEYS contract")

    for key in RESERVATION_KEYS:
        val = reservation[key]
        if type(val) is not str:
            raise QuotaProbeError(f"reservation field '{key}' must be a string")

    state_root = c_root / STATE_REL
    try:
        resolved_state_root = state_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise QuotaProbeError(f"Broker state directory is missing or unavailable: {exc}") from exc
    if resolved_state_root != state_root:
        raise QuotaProbeError("Broker state directory must be a canonical path without symlinks")
    try:
        state_stat = os.lstat(state_root)
    except OSError as exc:
        raise QuotaProbeError(f"cannot lstat Broker state directory: {exc}") from exc
    if not stat.S_ISDIR(state_stat.st_mode) or stat.S_ISLNK(state_stat.st_mode):
        raise QuotaProbeError("Broker state directory must be a directory and not a symlink")
    if state_stat.st_uid != os.geteuid():
        raise QuotaProbeError("Broker state directory owner mismatch")
    if stat.S_IMODE(state_stat.st_mode) != 0o700:
        raise QuotaProbeError("Broker state directory mode must be 0700")

    terminal_quota_dir = state_root / "terminal-quota"
    target_basename = f"{request_id}.json"

    try:
        term_stat = os.lstat(terminal_quota_dir)
        term_dir_present = True
    except FileNotFoundError:
        term_dir_present = False
    except OSError as exc:
        raise QuotaProbeError(f"cannot lstat terminal-quota directory: {exc}") from exc

    target_in_terminal = False
    if term_dir_present:
        try:
            entries = scan_private_record_directory(terminal_quota_dir)
        except (BrokerError, OSError) as exc:
            raise QuotaProbeError(f"failed to scan terminal-quota directory: {exc}") from exc
        if target_basename in entries:
            target_in_terminal = True

    if target_in_terminal:
        try:
            find_response = find_terminal_quota_for_reservation(c_root, reservation)
            receipt = read_terminal_quota_record(c_root, request_id)
        except (BrokerError, OSError) as exc:
            raise QuotaProbeError(f"failed to read terminal quota receipt: {exc}") from exc

        if not isinstance(receipt, dict) or "response" not in receipt:
            raise QuotaProbeError("terminal quota receipt is invalid or missing 'response'")

        receipt_response = receipt["response"]
        find_bytes = _canonical_json_bytes(find_response)
        receipt_bytes = _canonical_json_bytes(receipt_response)
        if find_bytes != receipt_bytes or find_response != receipt_response:
            raise QuotaProbeError(
                "find-returned terminal quota response does not match receipt response"
            )
        return "TERMINAL_QUOTA"

    for q_name in ("requests", "claimed", "responses"):
        q_dir = state_root / q_name
        try:
            resolved_q = q_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise QuotaProbeError(
                f"required Broker queue directory '{q_name}' is missing or unavailable: {exc}"
            ) from exc
        if resolved_q != q_dir:
            raise QuotaProbeError(
                f"Broker queue directory '{q_name}' must be a canonical path without symlinks"
            )
        try:
            lstat_before = os.lstat(q_dir)
        except OSError as exc:
            raise QuotaProbeError(
                f"cannot lstat Broker queue directory '{q_name}': {exc}"
            ) from exc
        if not stat.S_ISDIR(lstat_before.st_mode) or stat.S_ISLNK(lstat_before.st_mode):
            raise QuotaProbeError(
                f"Broker queue directory '{q_name}' must be a directory and not a symlink"
            )
        if lstat_before.st_uid != os.geteuid():
            raise QuotaProbeError(
                f"Broker queue directory '{q_name}' is not owned by current euid"
            )
        if stat.S_IMODE(lstat_before.st_mode) != 0o700:
            raise QuotaProbeError(
                f"Broker queue directory '{q_name}' mode must be 0700"
            )

        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(q_dir, flags)
        except OSError as exc:
            raise QuotaProbeError(
                f"cannot safely open Broker queue directory '{q_name}': {exc}"
            ) from exc
        try:
            fd_stat = os.fstat(fd)
            if not stat.S_ISDIR(fd_stat.st_mode):
                raise QuotaProbeError(
                    f"Broker queue directory descriptor for '{q_name}' is not a directory"
                )
            if fd_stat.st_uid != os.geteuid():
                raise QuotaProbeError(
                    f"Broker queue directory descriptor for '{q_name}' owner mismatch"
                )
            if stat.S_IMODE(fd_stat.st_mode) != 0o700:
                raise QuotaProbeError(
                    f"Broker queue directory descriptor for '{q_name}' mode mismatch"
                )
            if (fd_stat.st_dev, fd_stat.st_ino) != (lstat_before.st_dev, lstat_before.st_ino):
                raise QuotaProbeError(
                    f"Broker queue directory descriptor for '{q_name}' does not match path before stat"
                )

            target_exists = False
            try:
                os.stat(target_basename, dir_fd=fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                target_exists = False
            except OSError as exc:
                raise QuotaProbeError(
                    f"cannot stat '{target_basename}' in '{q_name}': {exc}"
                ) from exc

            try:
                lstat_after = os.lstat(q_dir)
            except OSError as exc:
                raise QuotaProbeError(
                    f"cannot lstat Broker queue directory '{q_name}' after inspection: {exc}"
                ) from exc
            if not stat.S_ISDIR(lstat_after.st_mode) or stat.S_ISLNK(lstat_after.st_mode):
                raise QuotaProbeError(
                    f"Broker queue directory '{q_name}' changed or became symlink"
                )
            if lstat_after.st_uid != os.geteuid():
                raise QuotaProbeError(
                    f"Broker queue directory '{q_name}' owner changed"
                )
            if stat.S_IMODE(lstat_after.st_mode) != 0o700:
                raise QuotaProbeError(
                    f"Broker queue directory '{q_name}' mode changed"
                )
            if (lstat_after.st_dev, lstat_after.st_ino) != (fd_stat.st_dev, fd_stat.st_ino):
                raise QuotaProbeError(
                    f"Broker queue directory '{q_name}' identity changed across inspection"
                )

            if target_exists:
                raise QuotaProbeError("ambiguous in-flight or unread Broker response")
        finally:
            os.close(fd)

    return "NO_TERMINAL_QUOTA"
