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


def publish_initial_wait_record_bytes(
    root: Path,
    controller_run_id: str,
    data: bytes,
) -> bytes:
    if not isinstance(root, Path):
        raise WaitStorageError("root must be a Path")
    if not isinstance(controller_run_id, str) or not controller_run_id:
        raise WaitStorageError("controller_run_id must be a non-empty string")
    if not isinstance(data, bytes) or type(data) is not bytes:
        raise WaitStorageError("data must be bytes")
    if len(data) > MAX_WAIT_RECORD_BYTES:
        raise WaitStorageError("data exceeds byte limit")

    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WaitStorageError(f"data is not valid UTF-8: {exc}") from exc

    try:
        payload = unique_json(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WaitStorageError(f"data is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or type(payload) is not dict:
        raise WaitStorageError("data must be a JSON object")

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
        raise WaitStorageError(f"data cannot be serialized to canonical JSON: {exc}") from exc

    if data != canonical_bytes:
        raise WaitStorageError("data is not canonical JSON bytes")

    temp_name = f".{QUOTA_WAIT_FILENAME}.tmp.{os.getpid()}"
    target_name = QUOTA_WAIT_FILENAME
    temp_created = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(root, controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise WaitStorageError(f"directory identity verification failed before write: {exc}") from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise WaitStorageError(f"cannot stat {target_name} via dir_fd: {exc}") from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise WaitStorageError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise WaitStorageError(f"{target_name} already exists")

            file_fd: int | None = None
            try:
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    file_fd = os.open(temp_name, flags, 0o600, dir_fd=run_fd)
                except OSError as exc:
                    raise WaitStorageError(f"cannot safely create temp file {temp_name}: {exc}") from exc
                temp_created = True

                try:
                    total_written = 0
                    data_len = len(data)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, data[total_written:])
                        except OSError as exc:
                            raise WaitStorageError(f"cannot write {temp_name}: {exc}") from exc
                        if written <= 0:
                            raise WaitStorageError(f"write stalled while writing {temp_name}")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot fchmod {temp_name}: {exc}") from exc

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise WaitStorageError(f"cannot fsync {temp_name}: {exc}") from exc
                finally:
                    try:
                        os.close(file_fd)
                    except OSError as exc:
                        file_fd = None
                        raise WaitStorageError(f"cannot safely close temp file {temp_name}: {exc}") from exc
                    file_fd = None

                try:
                    os.link(
                        temp_name,
                        target_name,
                        src_dir_fd=run_fd,
                        dst_dir_fd=run_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise WaitStorageError(f"cannot link {target_name}: target already exists") from exc
                except OSError as exc:
                    raise WaitStorageError(f"cannot link {target_name}: {exc}") from exc
                linked = True

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except OSError as exc:
                    raise WaitStorageError(f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}") from exc

                try:
                    os.fsync(run_fd)
                except OSError as exc:
                    raise WaitStorageError(f"ambiguous post-link publication failure: cannot fsync directory: {exc}") from exc

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise WaitStorageError(f"ambiguous post-link publication failure: directory identity verification failed after write: {exc}") from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
                if temp_created and not linked:
                    try:
                        os.unlink(temp_name, dir_fd=run_fd)
                    except OSError:
                        pass
    except BrokerError as exc:
        if linked:
            raise WaitStorageError(f"ambiguous post-link publication failure: cycle run directory error: {exc}") from exc
        raise WaitStorageError(f"cannot safely open cycle run directory: {exc}") from exc
    except OSError as exc:
        if linked:
            raise WaitStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise WaitStorageError(f"cannot safely access cycle run directory: {exc}") from exc

    try:
        readback_bytes = read_initial_wait_record_bytes(root, controller_run_id)
    except WaitStorageError as exc:
        raise WaitStorageError(f"ambiguous post-link publication failure: readback failed: {exc}") from exc
    except Exception as exc:
        raise WaitStorageError(f"ambiguous post-link publication failure: readback unexpected error: {exc}") from exc

    if readback_bytes != data:
        raise WaitStorageError("ambiguous post-link publication failure: readback data mismatch")

    return readback_bytes
