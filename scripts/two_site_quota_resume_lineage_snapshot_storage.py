"""Dormant private storage for expected quota resume lineage snapshots.

Provides durable no-overwrite publication and validated readback of
version 1.0 expected lineage snapshots in original cycle run directories.

Operational boundary and authority constraints:
1. Dormant private storage only: this module contains NO live hooks, NO queue
   authority, NO Provider execution authority, NO lock acquisition, and NO
   ack, wake, replay, or cleanup authority.
2. The caller MUST hold the Supervisor lock throughout publication.
3. The caller MUST obtain wait_record and claim_record from independently
   protected evidence; this storage module does not itself prove pre-dispatch timing.
4. Fail-closed publication: after the target file is linked, any failure during
   cleanup, directory fsync, identity verification, or readback is AMBIGUOUS.
   The target file is retained and never unlinked or retried.
"""

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
    from scripts.two_site_quota_resume_lineage_snapshot_record import (
        LineageSnapshotRecordError,
        MAX_SNAPSHOT_RECORD_BYTES,
        build_expected_lineage_snapshot,
        canonical_json_bytes,
        validate_expected_lineage_snapshot,
    )
except ImportError:
    from two_site_quota_resume_lineage_snapshot_record import (  # type: ignore
        LineageSnapshotRecordError,
        MAX_SNAPSHOT_RECORD_BYTES,
        build_expected_lineage_snapshot,
        canonical_json_bytes,
        validate_expected_lineage_snapshot,
    )

QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME: str = "quota-resume-lineage-snapshot.json"
_READ_LIMIT: int = MAX_SNAPSHOT_RECORD_BYTES + 1

__all__ = [
    "MAX_SNAPSHOT_RECORD_BYTES",
    "QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME",
    "SnapshotStorageError",
    "publish_expected_lineage_snapshot",
    "read_expected_lineage_snapshot",
]


class SnapshotStorageError(Exception):
    """Raised when lineage snapshot storage reading, writing, or validation fails."""


LineageSnapshotStorageError = SnapshotStorageError


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
        raise SnapshotStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise SnapshotStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SnapshotStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise SnapshotStorageError(f"{label} has invalid link count")
    if info.st_size == 0:
        raise SnapshotStorageError(f"{label} is empty")
    if info.st_size < 0 or info.st_size > MAX_SNAPSHOT_RECORD_BYTES:
        raise SnapshotStorageError(f"{label} exceeds byte limit")


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def publish_expected_lineage_snapshot(
    root: Path,
    original_controller_run_id: str,
    wait_record: object,
    expected_wait: object,
    claim_record: object,
    expected_claim: object,
    request: object,
) -> dict[str, Any]:
    """Publish version 1.0 expected lineage snapshot under caller-held Supervisor lock.

    One-shot durable publication primitive. Never grants wake, Provider, or lock authority.
    The caller MUST hold the Supervisor lock throughout this invocation.
    """
    if not isinstance(root, Path):
        raise SnapshotStorageError("root must be a Path")
    if type(original_controller_run_id) is not str:
        raise SnapshotStorageError(
            f"original_controller_run_id must be exact str, got {type(original_controller_run_id).__name__}"
        )
    if not original_controller_run_id:
        raise SnapshotStorageError("original_controller_run_id must be a non-empty string")
    if type(wait_record) is not dict:
        raise SnapshotStorageError(f"wait_record must be exact dict, got {type(wait_record).__name__}")
    if type(expected_wait) is not dict:
        raise SnapshotStorageError(f"expected_wait must be exact dict, got {type(expected_wait).__name__}")
    if type(claim_record) is not dict:
        raise SnapshotStorageError(f"claim_record must be exact dict, got {type(claim_record).__name__}")
    if type(expected_claim) is not dict:
        raise SnapshotStorageError(f"expected_claim must be exact dict, got {type(expected_claim).__name__}")
    if type(request) is not dict:
        raise SnapshotStorageError(f"request must be exact dict, got {type(request).__name__}")

    try:
        built_snapshot = build_expected_lineage_snapshot(
            wait_record=wait_record,
            expected_wait=expected_wait,
            claim_record=claim_record,
            expected_claim=expected_claim,
            request=request,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except LineageSnapshotRecordError as exc:
        raise SnapshotStorageError(f"lineage snapshot construction failed: {exc}") from exc
    except Exception as exc:
        raise SnapshotStorageError(f"unexpected error building lineage snapshot: {exc}") from exc

    built_orig_cid = built_snapshot.get("original_controller_run_id")
    if type(built_orig_cid) is not str or built_orig_cid != original_controller_run_id:
        raise SnapshotStorageError(
            f"original_controller_run_id {original_controller_run_id!r} does not match snapshot {built_orig_cid!r}"
        )

    try:
        canonical_bytes = canonical_json_bytes(built_snapshot, max_bytes=MAX_SNAPSHOT_RECORD_BYTES)
    except (KeyboardInterrupt, SystemExit):
        raise
    except LineageSnapshotRecordError as exc:
        raise SnapshotStorageError(f"lineage snapshot serialization failed: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise SnapshotStorageError(f"lineage snapshot cannot be serialized to canonical JSON: {exc}") from exc

    if len(canonical_bytes) > MAX_SNAPSHOT_RECORD_BYTES:
        raise SnapshotStorageError("lineage snapshot exceeds byte limit")
    if len(canonical_bytes) == 0:
        raise SnapshotStorageError("lineage snapshot is empty")

    target_name = QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME
    temp_name = f".{QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME}.tmp.{os.getpid()}"
    temp_created = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(root, original_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise SnapshotStorageError(f"directory identity verification failed before write: {exc}") from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SnapshotStorageError(f"cannot stat {target_name} via dir_fd: {exc}") from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise SnapshotStorageError(f"{target_name} already exists")

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
                    raise SnapshotStorageError(f"cannot safely create temp file {temp_name}: {exc}") from exc
                temp_created = True

                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, canonical_bytes[total_written:])
                        except OSError as exc:
                            raise SnapshotStorageError(f"cannot write {temp_name}: {exc}") from exc
                        if written <= 0:
                            raise SnapshotStorageError(f"write stalled while writing {temp_name}")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise SnapshotStorageError(f"cannot fchmod {temp_name}: {exc}") from exc

                    try:
                        temp_stat = os.fstat(file_fd)
                    except OSError as exc:
                        raise SnapshotStorageError(f"cannot fstat {temp_name}: {exc}") from exc
                    _validate_stat(temp_stat, f"temp file {temp_name}")
                    if temp_stat.st_size != data_len:
                        raise SnapshotStorageError(f"temp file {temp_name} size mismatch")

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise SnapshotStorageError(f"cannot fsync {temp_name}: {exc}") from exc
                finally:
                    if file_fd is not None:
                        try:
                            os.close(file_fd)
                        except OSError as exc:
                            file_fd = None
                            if sys.exc_info()[1] is not None:
                                raise SnapshotStorageError(
                                    f"cannot safely close temp file {temp_name}: {exc}"
                                ) from sys.exc_info()[1]
                            raise SnapshotStorageError(f"cannot safely close temp file {temp_name}: {exc}") from exc
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
                    raise SnapshotStorageError(f"cannot link {target_name}: target already exists") from exc
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot link {target_name}: {exc}") from exc
                linked = True

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except OSError as exc:
                    raise SnapshotStorageError(
                        f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}"
                    ) from exc

                try:
                    os.fsync(run_fd)
                except OSError as exc:
                    raise SnapshotStorageError(
                        f"ambiguous post-link publication failure: cannot fsync directory: {exc}"
                    ) from exc

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise SnapshotStorageError(
                        f"ambiguous post-link publication failure: directory identity verification failed after write: {exc}"
                    ) from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
                if temp_created and not linked:
                    try:
                        os.unlink(temp_name, dir_fd=run_fd)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        if sys.exc_info()[1] is not None:
                            raise SnapshotStorageError(
                                f"failed to clean up temp file {temp_name}: {exc}"
                            ) from sys.exc_info()[1]
                        raise SnapshotStorageError(
                            f"failed to clean up temp file {temp_name}: {exc}"
                        ) from exc
    except SnapshotStorageError:
        raise
    except BrokerError as exc:
        if linked:
            raise SnapshotStorageError(
                f"ambiguous post-link publication failure: cycle run directory error: {exc}"
            ) from exc
        raise SnapshotStorageError(f"cannot safely open cycle run directory: {exc}") from exc
    except OSError as exc:
        if linked:
            raise SnapshotStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise SnapshotStorageError(f"cannot safely access cycle run directory: {exc}") from exc
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        if linked:
            raise SnapshotStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise

    try:
        readback_snapshot = read_expected_lineage_snapshot(
            root=root,
            original_controller_run_id=original_controller_run_id,
            wait_record=wait_record,
            claim_record=claim_record,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except SnapshotStorageError as exc:
        raise SnapshotStorageError(f"ambiguous post-link publication failure: readback failed: {exc}") from exc
    except Exception as exc:
        raise SnapshotStorageError(f"ambiguous post-link publication failure: readback unexpected error: {exc}") from exc

    if not _type_sensitive_equal(readback_snapshot, built_snapshot):
        raise SnapshotStorageError("ambiguous post-link publication failure: readback snapshot mismatch")

    return copy.deepcopy(readback_snapshot)


def read_expected_lineage_snapshot(
    root: Path,
    original_controller_run_id: str,
    wait_record: object,
    claim_record: object,
) -> dict[str, Any]:
    """Read and validate version 1.0 expected lineage snapshot against wait and claim records.

    READ-ONLY custody. Never grants wake, execution, replay, or cleanup authority.
    """
    if not isinstance(root, Path):
        raise SnapshotStorageError("root must be a Path")
    if type(original_controller_run_id) is not str:
        raise SnapshotStorageError(
            f"original_controller_run_id must be exact str, got {type(original_controller_run_id).__name__}"
        )
    if not original_controller_run_id:
        raise SnapshotStorageError("original_controller_run_id must be a non-empty string")
    if type(wait_record) is not dict:
        raise SnapshotStorageError(f"wait_record must be exact dict, got {type(wait_record).__name__}")
    if type(claim_record) is not dict:
        raise SnapshotStorageError(f"claim_record must be exact dict, got {type(claim_record).__name__}")

    wait_orig_cid = wait_record.get("controller_run_id")
    if type(wait_orig_cid) is not str or wait_orig_cid != original_controller_run_id:
        raise SnapshotStorageError(
            f"wait_record controller_run_id {wait_orig_cid!r} does not match {original_controller_run_id!r}"
        )
    claim_orig_cid = claim_record.get("original_controller_run_id")
    if type(claim_orig_cid) is not str or claim_orig_cid != original_controller_run_id:
        raise SnapshotStorageError(
            f"claim_record original_controller_run_id {claim_orig_cid!r} does not match {original_controller_run_id!r}"
        )

    target_name = QUOTA_RESUME_LINEAGE_SNAPSHOT_FILENAME

    try:
        with _open_existing_cycle_run_directory(root, original_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise SnapshotStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / target_name

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise SnapshotStorageError(f"cannot lstat {target_name}: {exc}") from exc
            _validate_stat(lstat_before, f"{target_name} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise SnapshotStorageError(f"cannot stat {target_name} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{target_name} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise SnapshotStorageError(f"{target_name} dir_fd stat mismatch before open")

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                file_fd = os.open(target_name, flags, dir_fd=run_fd)
            except OSError as exc:
                raise SnapshotStorageError(f"cannot safely open {target_name}: {exc}") from exc

            try:
                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot fstat {target_name} before read: {exc}") from exc
                _validate_stat(fstat_before, f"{target_name} descriptor before read")
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise SnapshotStorageError(f"{target_name} descriptor metadata mismatch before read")

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise SnapshotStorageError(f"cannot read {target_name}: {exc}") from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_SNAPSHOT_RECORD_BYTES:
                    raise SnapshotStorageError(f"{target_name} exceeds byte limit")
                if len(data) == 0:
                    raise SnapshotStorageError(f"{target_name} is empty")
                if len(data) != expected_size:
                    raise SnapshotStorageError(f"{target_name} read size mismatch")

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot fstat {target_name} after read: {exc}") from exc
                _validate_stat(fstat_after, f"{target_name} descriptor after read")
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise SnapshotStorageError(f"{target_name} descriptor metadata changed after read")

                try:
                    dirfd_stat_after = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot stat {target_name} via dir_fd after read: {exc}") from exc
                _validate_stat(dirfd_stat_after, f"{target_name} via dir_fd after read")
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise SnapshotStorageError(f"{target_name} dir_fd metadata changed after read")

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise SnapshotStorageError(f"cannot lstat {target_name} after read: {exc}") from exc
                _validate_stat(lstat_after, f"{target_name} path after read")
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise SnapshotStorageError(f"{target_name} path metadata changed after read")

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise SnapshotStorageError(f"directory identity verification failed after read: {exc}") from exc

                try:
                    decoded = data.decode("utf-8")
                except (KeyboardInterrupt, SystemExit):
                    raise
                except UnicodeDecodeError as exc:
                    raise SnapshotStorageError(f"{target_name} is not valid UTF-8: {exc}") from exc

                try:
                    payload = unique_json(decoded)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except (json.JSONDecodeError, ValueError) as exc:
                    raise SnapshotStorageError(f"{target_name} is not valid JSON: {exc}") from exc
                except Exception as exc:
                    raise SnapshotStorageError(f"{target_name} is not valid JSON: {exc}") from exc

                if not isinstance(payload, dict) or type(payload) is not dict:
                    raise SnapshotStorageError(f"{target_name} must be a JSON object, got {type(payload).__name__}")

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
                    raise SnapshotStorageError(
                        f"{target_name} cannot be serialized to canonical JSON: {exc}"
                    ) from exc

                if data != canonical_bytes:
                    raise SnapshotStorageError(f"{target_name} is not canonical JSON bytes")

                try:
                    validated = validate_expected_lineage_snapshot(
                        snapshot=payload,
                        wait_record=wait_record,
                        claim_record=claim_record,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except LineageSnapshotRecordError as exc:
                    raise SnapshotStorageError(f"expected lineage snapshot validation failed: {exc}") from exc
                except Exception as exc:
                    raise SnapshotStorageError(f"unexpected error validating expected lineage snapshot: {exc}") from exc

                snap_orig_cid = validated.get("original_controller_run_id")
                if type(snap_orig_cid) is not str or snap_orig_cid != original_controller_run_id:
                    raise SnapshotStorageError(
                        f"snapshot original_controller_run_id {snap_orig_cid!r} does not match {original_controller_run_id!r}"
                    )

                return copy.deepcopy(validated)
            finally:
                try:
                    os.close(file_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise SnapshotStorageError(
                            f"cannot safely close {target_name} descriptor: {exc}"
                        ) from exc
    except SnapshotStorageError:
        raise
    except BrokerError as exc:
        raise SnapshotStorageError(f"cannot safely open cycle run directory: {exc}") from exc
