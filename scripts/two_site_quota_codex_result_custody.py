"""Dormant read-only custody for untrusted Codex result candidates in cycle run directories.

Provides bounded, descriptor-relative, detached read custody of raw `result.json`
evidence inside existing pinned cycle run directories without granting result acceptance,
live execution, queue, lock, Provider, or wake/ack/replay authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

try:
    from scripts.two_site_proposal_runner import unique_json
except ImportError:
    from two_site_proposal_runner import unique_json  # type: ignore

try:
    from scripts.two_site_provider_broker import (
        BrokerError,
        CYCLE_RUN_RE,
        _open_existing_cycle_run_directory,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        BrokerError,
        CYCLE_RUN_RE,
        _open_existing_cycle_run_directory,
    )

CODEX_RESULT_FILENAME: str = "result.json"
MAX_CODEX_RESULT_BYTES: int = 64_000
_READ_LIMIT: int = MAX_CODEX_RESULT_BYTES + 1
_FORBIDDEN_MODE_BITS: int = (
    stat.S_IWGRP | stat.S_IWOTH | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
)

__all__ = [
    "CODEX_RESULT_FILENAME",
    "MAX_CODEX_RESULT_BYTES",
    "CodexResultCustodyError",
    "ResultCustodyError",
    "read_untrusted_codex_result_candidate",
]


class CodexResultCustodyError(Exception):
    """Raised when Codex result custody reading, validation, or verification fails."""


ResultCustodyError = CodexResultCustodyError


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
        raise CodexResultCustodyError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise CodexResultCustodyError(f"{label} owner mismatch")
    if info.st_nlink != 1:
        raise CodexResultCustodyError(f"{label} has invalid link count")
    mode = stat.S_IMODE(info.st_mode)
    if not (mode & stat.S_IRUSR):
        raise CodexResultCustodyError(f"{label} is not readable by owner")
    if mode & _FORBIDDEN_MODE_BITS:
        raise CodexResultCustodyError(f"{label} has forbidden permission bits: {oct(mode)}")
    if info.st_size == 0:
        raise CodexResultCustodyError(f"{label} is empty")
    if info.st_size < 0 or info.st_size > MAX_CODEX_RESULT_BYTES:
        raise CodexResultCustodyError(
            f"{label} exceeds byte limit ({info.st_size} > {MAX_CODEX_RESULT_BYTES})"
        )


def read_untrusted_codex_result_candidate(
    root: Path,
    continuation_controller_run_id: str,
) -> tuple[bytes, str, dict[str, Any]]:
    """Read untrusted Codex result candidate from cycle run directory.

    Dormant read-only custody. Does NOT validate semantic result acceptance or grant
    lock, Provider, execution, ack, wake, replay, or cleanup authority.
    """
    if not isinstance(root, Path):
        raise CodexResultCustodyError("root must be a Path")
    if type(continuation_controller_run_id) is not str:
        raise CodexResultCustodyError(
            f"continuation_controller_run_id must be exact str, got {type(continuation_controller_run_id).__name__}"
        )
    if not continuation_controller_run_id:
        raise CodexResultCustodyError("continuation_controller_run_id must be a non-empty string")
    if not CYCLE_RUN_RE.fullmatch(continuation_controller_run_id):
        raise CodexResultCustodyError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )

    target_name = CODEX_RESULT_FILENAME

    try:
        with _open_existing_cycle_run_directory(
            root, continuation_controller_run_id
        ) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise CodexResultCustodyError(
                    f"directory identity verification failed before read: {exc}"
                ) from exc

            file_path = run_path / target_name

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise CodexResultCustodyError(f"cannot lstat {target_name}: {exc}") from exc
            _validate_stat(lstat_before, f"{target_name} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise CodexResultCustodyError(
                    f"cannot stat {target_name} via dir_fd before open: {exc}"
                ) from exc
            _validate_stat(dirfd_stat_before, f"{target_name} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise CodexResultCustodyError(f"{target_name} dir_fd stat mismatch before open")

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            file_fd: int | None = None
            try:
                try:
                    file_fd = os.open(target_name, flags, dir_fd=run_fd)
                except OSError as exc:
                    raise CodexResultCustodyError(f"cannot safely open {target_name}: {exc}") from exc

                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise CodexResultCustodyError(
                        f"cannot fstat {target_name} before read: {exc}"
                    ) from exc
                _validate_stat(fstat_before, f"{target_name} descriptor before read")
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise CodexResultCustodyError(
                        f"{target_name} descriptor metadata mismatch before read"
                    )

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise CodexResultCustodyError(f"cannot read {target_name}: {exc}") from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_CODEX_RESULT_BYTES:
                    raise CodexResultCustodyError(
                        f"{target_name} exceeds byte limit ({len(data)} > {MAX_CODEX_RESULT_BYTES})"
                    )
                if len(data) == 0:
                    raise CodexResultCustodyError(f"{target_name} is empty")
                if len(data) != expected_size:
                    raise CodexResultCustodyError(
                        f"{target_name} read size mismatch ({len(data)} != {expected_size})"
                    )

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise CodexResultCustodyError(
                        f"cannot fstat {target_name} after read: {exc}"
                    ) from exc
                _validate_stat(fstat_after, f"{target_name} descriptor after read")
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise CodexResultCustodyError(
                        f"{target_name} descriptor metadata changed after read"
                    )

                try:
                    dirfd_stat_after = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                except OSError as exc:
                    raise CodexResultCustodyError(
                        f"cannot stat {target_name} via dir_fd after read: {exc}"
                    ) from exc
                _validate_stat(dirfd_stat_after, f"{target_name} via dir_fd after read")
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise CodexResultCustodyError(
                        f"{target_name} dir_fd metadata changed after read"
                    )

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise CodexResultCustodyError(
                        f"cannot lstat {target_name} after read: {exc}"
                    ) from exc
                _validate_stat(lstat_after, f"{target_name} path after read")
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise CodexResultCustodyError(
                        f"{target_name} path metadata changed after read"
                    )

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise CodexResultCustodyError(
                        f"directory identity verification failed after read: {exc}"
                    ) from exc

                try:
                    decoded = data.decode("utf-8")
                except (KeyboardInterrupt, SystemExit):
                    raise
                except UnicodeDecodeError as exc:
                    raise CodexResultCustodyError(f"{target_name} is not valid UTF-8: {exc}") from exc

                try:
                    payload = unique_json(decoded)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except (json.JSONDecodeError, ValueError) as exc:
                    raise CodexResultCustodyError(f"{target_name} is not valid JSON: {exc}") from exc
                except Exception as exc:
                    raise CodexResultCustodyError(f"{target_name} is not valid JSON: {exc}") from exc

                if not isinstance(payload, dict) or type(payload) is not dict:
                    raise CodexResultCustodyError(
                        f"{target_name} must be a JSON object, got {type(payload).__name__}"
                    )

                raw_bytes = bytes(data)
                exact_sha256 = hashlib.sha256(raw_bytes).hexdigest()
                return raw_bytes, exact_sha256, copy.deepcopy(payload)
            finally:
                if file_fd is not None:
                    prior_exc = sys.exc_info()[1]
                    try:
                        os.close(file_fd)
                    except OSError as exc:
                        if prior_exc is None:
                            raise CodexResultCustodyError(
                                f"cannot safely close {target_name} descriptor: {exc}"
                            ) from exc
    except CodexResultCustodyError:
        raise
    except BrokerError as exc:
        raise CodexResultCustodyError(f"cannot safely open cycle run directory: {exc}") from exc
