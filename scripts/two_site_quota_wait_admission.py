"""Admission logic for initial quota wait state under existing Supervisor lock."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable

from scripts.two_site_codex_autopilot import AutopilotError
from scripts.two_site_provider_broker import (
    BrokerError,
    _open_existing_cycle_run_directory,
    canonical_root,
)
from scripts.two_site_quota_wait_intent import (
    WaitIntentError,
    read_pending_rescope_intent,
)
from scripts.two_site_quota_wait_record import (
    WaitRecordError,
    validate_initial_wait_record,
)
from scripts.two_site_quota_wait_schedule import (
    QuotaWaitScheduleError,
    initial_wait_schedule,
)
from scripts.two_site_quota_wait_sources import (
    WaitSourceError,
    collect_initial_wait_source_expected,
)
from scripts.two_site_quota_wait_storage import (
    MAX_WAIT_RECORD_BYTES,
    QUOTA_WAIT_FILENAME,
    WaitStorageError,
    publish_initial_wait_record_bytes,
    read_initial_wait_record,
)

__all__ = [
    "WaitAdmissionError",
    "admit_initial_quota_wait_locked",
]


class WaitAdmissionError(ValueError):
    """Raised when initial quota wait admission fails."""


def _type_sensitive_equal(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        for k, v in a.items():
            if not _type_sensitive_equal(v, b[k]):
                return False
        return True
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if not _type_sensitive_equal(x, y):
                return False
        return True
    return a == b


def _check_stop_sentinel(root: Path) -> None:
    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    try:
        os.lstat(stop_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WaitAdmissionError(
            f"cannot stat autopilot STOP sentinel {stop_path}: {exc}"
        ) from exc
    raise WaitAdmissionError(f"autopilot STOP sentinel exists: {stop_path}")


def admit_initial_quota_wait_locked(
    root: Path,
    controller_run_id: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Admit initial quota wait state under an existing Supervisor exclusive lock.

    Caller MUST own existing Supervisor exclusive lock; this isolated function
    DOES NOT call Provider, grant wake/replay authority, start scheduler, or
    wire Supervisor runtime.
    """
    if not isinstance(root, Path):
        raise WaitAdmissionError("root must be a Path")
    if not isinstance(controller_run_id, str) or not controller_run_id:
        raise WaitAdmissionError("controller_run_id must be a non-empty string")
    if clock is not None and not callable(clock):
        raise WaitAdmissionError("clock must be callable if provided")

    try:
        _check_stop_sentinel(root)
        c_root = canonical_root(root)
        if c_root != root:
            _check_stop_sentinel(c_root)

        epoch, attempt, normalized_repo = read_pending_rescope_intent(c_root)
        expected = collect_initial_wait_source_expected(
            c_root, controller_run_id, epoch, attempt
        )
        if not _type_sensitive_equal(expected.get("repository"), normalized_repo):
            raise WaitAdmissionError(
                "collected wait source repository does not match pending rescope intent repository"
            )

        existing_found = False
        with _open_existing_cycle_run_directory(
            c_root, controller_run_id
        ) as (run_path, run_fd, verify_identity):
            verify_identity()
            try:
                os.stat(QUOTA_WAIT_FILENAME, dir_fd=run_fd, follow_symlinks=False)
                existing_found = True
            except FileNotFoundError:
                existing_found = False
            except OSError as exc:
                raise WaitAdmissionError(
                    f"cannot stat {QUOTA_WAIT_FILENAME} via dir_fd: {exc}"
                ) from exc

        if existing_found:
            _check_stop_sentinel(c_root)
            return read_initial_wait_record(c_root, controller_run_id, expected)

        re_epoch, re_attempt, re_normalized_repo = read_pending_rescope_intent(c_root)
        re_expected = collect_initial_wait_source_expected(
            c_root, controller_run_id, re_epoch, re_attempt
        )
        if not _type_sensitive_equal(
            re_expected.get("repository"), re_normalized_repo
        ):
            raise WaitAdmissionError(
                "re-verified wait source repository does not match re-verified intent repository"
            )
        if (
            re_epoch != epoch
            or re_attempt != attempt
            or not _type_sensitive_equal(re_normalized_repo, normalized_repo)
            or not _type_sensitive_equal(re_expected, expected)
        ):
            raise WaitAdmissionError(
                "rescope intent or wait source changed between initial proof and publication"
            )

        if clock is None:
            now_dt = datetime.now(timezone.utc)
        else:
            now_dt = clock()

        if not isinstance(now_dt, datetime):
            raise WaitAdmissionError(
                f"clock must return a datetime instance, got {type(now_dt).__name__}"
            )
        if now_dt.tzinfo is None or now_dt.utcoffset() is None:
            raise WaitAdmissionError("clock datetime must be timezone-aware")

        utc_dt = now_dt.astimezone(timezone.utc)
        timespec = "microseconds" if utc_dt.microsecond else "seconds"
        now_string = utc_dt.isoformat(timespec=timespec).replace("+00:00", "Z")

        schedule = initial_wait_schedule(re_expected["quota_evidence"], now_string)

        record: dict[str, Any] = {
            "schema_version": "1.0",
            "state": "WAITING_QUOTA",
            "controller_run_id": re_expected["controller_run_id"],
            "request_id": re_expected["request_id"],
            "reservation": re_expected["reservation"],
            "request_sha256": re_expected["request_sha256"],
            "terminal_receipt_sha256": re_expected["terminal_receipt_sha256"],
            "response_sha256": re_expected["response_sha256"],
            "semantic_task_fingerprint": re_expected["semantic_task_fingerprint"],
            "rescope_epoch": re_expected["rescope_epoch"],
            "rescope_attempt": re_expected["rescope_attempt"],
            "quota_attempt": re_expected["quota_attempt"],
            "quota_evidence": re_expected["quota_evidence"],
            "schedule": schedule,
            "repository": re_expected["repository"],
        }

        validate_initial_wait_record(record, re_expected)

        try:
            canonical_text = json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            record_bytes = canonical_text.encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise WaitAdmissionError(
                f"failed to canonically serialize wait record: {exc}"
            ) from exc

        if len(record_bytes) > MAX_WAIT_RECORD_BYTES:
            raise WaitAdmissionError(
                f"serialized wait record exceeds maximum byte limit of {MAX_WAIT_RECORD_BYTES}: {len(record_bytes)}"
            )

        _check_stop_sentinel(c_root)

        publish_initial_wait_record_bytes(c_root, controller_run_id, record_bytes)

        published_record = read_initial_wait_record(
            c_root, controller_run_id, re_expected
        )
        if not _type_sensitive_equal(published_record, record):
            raise WaitAdmissionError(
                "readback wait record does not match published wait record"
            )

        return published_record

    except (KeyboardInterrupt, SystemExit):
        raise
    except WaitAdmissionError:
        raise
    except (
        AutopilotError,
        BrokerError,
        WaitIntentError,
        WaitSourceError,
        WaitRecordError,
        WaitStorageError,
        QuotaWaitScheduleError,
        OSError,
        ValueError,
    ) as exc:
        raise WaitAdmissionError(
            f"initial quota wait admission failed: {exc}"
        ) from exc
