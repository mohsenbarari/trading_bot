"""Dormant private storage for accepted lineage records.

Provides durable no-overwrite publication and validated readback of
version 1.0 accepted lineage records in original cycle run directories.

Operational boundary and authority constraints:
1. Dormant private storage only: this module contains NO live hooks, NO queue
   authority, NO Provider execution authority, NO lock acquisition, and NO
   ack, wake, replay, or cleanup authority.
2. The caller MUST hold the Supervisor lock throughout publication.
3. Candidate records are constructed via build_file_bound_accepted_lineage_candidate,
   binding exact raw result.json SHA from cycle run directory custody against
   independently validated transition, context, and upstream evidence.
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
from typing import Any

from scripts.two_site_proposal_runner import unique_json

try:
    from scripts.two_site_provider_broker import (
        CYCLE_RUN_RE,
        BrokerError,
        _open_existing_cycle_run_directory,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        CYCLE_RUN_RE,
        BrokerError,
        _open_existing_cycle_run_directory,
    )

try:
    from scripts.two_site_quota_accepted_lineage_record import (
        MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
        AcceptedLineageRecordError,
        validate_accepted_lineage_record,
    )
except ImportError:
    from two_site_quota_accepted_lineage_record import (  # type: ignore
        MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
        AcceptedLineageRecordError,
        validate_accepted_lineage_record,
    )

try:
    from scripts.two_site_quota_accepted_lineage_fileproof import (
        AcceptedLineageFileproofError,
        build_file_bound_accepted_lineage_candidate,
    )
except ImportError:
    from two_site_quota_accepted_lineage_fileproof import (  # type: ignore
        AcceptedLineageFileproofError,
        build_file_bound_accepted_lineage_candidate,
    )

ACCEPTED_LINEAGE_FILENAME: str = "accepted-lineage.json"
_READ_LIMIT: int = MAX_ACCEPTED_LINEAGE_RECORD_BYTES + 1

__all__ = [
    "ACCEPTED_LINEAGE_FILENAME",
    "AcceptedLineageStorageError",
    "AcceptedStorageError",
    "MAX_ACCEPTED_LINEAGE_RECORD_BYTES",
    "publish_accepted_lineage_record_candidate",
    "read_accepted_lineage_record_candidate",
    "read_untrusted_accepted_lineage_record_candidate",
]


class AcceptedLineageStorageError(Exception):
    """Raised when accepted lineage storage reading, writing, or validation fails."""


AcceptedStorageError = AcceptedLineageStorageError


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
        raise AcceptedLineageStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise AcceptedLineageStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AcceptedLineageStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise AcceptedLineageStorageError(f"{label} has invalid link count")
    if info.st_size == 0:
        raise AcceptedLineageStorageError(f"{label} is empty")
    if info.st_size < 0 or info.st_size > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
        raise AcceptedLineageStorageError(f"{label} exceeds byte limit")


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


def publish_accepted_lineage_record_candidate(
    root: Path,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> dict[str, Any]:
    """Publish version 1.0 accepted lineage record candidate under caller-held Supervisor lock.

    One-shot durable publication primitive. Never grants wake, Provider, or lock authority.
    The caller MUST hold the Supervisor lock throughout this invocation.
    """
    if not isinstance(root, Path):
        raise AcceptedLineageStorageError("root must be a Path")
    if type(continuation_controller_run_id) is not str:
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id must be exact str, got {type(continuation_controller_run_id).__name__}"
        )
    if not continuation_controller_run_id:
        raise AcceptedLineageStorageError("continuation_controller_run_id must be a non-empty string")
    if not CYCLE_RUN_RE.fullmatch(continuation_controller_run_id):
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )
    if type(expected_transition_result) is not dict:
        raise AcceptedLineageStorageError(
            f"expected_transition_result must be exact dict, got {type(expected_transition_result).__name__}"
        )
    if type(acceptance_context) is not dict:
        raise AcceptedLineageStorageError(
            f"acceptance_context must be exact dict, got {type(acceptance_context).__name__}"
        )
    if type(upstream) is not dict:
        raise AcceptedLineageStorageError(
            f"upstream must be exact dict, got {type(upstream).__name__}"
        )

    try:
        candidate = build_file_bound_accepted_lineage_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except (AcceptedLineageFileproofError, AcceptedLineageRecordError) as exc:
        raise AcceptedLineageStorageError(f"failed to build file-bound candidate: {exc}") from exc
    except Exception as exc:
        raise AcceptedLineageStorageError(f"unexpected error building candidate: {exc}") from exc

    candidate_run_id = candidate.get("controller_run_id")
    if candidate_run_id != continuation_controller_run_id:
        raise AcceptedLineageStorageError(
            f"candidate controller_run_id {candidate_run_id!r} does not match {continuation_controller_run_id!r}"
        )

    try:
        canonical_text = json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_bytes = canonical_text.encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise AcceptedLineageStorageError(f"candidate cannot be serialized to canonical JSON: {exc}") from exc

    if len(canonical_bytes) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
        raise AcceptedLineageStorageError("accepted lineage record exceeds byte limit")
    if len(canonical_bytes) == 0:
        raise AcceptedLineageStorageError("accepted lineage record is empty")

    target_name = ACCEPTED_LINEAGE_FILENAME
    temp_name = f".{ACCEPTED_LINEAGE_FILENAME}.tmp.{os.getpid()}"
    temp_created = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(root, continuation_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise AcceptedLineageStorageError(f"directory identity verification failed before write: {exc}") from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise AcceptedLineageStorageError(f"cannot stat {target_name} via dir_fd: {exc}") from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise AcceptedLineageStorageError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise AcceptedLineageStorageError(f"{target_name} already exists")

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
                    raise AcceptedLineageStorageError(f"cannot safely create temp file {temp_name}: {exc}") from exc
                temp_created = True

                write_exc: BaseException | None = None
                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, canonical_bytes[total_written:])
                        except OSError as exc:
                            raise AcceptedLineageStorageError(f"cannot write {temp_name}: {exc}") from exc
                        if written <= 0:
                            raise AcceptedLineageStorageError(f"write stalled while writing {temp_name}")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot fchmod {temp_name}: {exc}") from exc

                    try:
                        temp_stat = os.fstat(file_fd)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot fstat {temp_name}: {exc}") from exc
                    _validate_stat(temp_stat, f"temp file {temp_name}")
                    if temp_stat.st_size != data_len:
                        raise AcceptedLineageStorageError(f"temp file {temp_name} size mismatch")

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot fsync {temp_name}: {exc}") from exc
                except BaseException as exc:
                    write_exc = exc
                    raise
                finally:
                    if file_fd is not None:
                        fd_to_close = file_fd
                        file_fd = None
                        try:
                            os.close(fd_to_close)
                        except OSError as exc:
                            if write_exc is None:
                                raise AcceptedLineageStorageError(
                                    f"cannot safely close temp file {temp_name}: {exc}"
                                ) from exc

                try:
                    os.link(
                        temp_name,
                        target_name,
                        src_dir_fd=run_fd,
                        dst_dir_fd=run_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise AcceptedLineageStorageError(f"cannot link {target_name}: target already exists") from exc
                except OSError as exc:
                    raise AcceptedLineageStorageError(f"cannot link {target_name}: {exc}") from exc
                linked = True

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except OSError as exc:
                    raise AcceptedLineageStorageError(
                        f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}"
                    ) from exc

                try:
                    os.fsync(run_fd)
                except OSError as exc:
                    raise AcceptedLineageStorageError(
                        f"ambiguous post-link publication failure: cannot fsync directory: {exc}"
                    ) from exc

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise AcceptedLineageStorageError(
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
                        raise AcceptedLineageStorageError(
                            f"failed to clean up temp file {temp_name}: {exc}"
                        ) from exc
    except AcceptedLineageStorageError:
        raise
    except BrokerError as exc:
        if linked:
            raise AcceptedLineageStorageError(
                f"ambiguous post-link publication failure: cycle run directory error: {exc}"
            ) from exc
        raise AcceptedLineageStorageError(f"cannot safely open cycle run directory: {exc}") from exc
    except OSError as exc:
        if linked:
            raise AcceptedLineageStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise AcceptedLineageStorageError(f"cannot safely access cycle run directory: {exc}") from exc
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        if linked:
            raise AcceptedLineageStorageError(f"ambiguous post-link publication failure: {exc}") from exc
        raise

    try:
        readback_record = read_accepted_lineage_record_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except AcceptedLineageStorageError as exc:
        raise AcceptedLineageStorageError(f"ambiguous post-link publication failure: readback failed: {exc}") from exc
    except Exception as exc:
        raise AcceptedLineageStorageError(
            f"ambiguous post-link publication failure: readback unexpected error: {exc}"
        ) from exc

    if not _type_sensitive_equal(readback_record, candidate):
        raise AcceptedLineageStorageError("ambiguous post-link publication failure: readback record mismatch")

    return copy.deepcopy(readback_record)


def _read_canonical_accepted_lineage_object(
    root: Path,
    continuation_controller_run_id: str,
) -> dict[str, Any]:
    """Read canonical JSON object bytes from run directory under descriptor validation."""
    if not isinstance(root, Path):
        raise AcceptedLineageStorageError("root must be a Path")
    if type(continuation_controller_run_id) is not str:
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id must be exact str, got {type(continuation_controller_run_id).__name__}"
        )
    if not continuation_controller_run_id:
        raise AcceptedLineageStorageError("continuation_controller_run_id must be a non-empty string")
    if not CYCLE_RUN_RE.fullmatch(continuation_controller_run_id):
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )

    target_name = ACCEPTED_LINEAGE_FILENAME

    try:
        with _open_existing_cycle_run_directory(root, continuation_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise AcceptedLineageStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / target_name

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise AcceptedLineageStorageError(f"cannot lstat {target_name}: {exc}") from exc
            _validate_stat(lstat_before, f"{target_name} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise AcceptedLineageStorageError(f"cannot stat {target_name} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{target_name} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise AcceptedLineageStorageError(f"{target_name} dir_fd stat mismatch before open")

            file_fd: int | None = None
            try:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    file_fd = os.open(target_name, flags, dir_fd=run_fd)
                except OSError as exc:
                    raise AcceptedLineageStorageError(f"cannot safely open {target_name}: {exc}") from exc

                read_exc: BaseException | None = None
                try:
                    try:
                        fstat_before = os.fstat(file_fd)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot fstat {target_name} before read: {exc}") from exc
                    _validate_stat(fstat_before, f"{target_name} descriptor before read")
                    if _stat_fingerprint(fstat_before) != initial_fingerprint:
                        raise AcceptedLineageStorageError(f"{target_name} descriptor metadata mismatch before read")

                    expected_size = initial_fingerprint[5]
                    chunks: list[bytes] = []
                    total_read = 0
                    while total_read < _READ_LIMIT:
                        try:
                            chunk = os.read(file_fd, _READ_LIMIT - total_read)
                        except OSError as exc:
                            raise AcceptedLineageStorageError(f"cannot read {target_name}: {exc}") from exc
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total_read += len(chunk)

                    data = b"".join(chunks)
                    if len(data) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
                        raise AcceptedLineageStorageError(f"{target_name} exceeds byte limit")
                    if len(data) == 0:
                        raise AcceptedLineageStorageError(f"{target_name} is empty")
                    if len(data) != expected_size:
                        raise AcceptedLineageStorageError(f"{target_name} read size mismatch")

                    try:
                        fstat_after = os.fstat(file_fd)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot fstat {target_name} after read: {exc}") from exc
                    _validate_stat(fstat_after, f"{target_name} descriptor after read")
                    if _stat_fingerprint(fstat_after) != initial_fingerprint:
                        raise AcceptedLineageStorageError(f"{target_name} descriptor metadata changed after read")

                    try:
                        dirfd_stat_after = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot stat {target_name} via dir_fd after read: {exc}") from exc
                    _validate_stat(dirfd_stat_after, f"{target_name} via dir_fd after read")
                    if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                        raise AcceptedLineageStorageError(f"{target_name} dir_fd metadata changed after read")

                    try:
                        lstat_after = os.lstat(file_path)
                    except OSError as exc:
                        raise AcceptedLineageStorageError(f"cannot lstat {target_name} after read: {exc}") from exc
                    _validate_stat(lstat_after, f"{target_name} path after read")
                    if _stat_fingerprint(lstat_after) != initial_fingerprint:
                        raise AcceptedLineageStorageError(f"{target_name} path metadata changed after read")

                    try:
                        verify_identity()
                    except BrokerError as exc:
                        raise AcceptedLineageStorageError(f"directory identity verification failed after read: {exc}") from exc

                    try:
                        decoded = data.decode("utf-8")
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except UnicodeDecodeError as exc:
                        raise AcceptedLineageStorageError(f"{target_name} is not valid UTF-8: {exc}") from exc

                    try:
                        payload = unique_json(decoded)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise AcceptedLineageStorageError(f"{target_name} is not valid JSON: {exc}") from exc
                    except Exception as exc:
                        raise AcceptedLineageStorageError(f"{target_name} is not valid JSON: {exc}") from exc

                    if not isinstance(payload, dict) or type(payload) is not dict:
                        raise AcceptedLineageStorageError(f"{target_name} must be a JSON object, got {type(payload).__name__}")

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
                        raise AcceptedLineageStorageError(
                            f"{target_name} cannot be serialized to canonical JSON: {exc}"
                        ) from exc

                    if data != canonical_bytes:
                        raise AcceptedLineageStorageError(f"{target_name} is not canonical JSON bytes")

                    return copy.deepcopy(payload)
                except BaseException as exc:
                    read_exc = exc
                    raise
                finally:
                    if file_fd is not None:
                        fd_to_close = file_fd
                        file_fd = None
                        try:
                            os.close(fd_to_close)
                        except OSError as exc:
                            if read_exc is None:
                                raise AcceptedLineageStorageError(
                                    f"cannot safely close {target_name} descriptor: {exc}"
                                ) from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
    except AcceptedLineageStorageError:
        raise
    except BrokerError as exc:
        raise AcceptedLineageStorageError(f"cannot safely open cycle run directory: {exc}") from exc


def read_untrusted_accepted_lineage_record_candidate(
    root: Path,
    continuation_controller_run_id: str,
) -> dict[str, Any]:
    """Read untrusted accepted lineage record candidate as detached canonical JSON object.

    READ-ONLY detached reader without semantic lineage validation or wake/ack/replay authority."""
    return _read_canonical_accepted_lineage_object(
        root=root,
        continuation_controller_run_id=continuation_controller_run_id,
    )


def read_accepted_lineage_record_candidate(
    root: Path,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> dict[str, Any]:
    """Read and validate version 1.0 accepted lineage record against newly file-bound expectations.

    READ-ONLY custody. Never grants wake, execution, replay, or cleanup authority.
    """
    if not isinstance(root, Path):
        raise AcceptedLineageStorageError("root must be a Path")
    if type(continuation_controller_run_id) is not str:
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id must be exact str, got {type(continuation_controller_run_id).__name__}"
        )
    if not continuation_controller_run_id:
        raise AcceptedLineageStorageError("continuation_controller_run_id must be a non-empty string")
    if not CYCLE_RUN_RE.fullmatch(continuation_controller_run_id):
        raise AcceptedLineageStorageError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )
    if type(expected_transition_result) is not dict:
        raise AcceptedLineageStorageError(
            f"expected_transition_result must be exact dict, got {type(expected_transition_result).__name__}"
        )
    if type(acceptance_context) is not dict:
        raise AcceptedLineageStorageError(
            f"acceptance_context must be exact dict, got {type(acceptance_context).__name__}"
        )
    if type(upstream) is not dict:
        raise AcceptedLineageStorageError(
            f"upstream must be exact dict, got {type(upstream).__name__}"
        )

    try:
        candidate = build_file_bound_accepted_lineage_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except (AcceptedLineageFileproofError, AcceptedLineageRecordError) as exc:
        raise AcceptedLineageStorageError(f"failed to build file-bound candidate for readback: {exc}") from exc
    except Exception as exc:
        raise AcceptedLineageStorageError(f"unexpected error building candidate for readback: {exc}") from exc

    candidate_run_id = candidate.get("controller_run_id")
    if candidate_run_id != continuation_controller_run_id:
        raise AcceptedLineageStorageError(
            f"candidate controller_run_id {candidate_run_id!r} does not match {continuation_controller_run_id!r}"
        )

    payload = _read_canonical_accepted_lineage_object(
        root=root,
        continuation_controller_run_id=continuation_controller_run_id,
    )

    try:
        validated = validate_accepted_lineage_record(
            record=payload,
            expected_result=expected_transition_result,
            expected_context=acceptance_context,
            expected_upstream=upstream,
            expected_result_sha256=candidate["result_sha256"],
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except AcceptedLineageRecordError as exc:
        raise AcceptedLineageStorageError(f"accepted lineage record validation failed: {exc}") from exc
    except Exception as exc:
        raise AcceptedLineageStorageError(f"unexpected error validating accepted lineage record: {exc}") from exc

    validated_run_id = validated.get("controller_run_id")
    if validated_run_id != continuation_controller_run_id:
        raise AcceptedLineageStorageError(
            f"validated record controller_run_id {validated_run_id!r} does not match {continuation_controller_run_id!r}"
        )

    return copy.deepcopy(validated)
