"""One-shot durable submission intent storage and validation for quota continuation requests."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable

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
        CONTROLLER_RUN_RE,
        REQUEST_ID_RE,
        BrokerError,
        _open_existing_cycle_run_directory,
        canonical_root,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        CONTROLLER_RUN_RE,
        REQUEST_ID_RE,
        BrokerError,
        _open_existing_cycle_run_directory,
        canonical_root,
    )

try:
    from scripts.two_site_quota_continuation_admission import (
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )
except ImportError:
    from two_site_quota_continuation_admission import (  # type: ignore
        ContinuationAdmissionError,
        validate_quota_continuation_request,
    )

QUOTA_RESUME_SUBMISSION_FILENAME: str = "quota-resume-submission.json"
MAX_SUBMISSION_RECORD_BYTES: int = 4096
_READ_LIMIT: int = 4097
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUEST_KEYS = frozenset({
    "schema_version",
    "request_type",
    "request_id",
    "controller_run_id",
    "original_controller_run_id",
    "original_request_id",
    "claim_sha256",
    "original_wait_sha256",
    "semantic_task_fingerprint",
    "task_id",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
    "run_prefix",
    "role",
    "claimed_at_utc",
    "timeout",
    "created_at",
})

_SUBMISSION_RECORD_KEYS = frozenset({
    "schema_version",
    "request_id",
    "original_controller_run_id",
    "claim_sha256",
    "request_sha256",
})

__all__ = [
    "MAX_SUBMISSION_RECORD_BYTES",
    "QUOTA_RESUME_SUBMISSION_FILENAME",
    "QuotaResumeSubmissionIntentError",
    "ResumeSubmissionIntentError",
    "SubmissionIntentError",
    "build_quota_resume_submission_record",
    "build_submission_record",
    "compute_canonical_request_sha256",
    "publish_quota_resume_submission_intent",
    "publish_submission_intent",
    "read_quota_resume_submission_intent",
    "read_quota_resume_submission_intent_bytes",
    "read_submission_intent",
    "validate_quota_resume_submission_record",
    "validate_submission_record",
]


class SubmissionIntentError(BrokerError):
    """Raised when quota resume submission intent validation, publication, or reading fails."""


QuotaResumeSubmissionIntentError = SubmissionIntentError
ResumeSubmissionIntentError = SubmissionIntentError


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


def _validate_continuation_request_shape(request: object) -> dict[str, Any]:
    if type(request) is not dict:
        raise SubmissionIntentError(
            f"continuation request must be dict, got {type(request).__name__}"
        )
    req_keys = set(request.keys())
    if req_keys != _REQUEST_KEYS:
        missing = sorted(_REQUEST_KEYS - req_keys)
        extra = sorted(req_keys - _REQUEST_KEYS)
        raise SubmissionIntentError(
            f"continuation request keys mismatch: missing={missing}, extra={extra}"
        )
    if type(request["schema_version"]) is not str or request["schema_version"] != "1.0":
        raise SubmissionIntentError(
            f"continuation request schema_version must be '1.0', got {request['schema_version']!r}"
        )
    if type(request["request_type"]) is not str or request["request_type"] != "QUOTA_CONTINUATION":
        raise SubmissionIntentError(
            f"continuation request request_type must be 'QUOTA_CONTINUATION', got {request['request_type']!r}"
        )
    req_id = request["request_id"]
    if type(req_id) is not str or not REQUEST_ID_RE.fullmatch(req_id):
        raise SubmissionIntentError(
            f"continuation request request_id must be 32 lowercase hex: {req_id!r}"
        )
    orig_req_id = request["original_request_id"]
    if type(orig_req_id) is not str or not REQUEST_ID_RE.fullmatch(orig_req_id):
        raise SubmissionIntentError(
            f"continuation request original_request_id must be 32 lowercase hex: {orig_req_id!r}"
        )
    ctrl_run_id = request["controller_run_id"]
    if type(ctrl_run_id) is not str or not CONTROLLER_RUN_RE.fullmatch(ctrl_run_id):
        raise SubmissionIntentError(
            f"continuation request controller_run_id is invalid: {ctrl_run_id!r}"
        )
    orig_cid = request["original_controller_run_id"]
    if type(orig_cid) is not str or not CONTROLLER_RUN_RE.fullmatch(orig_cid):
        raise SubmissionIntentError(
            f"continuation request original_controller_run_id is invalid: {orig_cid!r}"
        )
    for k in (
        "claim_sha256",
        "original_wait_sha256",
        "semantic_task_fingerprint",
        "task_sha256",
        "assignment_sha256",
    ):
        val = request[k]
        if type(val) is not str or not _HEX_64_RE.fullmatch(val):
            raise SubmissionIntentError(
                f"continuation request {k} must be 64 lowercase hex: {val!r}"
            )
    for k in (
        "task_id",
        "task_path",
        "source_head",
        "assignment_id",
        "run_prefix",
        "role",
        "claimed_at_utc",
    ):
        val = request[k]
        if type(val) is not str or not val:
            raise SubmissionIntentError(
                f"continuation request {k} must be non-empty string"
            )
    timeout = request["timeout"]
    if type(timeout) is not int or type(timeout) is bool or not (10 <= timeout <= 900):
        raise SubmissionIntentError(
            f"continuation request timeout must be exact int 10..900, got {timeout!r} ({type(timeout).__name__})"
        )
    created_at = request["created_at"]
    if type(created_at) is bool or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
        raise SubmissionIntentError(
            f"continuation request created_at must be finite numeric (not bool), got {created_at!r}"
        )
    return request


def compute_canonical_request_sha256(request: object) -> str:
    """Compute SHA-256 of canonical JSON encoding of a full continuation request."""
    validated = _validate_continuation_request_shape(request)
    try:
        canonical_text = json.dumps(
            validated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_bytes = canonical_text.encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise SubmissionIntentError(
            f"continuation request cannot be serialized to canonical JSON: {exc}"
        ) from exc
    return hashlib.sha256(canonical_bytes).hexdigest()


def validate_quota_resume_submission_record(
    record: object,
    expected_request: object = None,
) -> dict[str, Any]:
    """Validate a version 1.0 quota resume submission record against schema and expected full request."""
    if type(record) is not dict:
        raise SubmissionIntentError(f"submission record must be dict, got {type(record).__name__}")

    keys = set(record.keys())
    if keys != _SUBMISSION_RECORD_KEYS:
        missing = sorted(_SUBMISSION_RECORD_KEYS - keys)
        extra = sorted(keys - _SUBMISSION_RECORD_KEYS)
        raise SubmissionIntentError(
            f"submission record keys mismatch: missing={missing}, extra={extra}"
        )

    schema_version = record["schema_version"]
    if type(schema_version) is not str or schema_version != "1.0":
        raise SubmissionIntentError(f"schema_version must be '1.0', got {schema_version!r}")

    request_id = record["request_id"]
    if type(request_id) is not str or not REQUEST_ID_RE.fullmatch(request_id):
        raise SubmissionIntentError(f"request_id must be 32 lowercase hex: {request_id!r}")

    original_controller_run_id = record["original_controller_run_id"]
    if (
        type(original_controller_run_id) is not str
        or not CONTROLLER_RUN_RE.fullmatch(original_controller_run_id)
    ):
        raise SubmissionIntentError(
            f"original_controller_run_id is invalid: {original_controller_run_id!r}"
        )

    claim_sha256 = record["claim_sha256"]
    if type(claim_sha256) is not str or not _HEX_64_RE.fullmatch(claim_sha256):
        raise SubmissionIntentError(f"claim_sha256 must be 64 lowercase hex: {claim_sha256!r}")

    request_sha256 = record["request_sha256"]
    if type(request_sha256) is not str or not _HEX_64_RE.fullmatch(request_sha256):
        raise SubmissionIntentError(f"request_sha256 must be 64 lowercase hex: {request_sha256!r}")

    if expected_request is not None:
        validated_req = _validate_continuation_request_shape(expected_request)

        if request_id != validated_req["request_id"]:
            raise SubmissionIntentError(
                f"request_id mismatch: {request_id!r} != {validated_req['request_id']!r}"
            )
        if original_controller_run_id != validated_req["original_controller_run_id"]:
            raise SubmissionIntentError(
                f"original_controller_run_id mismatch: {original_controller_run_id!r} != {validated_req['original_controller_run_id']!r}"
            )
        if claim_sha256 != validated_req["claim_sha256"]:
            raise SubmissionIntentError(
                f"claim_sha256 mismatch: {claim_sha256!r} != {validated_req['claim_sha256']!r}"
            )
        exp_req_sha = compute_canonical_request_sha256(validated_req)
        if request_sha256 != exp_req_sha:
            raise SubmissionIntentError(
                f"request_sha256 mismatch: {request_sha256!r} != {exp_req_sha!r}"
            )

    return copy.deepcopy(record)


def build_quota_resume_submission_record(request: object) -> dict[str, Any]:
    """Pure builder for version 1.0 quota resume submission intent record from full continuation request."""
    validated = _validate_continuation_request_shape(request)
    record: dict[str, Any] = {
        "schema_version": "1.0",
        "request_id": validated["request_id"],
        "original_controller_run_id": validated["original_controller_run_id"],
        "claim_sha256": validated["claim_sha256"],
        "request_sha256": compute_canonical_request_sha256(validated),
    }
    return validate_quota_resume_submission_record(record, expected_request=validated)


build_submission_record = build_quota_resume_submission_record
validate_submission_record = validate_quota_resume_submission_record


def _stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (\
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
        raise SubmissionIntentError(f"{label} is not a regular file")
    if info.st_uid != os.geteuid():
        raise SubmissionIntentError(f"{label} owner mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SubmissionIntentError(f"{label} mode must be 0600")
    if info.st_nlink != 1:
        raise SubmissionIntentError(f"{label} has invalid link count")
    if info.st_size < 0 or info.st_size > MAX_SUBMISSION_RECORD_BYTES:
        raise SubmissionIntentError(f"{label} exceeds byte limit")


def read_quota_resume_submission_intent_bytes(
    root: Path,
    original_controller_run_id: str,
) -> bytes:
    """Read raw bytes of quota resume submission intent with pinned directory validation."""
    if not isinstance(root, Path):
        raise SubmissionIntentError("root must be a Path")
    if type(original_controller_run_id) is not str:
        raise SubmissionIntentError(
            f"original_controller_run_id must be exact str, got {type(original_controller_run_id).__name__}"
        )
    if not original_controller_run_id:
        raise SubmissionIntentError("original_controller_run_id must be a non-empty string")

    try:
        with _open_existing_cycle_run_directory(
            root, original_controller_run_id
        ) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise SubmissionIntentError(
                    f"directory identity verification failed before read: {exc}"
                ) from exc

            file_path = run_path / QUOTA_RESUME_SUBMISSION_FILENAME

            try:
                lstat_before = os.lstat(file_path)
            except OSError as exc:
                raise SubmissionIntentError(
                    f"cannot lstat {QUOTA_RESUME_SUBMISSION_FILENAME}: {exc}"
                ) from exc
            _validate_stat(lstat_before, f"{QUOTA_RESUME_SUBMISSION_FILENAME} path before open")
            initial_fingerprint = _stat_fingerprint(lstat_before)

            try:
                dirfd_stat_before = os.stat(
                    QUOTA_RESUME_SUBMISSION_FILENAME, dir_fd=run_fd, follow_symlinks=False
                )
            except OSError as exc:
                raise SubmissionIntentError(
                    f"cannot stat {QUOTA_RESUME_SUBMISSION_FILENAME} via dir_fd before open: {exc}"
                ) from exc
            _validate_stat(
                dirfd_stat_before, f"{QUOTA_RESUME_SUBMISSION_FILENAME} via dir_fd before open"
            )
            if _stat_fingerprint(dirfd_stat_before) != initial_fingerprint:
                raise SubmissionIntentError(
                    f"{QUOTA_RESUME_SUBMISSION_FILENAME} dir_fd stat mismatch before open"
                )

            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                file_fd = os.open(QUOTA_RESUME_SUBMISSION_FILENAME, flags, dir_fd=run_fd)
            except OSError as exc:
                raise SubmissionIntentError(
                    f"cannot safely open {QUOTA_RESUME_SUBMISSION_FILENAME}: {exc}"
                ) from exc

            try:
                try:
                    fstat_before = os.fstat(file_fd)
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"cannot fstat {QUOTA_RESUME_SUBMISSION_FILENAME} before read: {exc}"
                    ) from exc
                _validate_stat(
                    fstat_before, f"{QUOTA_RESUME_SUBMISSION_FILENAME} descriptor before read"
                )
                if _stat_fingerprint(fstat_before) != initial_fingerprint:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} descriptor metadata mismatch before read"
                    )

                expected_size = initial_fingerprint[5]
                chunks: list[bytes] = []
                total_read = 0
                while total_read < _READ_LIMIT:
                    try:
                        chunk = os.read(file_fd, _READ_LIMIT - total_read)
                    except OSError as exc:
                        raise SubmissionIntentError(
                            f"cannot read {QUOTA_RESUME_SUBMISSION_FILENAME}: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total_read += len(chunk)

                data = b"".join(chunks)
                if len(data) > MAX_SUBMISSION_RECORD_BYTES:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} exceeds byte limit"
                    )
                if len(data) != expected_size:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} read size mismatch"
                    )

                try:
                    fstat_after = os.fstat(file_fd)
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"cannot fstat {QUOTA_RESUME_SUBMISSION_FILENAME} after read: {exc}"
                    ) from exc
                _validate_stat(
                    fstat_after, f"{QUOTA_RESUME_SUBMISSION_FILENAME} descriptor after read"
                )
                if _stat_fingerprint(fstat_after) != initial_fingerprint:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} descriptor metadata changed after read"
                    )

                try:
                    dirfd_stat_after = os.stat(
                        QUOTA_RESUME_SUBMISSION_FILENAME, dir_fd=run_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"cannot stat {QUOTA_RESUME_SUBMISSION_FILENAME} via dir_fd after read: {exc}"
                    ) from exc
                _validate_stat(
                    dirfd_stat_after, f"{QUOTA_RESUME_SUBMISSION_FILENAME} via dir_fd after read"
                )
                if _stat_fingerprint(dirfd_stat_after) != initial_fingerprint:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} dir_fd metadata changed after read"
                    )

                try:
                    lstat_after = os.lstat(file_path)
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"cannot lstat {QUOTA_RESUME_SUBMISSION_FILENAME} after read: {exc}"
                    ) from exc
                _validate_stat(
                    lstat_after, f"{QUOTA_RESUME_SUBMISSION_FILENAME} path after read"
                )
                if _stat_fingerprint(lstat_after) != initial_fingerprint:
                    raise SubmissionIntentError(
                        f"{QUOTA_RESUME_SUBMISSION_FILENAME} path metadata changed after read"
                    )

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise SubmissionIntentError(
                        f"directory identity verification failed after read: {exc}"
                    ) from exc

                return data
            finally:
                try:
                    os.close(file_fd)
                except OSError as exc:
                    if sys.exc_info()[1] is None:
                        raise SubmissionIntentError(
                            f"cannot safely close {QUOTA_RESUME_SUBMISSION_FILENAME} descriptor: {exc}"
                        ) from exc
    except BrokerError as exc:
        raise SubmissionIntentError(f"cannot safely open cycle run directory: {exc}") from exc


def read_quota_resume_submission_intent(
    root: Path,
    original_controller_run_id: str,
    expected_request: object,
) -> dict[str, Any]:
    """Read and validate version 1.0 quota resume submission intent against expected full request."""
    if expected_request is None:
        raise SubmissionIntentError("expected_request must be provided")
    raw_bytes = read_quota_resume_submission_intent_bytes(root, original_controller_run_id)

    try:
        decoded = raw_bytes.decode("utf-8")
    except (KeyboardInterrupt, SystemExit):
        raise
    except UnicodeDecodeError as exc:
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} is not valid UTF-8: {exc}"
        ) from exc

    try:
        payload = unique_json(decoded)
    except (KeyboardInterrupt, SystemExit):
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} is not valid JSON: {exc}"
        ) from exc
    except Exception as exc:
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict) or type(payload) is not dict:
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} must be a JSON object, got {type(payload).__name__}"
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
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} cannot be serialized to canonical JSON: {exc}"
        ) from exc

    if raw_bytes != canonical_bytes:
        raise SubmissionIntentError(
            f"{QUOTA_RESUME_SUBMISSION_FILENAME} is not canonical JSON bytes"
        )

    return validate_quota_resume_submission_record(payload, expected_request=expected_request)


read_submission_intent = read_quota_resume_submission_intent


def publish_quota_resume_submission_intent(
    root: Path,
    request: object,
    *,
    now_epoch: object = None,
    pipeline_dir: Path | str = Path(".local/two-site-refactor"),
    scope_loader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Publish version 1.0 quota resume submission intent under caller-held Supervisor lock."""
    if not isinstance(root, Path):
        raise SubmissionIntentError("root must be a Path")
    if type(request) is not dict:
        raise SubmissionIntentError("request must be dict")

    try:
        admitted_request, _task_path, _scope = validate_quota_continuation_request(
            root,
            request,
            now_epoch=now_epoch,
            pipeline_dir=pipeline_dir,
            scope_loader=scope_loader,
        )
    except ContinuationAdmissionError as exc:
        raise SubmissionIntentError(f"continuation request admission failed: {exc}") from exc
    except BrokerError as exc:
        raise SubmissionIntentError(
            f"continuation request broker validation failed: {exc}"
        ) from exc
    except Exception as exc:
        raise SubmissionIntentError(
            f"unexpected failure validating continuation request: {exc}"
        ) from exc

    record = build_quota_resume_submission_record(admitted_request)
    original_controller_run_id = admitted_request["original_controller_run_id"]

    try:
        canonical_text = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_bytes = canonical_text.encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise SubmissionIntentError(
            f"submission record cannot be serialized to canonical JSON: {exc}"
        ) from exc

    if len(canonical_bytes) > MAX_SUBMISSION_RECORD_BYTES:
        raise SubmissionIntentError("submission record exceeds byte limit")

    temp_name = f".{QUOTA_RESUME_SUBMISSION_FILENAME}.tmp.{os.getpid()}"
    target_name = QUOTA_RESUME_SUBMISSION_FILENAME
    temp_created = False
    linked = False

    try:
        with _open_existing_cycle_run_directory(
            root, original_controller_run_id
        ) as (run_path, run_fd, verify_identity):
            try:
                verify_identity()
            except BrokerError as exc:
                raise SubmissionIntentError(
                    f"directory identity verification failed before write: {exc}"
                ) from exc

            file_path = run_path / target_name
            target_exists = False
            try:
                os.stat(target_name, dir_fd=run_fd, follow_symlinks=False)
                target_exists = True
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SubmissionIntentError(
                    f"cannot stat {target_name} via dir_fd: {exc}"
                ) from exc

            if not target_exists:
                try:
                    os.lstat(file_path)
                    target_exists = True
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise SubmissionIntentError(f"cannot lstat {target_name}: {exc}") from exc

            if target_exists:
                raise SubmissionIntentError(f"{target_name} already exists")

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
                    raise SubmissionIntentError(
                        f"cannot safely create temp file {temp_name}: {exc}"
                    ) from exc
                temp_created = True

                try:
                    total_written = 0
                    data_len = len(canonical_bytes)
                    while total_written < data_len:
                        try:
                            written = os.write(file_fd, canonical_bytes[total_written:])
                        except OSError as exc:
                            raise SubmissionIntentError(
                                f"cannot write {temp_name}: {exc}"
                            ) from exc
                        if written <= 0:
                            raise SubmissionIntentError(
                                f"write stalled while writing {temp_name}"
                            )
                        total_written += written

                    try:
                        os.fchmod(file_fd, 0o600)
                    except OSError as exc:
                        raise SubmissionIntentError(
                            f"cannot fchmod {temp_name}: {exc}"
                        ) from exc

                    try:
                        temp_stat = os.fstat(file_fd)
                    except OSError as exc:
                        raise SubmissionIntentError(
                            f"cannot fstat {temp_name}: {exc}"
                        ) from exc
                    _validate_stat(temp_stat, f"temp file {temp_name}")
                    if temp_stat.st_size != data_len:
                        raise SubmissionIntentError(f"temp file {temp_name} size mismatch")

                    try:
                        os.fsync(file_fd)
                    except OSError as exc:
                        raise SubmissionIntentError(
                            f"cannot fsync {temp_name}: {exc}"
                        ) from exc
                finally:
                    if file_fd is not None:
                        try:
                            os.close(file_fd)
                        except OSError as exc:
                            file_fd = None
                            if sys.exc_info()[1] is not None:
                                raise SubmissionIntentError(
                                    f"cannot safely close temp file {temp_name}: {exc}"
                                ) from sys.exc_info()[1]
                            raise SubmissionIntentError(
                                f"cannot safely close temp file {temp_name}: {exc}"
                            ) from exc
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
                    raise SubmissionIntentError(
                        f"cannot link {target_name}: target already exists"
                    ) from exc
                except OSError as exc:
                    raise SubmissionIntentError(f"cannot link {target_name}: {exc}") from exc
                linked = True

                try:
                    os.unlink(temp_name, dir_fd=run_fd)
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"ambiguous post-link publication failure: cannot unlink {temp_name}: {exc}"
                    ) from exc

                try:
                    os.fsync(run_fd)
                except OSError as exc:
                    raise SubmissionIntentError(
                        f"ambiguous post-link publication failure: cannot fsync directory: {exc}"
                    ) from exc

                try:
                    verify_identity()
                except BrokerError as exc:
                    raise SubmissionIntentError(
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
                            raise SubmissionIntentError(
                                f"failed to clean up temp file {temp_name}: {exc}"
                            ) from sys.exc_info()[1]
                        raise SubmissionIntentError(
                            f"failed to clean up temp file {temp_name}: {exc}"
                        ) from exc
    except SubmissionIntentError:
        raise
    except BrokerError as exc:
        if linked:
            raise SubmissionIntentError(
                f"ambiguous post-link publication failure: cycle run directory error: {exc}"
            ) from exc
        raise SubmissionIntentError(f"cannot safely open cycle run directory: {exc}") from exc
    except OSError as exc:
        if linked:
            raise SubmissionIntentError(
                f"ambiguous post-link publication failure: {exc}"
            ) from exc
        raise SubmissionIntentError(
            f"cannot safely access cycle run directory: {exc}"
        ) from exc
    except Exception as exc:
        if linked:
            raise SubmissionIntentError(
                f"ambiguous post-link publication failure: {exc}"
            ) from exc
        raise

    try:
        readback_record = read_quota_resume_submission_intent(
            root, original_controller_run_id, admitted_request
        )
    except SubmissionIntentError as exc:
        raise SubmissionIntentError(
            f"ambiguous post-link publication failure: readback failed: {exc}"
        ) from exc
    except Exception as exc:
        raise SubmissionIntentError(
            f"ambiguous post-link publication failure: readback unexpected error: {exc}"
        ) from exc

    if not _type_sensitive_equal(readback_record, record):
        raise SubmissionIntentError(
            "ambiguous post-link publication failure: readback record mismatch"
        )

    return copy.deepcopy(readback_record)


publish_submission_intent = publish_quota_resume_submission_intent
