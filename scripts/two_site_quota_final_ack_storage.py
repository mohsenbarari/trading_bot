"""Dormant private storage for accepted final acknowledgement records.

Provides durable caller-lock-required one-shot no-overwrite publication and
descriptor-pinned readback of version 1.0 final acknowledgement candidate records
in continuation cycle run directories.

Boundary and authority constraints:
1. Dormant storage only: contains NO live hooks, NO Supervisor/Monitor/Broker loop,
   NO Provider execution, NO wake, NO replay, and NO retention or deletion authority.
2. The caller MUST hold the exclusive Supervisor lock throughout publication.
3. Candidate records are built strictly from file-bound accepted lineage records
   retrieved via read_accepted_lineage_record_candidate, not from caller dicts or mutable status.
4. Fail-closed publication: pre-link failures never create the target file. Once linked,
   ANY subsequent failure during cleanup, directory fsync, descriptor close, identity verification,
   accepted lineage revalidation, or readback is an explicit ambiguous-post-link error; the
   published target is retained and never unlinked, retried, or overwritten.
5. Retention proof independence: the acknowledged_at_utc timestamp is evidence only; this unit
   does NOT prove retention age or authorize C4c pruning.
"""

from __future__ import annotations

import copy
import hashlib
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
    )
except ImportError:
    from two_site_quota_accepted_lineage_record import (  # type: ignore
        MAX_ACCEPTED_LINEAGE_RECORD_BYTES,
    )

try:
    from scripts.two_site_quota_accepted_lineage_storage import (
        AcceptedLineageStorageError,
        read_accepted_lineage_record_candidate,
    )
except ImportError:
    from two_site_quota_accepted_lineage_storage import (  # type: ignore
        AcceptedLineageStorageError,
        read_accepted_lineage_record_candidate,
    )

try:
    from scripts.two_site_quota_final_ack_record import (
        FINAL_ACK_KIND,
        FINAL_ACK_SCHEMA_VERSION,
        FinalAckRecordError,
        MAX_FINAL_ACK_RECORD_BYTES,
        _bounded_canonical_bytes,
        build_final_ack_candidate,
        validate_final_ack_candidate,
    )
except ImportError:
    from two_site_quota_final_ack_record import (  # type: ignore
        FINAL_ACK_KIND,
        FINAL_ACK_SCHEMA_VERSION,
        FinalAckRecordError,
        MAX_FINAL_ACK_RECORD_BYTES,
        _bounded_canonical_bytes,
        build_final_ack_candidate,
        validate_final_ack_candidate,
    )

FINAL_ACK_FILENAME: str = "final-ack.json"
_READ_LIMIT: int = MAX_FINAL_ACK_RECORD_BYTES + 1

__all__ = [
    "FINAL_ACK_FILENAME",
    "FinalAckStorageAmbiguousError",
    "FinalAckStorageAmbiguousPostLinkError",
    "FinalAckStorageAmbiguousPostPublicationError",
    "FinalAckStorageError",
    "MAX_FINAL_ACK_RECORD_BYTES",
    "publish_final_ack_record_candidate",
    "read_final_ack_record_candidate",
]


class FinalAckStorageError(Exception):
    """Raised when final acknowledgement storage reading, writing, or validation fails."""


class FinalAckStorageAmbiguousPostPublicationError(FinalAckStorageError):
    """Raised when failure or drift occurs after target file hard-link."""


FinalAckStorageAmbiguousError = FinalAckStorageAmbiguousPostPublicationError
FinalAckStorageAmbiguousPostLinkError = FinalAckStorageAmbiguousPostPublicationError


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
        raise FinalAckStorageError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise FinalAckStorageError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise FinalAckStorageError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise FinalAckStorageError(f"{label} has invalid link count")
    if info.st_size == 0:
        raise FinalAckStorageError(f"{label} is empty")
    if info.st_size < 0 or info.st_size > MAX_FINAL_ACK_RECORD_BYTES:
        raise FinalAckStorageError(f"{label} exceeds byte limit")


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


def _validate_common_inputs(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> None:
    if not isinstance(root, Path):
        raise FinalAckStorageError("root must be a Path")
    if type(original_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        original_controller_run_id
    ):
        raise FinalAckStorageError(
            "original_controller_run_id is not a valid cycle run ID"
        )
    if type(continuation_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        continuation_controller_run_id
    ):
        raise FinalAckStorageError(
            "continuation_controller_run_id is not a valid cycle run ID"
        )
    if original_controller_run_id == continuation_controller_run_id:
        raise FinalAckStorageError(
            "original_controller_run_id and continuation_controller_run_id must be distinct"
        )
    if type(expected_transition_result) is not dict:
        raise FinalAckStorageError("expected_transition_result must be exact dict")
    if type(acceptance_context) is not dict:
        raise FinalAckStorageError("acceptance_context must be exact dict")
    if type(upstream) is not dict:
        raise FinalAckStorageError("upstream must be exact dict")


def _read_canonical_final_ack_object(
    root: Path,
    continuation_controller_run_id: str,
) -> dict[str, Any]:
    """Read canonical JSON object bytes from run directory under descriptor validation."""
    if not isinstance(root, Path):
        raise FinalAckStorageError("root must be a Path")
    if type(continuation_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        continuation_controller_run_id
    ):
        raise FinalAckStorageError(
            "continuation_controller_run_id is not a valid cycle run ID"
        )

    target_name = FINAL_ACK_FILENAME

    try:
        with _open_existing_cycle_run_directory(root, continuation_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise FinalAckStorageError(f"directory identity verification failed before read: {exc}") from exc

            file_path = run_path / target_name

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise FinalAckStorageError(f"cannot lstat {target_name}: {exc}") from exc
            _validate_stat(lstat_before, f"{target_name} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
            except OSError as exc:
                raise FinalAckStorageError(f"cannot stat {target_name} via dir_fd before open: {exc}") from exc
            _validate_stat(dirfd_stat_before, f"{target_name} via dir_fd before open")
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise FinalAckStorageError(f"{target_name} dir_fd stat mismatch before open")

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
                    raise FinalAckStorageError(f"cannot safely open {target_name}: {exc}") from exc

                read_exc: BaseException | None = None
                try:
                    try:
                        fstat_before = os.fstat(file_fd)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot fstat {target_name} before read: {exc}") from exc
                    _validate_stat(fstat_before, f"{target_name} descriptor before read")
                    if _stat_fingerprint(fstat_before) != initial_fingerprint:
                        raise FinalAckStorageError(f"{target_name} descriptor metadata mismatch before read")

                    expected_size = initial_fingerprint[5]
                    chunks: list[bytes] = []
                    total_read = 0
                    while total_read < _READ_LIMIT:
                        try:
                            chunk = os.read(file_fd, _READ_LIMIT - total_read)
                        except OSError as exc:
                            raise FinalAckStorageError(f"cannot read {target_name}: {exc}") from exc
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total_read += len(chunk)

                    data = b"".join(chunks)
                    if len(data) > MAX_FINAL_ACK_RECORD_BYTES:
                        raise FinalAckStorageError(f"{target_name} exceeds byte limit")
                    if len(data) == 0:
                        raise FinalAckStorageError(f"{target_name} is empty")
                    if len(data) != expected_size:
                        raise FinalAckStorageError(f"{target_name} read size mismatch")

                    try:
                        fstat_after = os.fstat(file_fd)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot fstat {target_name} after read: {exc}") from exc
                    _validate_stat(fstat_after, f"{target_name} descriptor after read")
                    if _stat_fingerprint(fstat_after) != initial_fingerprint:
                        raise FinalAckStorageError(f"{target_name} descriptor metadata changed after read")

                    try:
                        dirfd_stat_after = os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot stat {target_name} via dir_fd after read: {exc}") from exc
                    _validate_stat(dirfd_stat_after, f"{target_name} via dir_fd after read")
                    if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                        raise FinalAckStorageError(f"{target_name} dir_fd metadata changed after read")

                    try:
                        lstat_after = os.lstat(file_path)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot lstat {target_name} after read: {exc}") from exc
                    _validate_stat(lstat_after, f"{target_name} path after read")
                    if _stat_fingerprint(lstat_after) != initial_fingerprint:
                        raise FinalAckStorageError(f"{target_name} path metadata changed after read")

                    try:
                        verify_identity()
                    except BrokerError as exc:
                        raise FinalAckStorageError(f"directory identity verification failed after read: {exc}") from exc

                    try:
                        decoded = data.decode("utf-8")
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except UnicodeDecodeError as exc:
                        raise FinalAckStorageError(f"{target_name} is not valid UTF-8: {exc}") from exc

                    try:
                        payload = unique_json(decoded)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise FinalAckStorageError(f"{target_name} is not valid JSON: {exc}") from exc
                    except Exception as exc:
                        raise FinalAckStorageError(f"{target_name} is not valid JSON: {exc}") from exc

                    if not isinstance(payload, dict) or type(payload) is not dict:
                        raise FinalAckStorageError(f"{target_name} must be a JSON object, got {type(payload).__name__}")

                    try:
                        canonical_bytes = _bounded_canonical_bytes(payload, MAX_FINAL_ACK_RECORD_BYTES)
                    except FinalAckRecordError as exc:
                        raise FinalAckStorageError(
                            f"{target_name} cannot be serialized to canonical JSON: {exc}"
                        ) from exc

                    if data != canonical_bytes:
                        raise FinalAckStorageError(f"{target_name} is not canonical JSON bytes")

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
                                raise FinalAckStorageError(
                                    f"cannot safely close {target_name} descriptor: {exc}"
                                ) from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
    except FinalAckStorageError:
        raise
    except BrokerError as exc:
        raise FinalAckStorageError(f"cannot safely open cycle run directory: {exc}") from exc


def _read_and_validate_accepted_lineage(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    try:
        lineage = read_accepted_lineage_record_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except AcceptedLineageStorageError as exc:
        raise FinalAckStorageError(f"failed to read accepted lineage record: {exc}") from exc
    except Exception as exc:
        raise FinalAckStorageError(f"unexpected error reading accepted lineage record: {exc}") from exc

    if lineage.get("controller_run_id") != continuation_controller_run_id:
        raise FinalAckStorageError(
            "accepted lineage controller_run_id does not match continuation_controller_run_id"
        )
    lineage_upstream = lineage.get("upstream")
    if not isinstance(lineage_upstream, dict):
        raise FinalAckStorageError("accepted lineage upstream must be a dict")
    if lineage_upstream.get("original_controller_run_id") != original_controller_run_id:
        raise FinalAckStorageError(
            "accepted lineage upstream original_controller_run_id does not match supplied original_controller_run_id"
        )

    try:
        lineage_bytes = _bounded_canonical_bytes(lineage, MAX_ACCEPTED_LINEAGE_RECORD_BYTES)
    except FinalAckRecordError as exc:
        raise FinalAckStorageError(f"failed to canonically encode accepted lineage: {exc}") from exc

    lineage_sha = hashlib.sha256(lineage_bytes).hexdigest()
    return lineage, lineage_sha


def read_final_ack_record_candidate(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
) -> dict[str, Any]:
    """Read and strictly validate version 1.0 final acknowledgement candidate record."""
    _validate_common_inputs(
        root=root,
        original_controller_run_id=original_controller_run_id,
        continuation_controller_run_id=continuation_controller_run_id,
        expected_transition_result=expected_transition_result,
        acceptance_context=acceptance_context,
        upstream=upstream,
    )

    lineage, lineage_sha = _read_and_validate_accepted_lineage(
        root=root,
        original_controller_run_id=original_controller_run_id,
        continuation_controller_run_id=continuation_controller_run_id,
        expected_transition_result=expected_transition_result,
        acceptance_context=acceptance_context,
        upstream=upstream,
    )

    payload = _read_canonical_final_ack_object(
        root=root,
        continuation_controller_run_id=continuation_controller_run_id,
    )

    try:
        validated = validate_final_ack_candidate(
            candidate=payload,
            accepted_lineage_record=lineage,
            accepted_lineage_sha256=lineage_sha,
        )
    except FinalAckRecordError as exc:
        raise FinalAckStorageError(f"final ack candidate validation failed: {exc}") from exc

    return copy.deepcopy(validated)


def publish_final_ack_record_candidate(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
    acknowledged_at_utc: str,
) -> dict[str, Any]:
    """Publish version 1.0 final acknowledgement record under caller-held exclusive Supervisor lock.

    One-shot durable publication primitive. Never grants wake, replay, or retention authority.
    Caller MUST hold exclusive Supervisor lock throughout this invocation.
    """
    _validate_common_inputs(
        root=root,
        original_controller_run_id=original_controller_run_id,
        continuation_controller_run_id=continuation_controller_run_id,
        expected_transition_result=expected_transition_result,
        acceptance_context=acceptance_context,
        upstream=upstream,
    )
    if type(acknowledged_at_utc) is not str:
        raise FinalAckStorageError("acknowledged_at_utc must be a string")

    lineage_before, lineage_sha = _read_and_validate_accepted_lineage(
        root=root,
        original_controller_run_id=original_controller_run_id,
        continuation_controller_run_id=continuation_controller_run_id,
        expected_transition_result=expected_transition_result,
        acceptance_context=acceptance_context,
        upstream=upstream,
    )

    try:
        candidate = build_final_ack_candidate(
            accepted_lineage_record=lineage_before,
            accepted_lineage_sha256=lineage_sha,
            acknowledged_at_utc=acknowledged_at_utc,
        )
    except FinalAckRecordError as exc:
        raise FinalAckStorageError(f"failed to build final ack candidate: {exc}") from exc

    try:
        canonical_bytes = _bounded_canonical_bytes(candidate, MAX_FINAL_ACK_RECORD_BYTES)
    except FinalAckRecordError as exc:
        raise FinalAckStorageError(f"candidate cannot be serialized to canonical JSON: {exc}") from exc

    if len(canonical_bytes) > MAX_FINAL_ACK_RECORD_BYTES:
        raise FinalAckStorageError("final ack record exceeds byte limit")
    if len(canonical_bytes) == 0:
        raise FinalAckStorageError("final ack record is empty")

    target_name = FINAL_ACK_FILENAME
    temp_name = f".{FINAL_ACK_FILENAME}.tmp.{os.getpid()}"
    temp_created = False
    link_attempted = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(root, continuation_controller_run_id) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise FinalAckStorageError(f"directory identity verification failed before write: {exc}") from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FinalAckStorageError(f"cannot stat {target_name} via dir_fd: {exc}") from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise FinalAckStorageError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise FinalAckStorageError(f"{target_name} already exists")

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
                    raise FinalAckStorageError(f"cannot safely create temp file {temp_name}: {exc}") from exc
                temp_created = True

                write_exc: BaseException | None = None
                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, canonical_bytes[total_written:])
                        except OSError as exc:
                            raise FinalAckStorageError(f"cannot write {temp_name}: {exc}") from exc
                        if written <= 0:
                            raise FinalAckStorageError(f"write stalled while writing {temp_name}")
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot fchmod {temp_name}: {exc}") from exc

                    try:
                        temp_stat = os.fstat(file_fd)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot fstat {temp_name}: {exc}") from exc
                    _validate_stat(temp_stat, f"temp file {temp_name}")
                    if temp_stat.st_size != data_len:
                        raise FinalAckStorageError(f"temp file {temp_name} size mismatch")

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise FinalAckStorageError(f"cannot fsync {temp_name}: {exc}") from exc
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
                                raise FinalAckStorageError(
                                    f"cannot safely close temp file {temp_name}: {exc}"
                                ) from exc

                link_attempted = True
                try:
                    os.link(
                        temp_name,
                        target_name,
                        src_dir_fd=run_fd,
                        dst_dir_fd=run_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    link_attempted = False
                    raise FinalAckStorageError(f"cannot link {target_name}: target already exists") from exc
                except BaseException as exc:
                    raise FinalAckStorageAmbiguousPostPublicationError(
                        f"ambiguous post-link publication failure: os.link status uncertain: {exc}"
                    ) from exc
                linked = True

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except BaseException as exc:
                    raise FinalAckStorageAmbiguousPostPublicationError(
                        f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}"
                    ) from exc

                try:
                    os.fsync(run_fd)
                except BaseException as exc:
                    raise FinalAckStorageAmbiguousPostPublicationError(
                        f"ambiguous post-link publication failure: cannot fsync directory: {exc}"
                    ) from exc

                try:
                    verify_identity()
                except BaseException as exc:
                    raise FinalAckStorageAmbiguousPostPublicationError(
                        f"ambiguous post-link publication failure: directory identity verification failed after write: {exc}"
                    ) from exc
            finally:
                if file_fd is not None:
                    try:
                        os.close(file_fd)
                    except OSError:
                        pass
                if temp_created and not linked and not link_attempted:
                    try:
                        os.unlink(temp_name, dir_fd=run_fd)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        raise FinalAckStorageError(
                            f"failed to clean up temp file {temp_name}: {exc}"
                        ) from exc
    except FinalAckStorageAmbiguousPostPublicationError:
        raise
    except BaseException as exc:
        if linked or link_attempted:
            raise FinalAckStorageAmbiguousPostPublicationError(
                f"ambiguous post-link publication failure: {exc}"
            ) from exc
        if isinstance(exc, FinalAckStorageError):
            raise
        if isinstance(exc, BrokerError):
            raise FinalAckStorageError(f"cannot safely open cycle run directory: {exc}") from exc
        if isinstance(exc, OSError):
            raise FinalAckStorageError(f"cannot safely access cycle run directory: {exc}") from exc
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise

    # Post-link revalidation of accepted lineage
    try:
        lineage_after, _ = _read_and_validate_accepted_lineage(
            root=root,
            original_controller_run_id=original_controller_run_id,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except FinalAckStorageAmbiguousPostPublicationError:
        raise
    except BaseException as exc:
        raise FinalAckStorageAmbiguousPostPublicationError(
            f"ambiguous post-link publication failure: accepted lineage revalidation failed: {exc}"
        ) from exc

    if not _type_sensitive_equal(lineage_before, lineage_after):
        raise FinalAckStorageAmbiguousPostPublicationError(
            "ambiguous post-link publication failure: accepted lineage record drifted after publication"
        )

    # Post-link strict readback of target final-ack.json
    try:
        readback_record = read_final_ack_record_candidate(
            root=root,
            original_controller_run_id=original_controller_run_id,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except FinalAckStorageAmbiguousPostPublicationError:
        raise
    except BaseException as exc:
        raise FinalAckStorageAmbiguousPostPublicationError(
            f"ambiguous post-link publication failure: readback failed: {exc}"
        ) from exc

    if not _type_sensitive_equal(readback_record, candidate):
        raise FinalAckStorageAmbiguousPostPublicationError(
            "ambiguous post-link publication failure: readback record does not match candidate"
        )

    return copy.deepcopy(readback_record)
