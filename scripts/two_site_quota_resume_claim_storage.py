from __future__ import annotations

import copy
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

try:
    from scripts.two_site_quota_resume_claim_record import (
        ResumeClaimRecordError,
        _EXPECTED_KEYS,
        validate_known_reset_resume_claim,
    )
except ImportError:
    from two_site_quota_resume_claim_record import (  # type: ignore
        ResumeClaimRecordError,
        _EXPECTED_KEYS,
        validate_known_reset_resume_claim,
    )

QUOTA_RESUME_CLAIM_FILENAME: str = "quota-resume-claim.json"
MAX_CLAIM_RECORD_BYTES: int = 8192
_READ_LIMIT: int = 8193

__all__ = [
    "ClaimStorageError",
    "MAX_CLAIM_RECORD_BYTES",
    "QUOTA_RESUME_CLAIM_FILENAME",
    "read_known_reset_resume_claim",
]


class ClaimStorageError(Exception):
    """Raised when quota resume claim storage reading or validation fails."""


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
        raise ClaimStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise ClaimStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ClaimStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise ClaimStorageError(f"{label} has invalid link count")
    if info.st_size < 0 or info.st_size > MAX_CLAIM_RECORD_BYTES:
        raise ClaimStorageError(f"{label} exceeds byte limit")


def read_known_reset_resume_claim(
    root: Path,
    original_controller_run_id: str,
    expected: object,
) -> dict[str, Any]:
    """Read and validate version 1.0 known-reset quota resume claim against independent expected identity.

    READ-ONLY custody. Never grants wake, execution, or replay authority.
    """
    if not isinstance(root, Path):
        raise ClaimStorageError("root must be a Path")
    if type(original_controller_run_id) is not str:
        raise ClaimStorageError(
            f"original_controller_run_id must be exact str, got {type(original_controller_run_id).__name__}"
        )
    if not original_controller_run_id:
        raise ClaimStorageError("original_controller_run_id must be a non-empty string")
    if type(expected) is not dict:
        raise ClaimStorageError(
            f"expected must be exact dict, got {type(expected).__name__}"
        )
    if set(expected.keys()) != _EXPECTED_KEYS:
        raise ClaimStorageError(
            f"expected keys must exactly match {sorted(_EXPECTED_KEYS)!r}"
        )
    expected_orig_cid = expected["original_controller_run_id"]
    if type(expected_orig_cid) is not type(original_controller_run_id) or expected_orig_cid != original_controller_run_id:
        raise ClaimStorageError(
            f"expected original_controller_run_id {expected_orig_cid!r} does not match {original_controller_run_id!r}"
        )

    try:
        with _open_existing_cycle_run_directory(root, original_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise ClaimStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / QUOTA_RESUME_CLAIM_FILENAME

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise ClaimStorageError(f"cannot lstat {QUOTA_RESUME_CLAIM_FILENAME}: {exc}") from exc
            _validate_stat(lstat_before, f"{QUOTA_RESUME_CLAIM_FILENAME} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(QUOTA_RESUME_CLAIM_FILENAME, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise ClaimStorageError(f"cannot stat {QUOTA_RESUME_CLAIM_FILENAME} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{QUOTA_RESUME_CLAIM_FILENAME} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} dir_fd stat mismatch before open")

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                file_fd = os.open(QUOTA_RESUME_CLAIM_FILENAME, flags, dir_fd=run_fd)
            except OSError as exc:
                raise ClaimStorageError(f"cannot safely open {QUOTA_RESUME_CLAIM_FILENAME}: {exc}") from exc

            try:
                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise ClaimStorageError(f"cannot fstat {QUOTA_RESUME_CLAIM_FILENAME} before read: {exc}") from exc
                _validate_stat(fstat_before, f"{QUOTA_RESUME_CLAIM_FILENAME} descriptor before read")
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} descriptor metadata mismatch before read")

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise ClaimStorageError(f"cannot read {QUOTA_RESUME_CLAIM_FILENAME}: {exc}") from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_CLAIM_RECORD_BYTES:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} exceeds byte limit")
                if len(data) != expected_size:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} read size mismatch")

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise ClaimStorageError(f"cannot fstat {QUOTA_RESUME_CLAIM_FILENAME} after read: {exc}") from exc
                _validate_stat(fstat_after, f"{QUOTA_RESUME_CLAIM_FILENAME} descriptor after read")
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} descriptor metadata changed after read")

                try:
                    dirfd_stat_after = os.stat(QUOTA_RESUME_CLAIM_FILENAME, dir_fd=run_fd, follow_symlinks=False)
                except OSError as exc:
                    raise ClaimStorageError(f"cannot stat {QUOTA_RESUME_CLAIM_FILENAME} via dir_fd after read: {exc}") from exc
                _validate_stat(dirfd_stat_after, f"{QUOTA_RESUME_CLAIM_FILENAME} via dir_fd after read")
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} dir_fd metadata changed after read")

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise ClaimStorageError(f"cannot lstat {QUOTA_RESUME_CLAIM_FILENAME} after read: {exc}") from exc
                _validate_stat(lstat_after, f"{QUOTA_RESUME_CLAIM_FILENAME} path after read")
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} path metadata changed after read")

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise ClaimStorageError(f"directory identity verification failed after read: {exc}") from exc

                try:
                    decoded = data.decode("utf-8")
                except (KeyboardInterrupt, SystemExit):
                    raise
                except UnicodeDecodeError as exc:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} is not valid UTF-8: {exc}") from exc

                try:
                    payload = unique_json(decoded)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} is not valid JSON: {exc}") from exc
                except Exception as exc:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} is not valid JSON: {exc}") from exc

                if not isinstance(payload, dict) or type(payload) is not dict:
                    raise ClaimStorageError(
                        f"{QUOTA_RESUME_CLAIM_FILENAME} must be a JSON object, got {type(payload).__name__}"
                    )

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
                    raise ClaimStorageError(
                        f"{QUOTA_RESUME_CLAIM_FILENAME} cannot be serialized to canonical JSON: {exc}"
                    ) from exc

                if data != canonical_bytes:
                    raise ClaimStorageError(f"{QUOTA_RESUME_CLAIM_FILENAME} is not canonical JSON bytes")

                try:
                    return validate_known_reset_resume_claim(payload, expected)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except ResumeClaimRecordError as exc:
                    raise ClaimStorageError(f"known-reset resume claim validation failed: {exc}") from exc
                except Exception as exc:
                    raise ClaimStorageError(
                        f"unexpected error validating known-reset resume claim: {exc}"
                    ) from exc
            finally:
                try:
                    os.close(file_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise ClaimStorageError(
                            f"cannot safely close {QUOTA_RESUME_CLAIM_FILENAME} descriptor: {exc}"
                        ) from exc
    except BrokerError as exc:
        raise ClaimStorageError(f"cannot safely open cycle run directory: {exc}") from exc
