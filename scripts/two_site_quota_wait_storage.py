from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

try:
    from scripts.proposal_runner import unique_json
except ImportError:
    try:
        from proposal_runner import unique_json  # type: ignore
    except ImportError:
        try:
            from scripts.two_site_proposal_runner import unique_json  # type: ignore
        except ImportError:
            from two_site_proposal_runner import unique_json  # type: ignore

try:
    from scripts.two_site_provider_broker import (
        BrokerError,
        _open_existing_cycle_run_directory,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        BrokerError,
        _open_existing_cycle_run_directory,
    )

QUOTA_WAIT_FILENAME: str = "quota-wait.json"
MAX_WAIT_RECORD_BYTES: int = 16384
_READ_LIMIT: int = 16385


class WaitStorageError(Exception):
    """Raised when quota wait storage reading or validation fails."""


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _validate_stat(info: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WaitStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise WaitStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise WaitStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise WaitStorageError(f"{label} has invalid link count")
    if info.st_size < 0 or info.st_size > MAX_WAIT_RECORD_BYTES:
        raise WaitStorageError(f"{label} exceeds byte limit")


def read_initial_wait_record_bytes(root: Path, controller_run_id: str) -> bytes:
    if not isinstance(root, Path):
        raise WaitStorageError("root must be a Path")
    if not isinstance(controller_run_id, str) or not controller_run_id:
        raise WaitStorageError("controller_run_id must be a non-empty string")

    try:
        with _open_existing_cycle_run_directory(root, controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise WaitStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / QUOTA_WAIT_FILENAME

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise WaitStorageError(f"cannot lstat {QUOTA_WAIT_FILENAME}: {exc}") from exc
            _validate_stat(lstat_before, f"{QUOTA_WAIT_FILENAME} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(QUOTA_WAIT_FILENAME, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise WaitStorageError(f"cannot stat {QUOTA_WAIT_FILENAME} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{QUOTA_WAIT_FILENAME} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} dir_fd stat mismatch before open")

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                file_fd = os.open(QUOTA_WAIT_FILENAME, flags, dir_fd=run_fd)
            except OSError as exc:
                raise WaitStorageError(f"cannot safely open {QUOTA_WAIT_FILENAME}: {exc}") from exc

            try:
                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot fstat {QUOTA_WAIT_FILENAME} before read: {exc}") from exc
                _validate_stat(fstat_before, f"{QUOTA_WAIT_FILENAME} descriptor before read")
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} descriptor metadata mismatch before read")

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot read {QUOTA_WAIT_FILENAME}: {exc}") from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_WAIT_RECORD_BYTES:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} exceeds byte limit")
                if len(data) != expected_size:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} read size mismatch")

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot fstat {QUOTA_WAIT_FILENAME} after read: {exc}") from exc
                _validate_stat(fstat_after, f"{QUOTA_WAIT_FILENAME} descriptor after read")
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} descriptor metadata changed after read")

                try:
                    dirfd_stat_after = os.stat(QUOTA_WAIT_FILENAME, dir_fd=run_fd, follow_symlinks=False)
                except OSError as exc:
                    raise WaitStorageError(f"cannot stat {QUOTA_WAIT_FILENAME} via dir_fd after read: {exc}") from exc
                _validate_stat(dirfd_stat_after, f"{QUOTA_WAIT_FILENAME} via dir_fd after read")
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} dir_fd metadata changed after read")

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise WaitStorageError(f"cannot lstat {QUOTA_WAIT_FILENAME} after read: {exc}") from exc
                _validate_stat(lstat_after, f"{QUOTA_WAIT_FILENAME} path after read")
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} path metadata changed after read")

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise WaitStorageError(f"directory identity verification failed after read: {exc}") from exc

                try:
                    decoded = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not valid UTF-8: {exc}") from exc

                try:
                    payload = unique_json(decoded)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not valid JSON: {exc}") from exc

                if not isinstance(payload, dict) or type(payload) is not dict:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} must be a JSON object")

                try:
                    canonical_text = json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    canonical_bytes = canonical_text.encode("utf-8")
                except (ValueError, TypeError) as exc:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} cannot be serialized to canonical JSON: {exc}") from exc

                if data != canonical_bytes:
                    raise WaitStorageError(f"{QUOTA_WAIT_FILENAME} is not canonical JSON bytes")

                return data
            finally:
                try:
                    os.close(file_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise WaitStorageError(f"cannot safely close {QUOTA_WAIT_FILENAME} descriptor: {exc}") from exc
    except BrokerError as exc:
        raise WaitStorageError(f"cannot safely open cycle run directory: {exc}") from exc
