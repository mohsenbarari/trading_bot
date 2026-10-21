"""Read-only admission verification for quota continuation requests."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
import sys
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import (
    CONTROLLER_RUN_RE,
    REQUEST_ID_RE,
    BrokerError,
    canonical_root,
    validate_request,
)
from scripts.two_site_quota_resume_claim_admission import (
    ClaimAdmissionError,
    prepare_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_claim_storage import (
    ClaimStorageError,
    read_known_reset_resume_claim,
)
from scripts.two_site_quota_resume_request_record import (
    ResumeRequestRecordError,
    validate_quota_resume_request,
)
from scripts.two_site_quota_wait_intent import (
    WaitIntentError,
    read_pending_rescope_intent,
)
from scripts.two_site_quota_wait_sources import (
    WaitSourceError,
    collect_initial_wait_source_expected,
)
from scripts.two_site_quota_wait_storage import (
    WaitStorageError,
    read_initial_wait_record,
    read_initial_wait_record_bytes,
)
from scripts.two_site_semantic_attempts import (
    SemanticAttemptError,
    read_existing_semantic_attempt,
)

__all__ = [
    "ContinuationAdmissionError",
    "admit_quota_continuation_request",
    "validate_quota_continuation_request",
]

_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_STOP_REL_PATH = Path(".local") / "two-site-refactor" / "autopilot" / "STOP"

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

_COMMON_REQUEST_KEYS = (
    "schema_version",
    "request_id",
    "run_prefix",
    "timeout",
    "source_head",
    "task_sha256",
    "controller_run_id",
    "created_at",
    "task_path",
    "assignment_id",
    "assignment_sha256",
)


class ContinuationAdmissionError(BrokerError):
    """Raised when quota continuation request admission validation fails."""


_NORMAL_PIPELINE_ROOT: ContextVar[Path | None] = ContextVar("two_site_normal_pipeline_root", default=None)
_LEGACY_PIPELINE = Path(".local/two-site-refactor")
_NORMAL_PIPELINE = _LEGACY_PIPELINE / "pipeline"


def _check_admission_module_identity() -> None:
    canonical = sys.modules.get("scripts.two_site_quota_continuation_admission")
    bare = sys.modules.get("two_site_quota_continuation_admission")
    if canonical is not None and bare is not None and canonical is not bare:
        raise ContinuationAdmissionError("duplicate canonical/bare admission modules")


def quota_semantic_directory(root: Path, pipeline_dir: Path | str = _LEGACY_PIPELINE) -> Path:
    """Resolve the fixed execution profile; never infer a profile from files."""
    _check_admission_module_identity()
    canonical = canonical_root(root)
    active = _NORMAL_PIPELINE_ROOT.get()
    if active is None:
        return Path(pipeline_dir)
    if canonical != active:
        raise ContinuationAdmissionError("NORMAL semantic context belongs to another root")
    supplied = Path(pipeline_dir)
    if supplied not in {_LEGACY_PIPELINE, _NORMAL_PIPELINE,
                        active / _LEGACY_PIPELINE, active / _NORMAL_PIPELINE}:
        raise ContinuationAdmissionError("unknown semantic directory inside NORMAL context")
    return _NORMAL_PIPELINE


@contextmanager
def normal_pipeline_context(root: Path):
    """Bind the whole synchronous normal Broker/Supervisor call to NORMAL."""
    _check_admission_module_identity()
    canonical = canonical_root(root)
    active = _NORMAL_PIPELINE_ROOT.get()
    if active is not None and active != canonical:
        raise ContinuationAdmissionError("cross-root NORMAL context nesting")
    token = _NORMAL_PIPELINE_ROOT.set(canonical)
    primary = None
    try:
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            _NORMAL_PIPELINE_ROOT.reset(token)
        except BaseException as cleanup:
            if primary is not None:
                raise primary from cleanup
            raise


def _type_sensitive_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_type_sensitive_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_type_sensitive_equal(x, y) for x, y in zip(a, b))
    return bool(a == b)


def _scopes_equal(s1: Any, s2: Any) -> bool:
    if type(s1) is not type(s2):
        return False
    if s1 != s2:
        return False
    if hasattr(s1, "__dict__") and hasattr(s2, "__dict__"):
        if not _type_sensitive_equal(vars(s1), vars(s2)):
            return False
    return True


def _check_wait_pair_identity(
    raw_bytes: bytes,
    record: dict[str, Any],
    *,
    label: str = "initial wait record",
) -> None:
    try:
        canonical_text = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_bytes = canonical_text.encode("utf-8")
    except Exception as exc:
        raise ContinuationAdmissionError(f"{label} cannot be serialized to canonical JSON: {exc}") from exc
    if raw_bytes != canonical_bytes:
        raise ContinuationAdmissionError(f"{label} bytes do not match canonical validated record")


def _check_stop(root: Path) -> None:
    if not isinstance(root, Path):
        raise ContinuationAdmissionError("root must be a Path")
    try:
        c_root = canonical_root(root)
    except (BrokerError, OSError) as exc:
        raise ContinuationAdmissionError(f"failed to inspect autopilot STOP sentinel: {exc}") from exc
    stop_path = c_root / _STOP_REL_PATH
    try:
        if os.path.lexists(stop_path):
            raise ContinuationAdmissionError(f"autopilot STOP sentinel exists: {stop_path}")
        os.lstat(stop_path)
        raise ContinuationAdmissionError(f"autopilot STOP sentinel exists: {stop_path}")
    except FileNotFoundError:
        pass
    except ContinuationAdmissionError:
        raise
    except OSError as exc:
        raise ContinuationAdmissionError(f"failed to inspect autopilot STOP sentinel: {exc}") from exc


def validate_quota_continuation_request(
    root: Path,
    request: object,
    *,
    scope_loader: Callable[[Path], Any] | None = None,
    now_epoch: object = None,
    pipeline_dir: Path | str = Path(".local/two-site-refactor"),
) -> tuple[dict[str, Any], Path, Any]:
    """Perform read-only admission and strict provenance verification for QUOTA_CONTINUATION."""
    if not isinstance(root, Path):
        raise ContinuationAdmissionError("root must be a Path")

    try:
        c_root = canonical_root(root)
    except (BrokerError, OSError) as exc:
        raise ContinuationAdmissionError(f"failed to canonicalize broker root: {exc}") from exc

    if type(request) is not dict:
        raise ContinuationAdmissionError(f"request must be dict, got {type(request).__name__}")

    pipeline_dir = quota_semantic_directory(c_root, pipeline_dir)
    from scripts.two_site_quota_retention import QuotaRetentionError, assert_not_retired
    try:
        assert_not_retired(c_root, request=request)
    except QuotaRetentionError as exc:
        raise ContinuationAdmissionError("quota identity is retired or uncertain") from exc

    req_keys = set(request.keys())
    if req_keys != _REQUEST_KEYS:
        missing = sorted(_REQUEST_KEYS - req_keys)
        extra = sorted(req_keys - _REQUEST_KEYS)
        raise ContinuationAdmissionError(
            f"continuation request keys mismatch: missing={missing}, extra={extra}"
        )

    if request.get("schema_version") != "1.0":
        raise ContinuationAdmissionError("continuation request schema_version must be '1.0'")
    if request.get("request_type") != "QUOTA_CONTINUATION":
        raise ContinuationAdmissionError("continuation request request_type must be 'QUOTA_CONTINUATION'")

    resume_request_id = request.get("request_id")
    if not isinstance(resume_request_id, str) or not REQUEST_ID_RE.fullmatch(resume_request_id):
        raise ContinuationAdmissionError("continuation request_id is invalid 32-hex")

    orig_request_id = request.get("original_request_id")
    if not isinstance(orig_request_id, str) or not REQUEST_ID_RE.fullmatch(orig_request_id):
        raise ContinuationAdmissionError("continuation original_request_id is invalid 32-hex")
    if resume_request_id == orig_request_id:
        raise ContinuationAdmissionError("continuation request_id must differ from original_request_id")

    ctrl_run_id = request.get("controller_run_id")
    if not isinstance(ctrl_run_id, str) or not CONTROLLER_RUN_RE.fullmatch(ctrl_run_id):
        raise ContinuationAdmissionError("continuation controller_run_id is invalid")

    orig_ctrl_run_id = request.get("original_controller_run_id")
    if not isinstance(orig_ctrl_run_id, str) or not CONTROLLER_RUN_RE.fullmatch(orig_ctrl_run_id):
        raise ContinuationAdmissionError("continuation original_controller_run_id is invalid")
    if ctrl_run_id == orig_ctrl_run_id:
        raise ContinuationAdmissionError("continuation controller_run_id must differ from original_controller_run_id")

    claimed_at_utc = request.get("claimed_at_utc")
    if not isinstance(claimed_at_utc, str) or not claimed_at_utc:
        raise ContinuationAdmissionError("continuation claimed_at_utc must be a non-empty string")

    claim_sha256 = request.get("claim_sha256")
    if not isinstance(claim_sha256, str) or not _HEX_64_RE.fullmatch(claim_sha256):
        raise ContinuationAdmissionError("continuation claim_sha256 must be 64 lowercase hex")

    orig_wait_sha256 = request.get("original_wait_sha256")
    if not isinstance(orig_wait_sha256, str) or not _HEX_64_RE.fullmatch(orig_wait_sha256):
        raise ContinuationAdmissionError("continuation original_wait_sha256 must be 64 lowercase hex")

    if now_epoch is not None:
        if type(now_epoch) is bool or not isinstance(now_epoch, (int, float)) or not math.isfinite(now_epoch):
            raise ContinuationAdmissionError(
                f"now_epoch must be finite numeric (not bool), got {now_epoch!r}"
            )

    _check_stop(c_root)

    common_request = {k: request[k] for k in _COMMON_REQUEST_KEYS}
    val_kwargs: dict[str, Any] = {}
    if scope_loader is not None:
        val_kwargs["scope_loader"] = scope_loader

    try:
        common_validated_1, task_path_1, scope_1 = validate_request(c_root, common_request, **val_kwargs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"initial common request validation failed: {exc}") from exc

    try:
        intent_epoch, intent_attempt, intent_repo = read_pending_rescope_intent(c_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to read pending rescope intent: {exc}") from exc

    try:
        expected_wait = collect_initial_wait_source_expected(
            c_root,
            orig_ctrl_run_id,
            intent_epoch,
            intent_attempt,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to collect initial wait source expected: {exc}") from exc

    expected_repo = expected_wait.get("repository")
    if not _type_sensitive_equal(expected_repo, intent_repo):
        raise ContinuationAdmissionError("expected wait repository does not match rescope intent repository")

    try:
        raw_wait_bytes = read_initial_wait_record_bytes(c_root, orig_ctrl_run_id)
        wait_record = read_initial_wait_record(c_root, orig_ctrl_run_id, expected_wait)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to read initial wait record: {exc}") from exc

    computed_wait_sha = hashlib.sha256(raw_wait_bytes).hexdigest()
    if computed_wait_sha != orig_wait_sha256:
        raise ContinuationAdmissionError(
            f"original wait SHA mismatch: computed {computed_wait_sha} != request {orig_wait_sha256}"
        )

    _check_wait_pair_identity(raw_wait_bytes, wait_record, label="initial wait record")

    if wait_record.get("request_id") != orig_request_id:
        raise ContinuationAdmissionError(
            f"original request ID mismatch: wait record {wait_record.get('request_id')!r} != request {orig_request_id!r}"
        )

    try:
        task, _sources = load_task(c_root, task_path_1)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to load task snapshot: {exc}") from exc

    if not isinstance(task, dict):
        raise ContinuationAdmissionError("loaded task snapshot is not a dict")
    if task.get("task_id") != request.get("task_id"):
        raise ContinuationAdmissionError(
            f"task_id mismatch: task {task.get('task_id')!r} != request {request.get('task_id')!r}"
        )

    orig_reservation = expected_wait.get("reservation")
    if not isinstance(orig_reservation, dict) or "run_prefix" not in orig_reservation:
        raise ContinuationAdmissionError("expected wait reservation missing run_prefix")
    orig_run_prefix = orig_reservation["run_prefix"]

    try:
        semantic_attempt = read_existing_semantic_attempt(
            c_root,
            pipeline_dir,
            task,
            orig_run_prefix,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to read existing semantic attempt: {exc}") from exc

    if semantic_attempt.get("fingerprint") != request.get("semantic_task_fingerprint"):
        raise ContinuationAdmissionError(
            f"semantic fingerprint mismatch: attempt {semantic_attempt.get('fingerprint')!r} != request {request.get('semantic_task_fingerprint')!r}"
        )
    if semantic_attempt.get("fingerprint") != expected_wait.get("semantic_task_fingerprint"):
        raise ContinuationAdmissionError(
            f"semantic fingerprint mismatch: attempt {semantic_attempt.get('fingerprint')!r} != wait {expected_wait.get('semantic_task_fingerprint')!r}"
        )
    if semantic_attempt.get("task_id") != request.get("task_id"):
        raise ContinuationAdmissionError(
            f"semantic attempt task_id mismatch: attempt {semantic_attempt.get('task_id')!r} != request {request.get('task_id')!r}"
        )

    try:
        claim_prep = prepare_known_reset_resume_claim(
            wait_record=wait_record,
            expected_wait=expected_wait,
            semantic_attempt=semantic_attempt,
            expected_task_id=request["task_id"],
            original_wait_sha256=orig_wait_sha256,
            continuation_controller_run_id=ctrl_run_id,
            resume_request_id=resume_request_id,
            claimed_at_utc=claimed_at_utc,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"known reset resume claim preparation failed: {exc}") from exc

    expected_claim = claim_prep["expected"]

    try:
        stored_claim = read_known_reset_resume_claim(c_root, orig_ctrl_run_id, expected_claim)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"failed to read known reset resume claim: {exc}") from exc

    try:
        canonical_claim_text = json.dumps(
            stored_claim,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        canonical_claim_bytes = canonical_claim_text.encode("utf-8")
    except Exception as exc:
        raise ContinuationAdmissionError(f"cannot serialize stored claim to canonical JSON: {exc}") from exc

    computed_claim_sha = hashlib.sha256(canonical_claim_bytes).hexdigest()
    if computed_claim_sha != claim_sha256:
        raise ContinuationAdmissionError(
            f"claim SHA mismatch: computed {computed_claim_sha} != request {claim_sha256}"
        )

    effective_now = time.time() if now_epoch is None else now_epoch
    try:
        validated_request = validate_quota_resume_request(
            request=request,
            claim=stored_claim,
            expected_claim=expected_claim,
            expected_claim_sha256=computed_claim_sha,
            now_epoch=effective_now,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"quota resume request validation failed: {exc}") from exc

    # Re-run provenance readers to detect drift during admission
    try:
        intent_epoch_2, intent_attempt_2, intent_repo_2 = read_pending_rescope_intent(c_root)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"rescope intent re-read failed: {exc}") from exc

    if intent_epoch_2 != intent_epoch or intent_attempt_2 != intent_attempt:
        raise ContinuationAdmissionError("rescope intent epoch or attempt drifted during admission")
    if not _type_sensitive_equal(intent_repo_2, intent_repo):
        raise ContinuationAdmissionError("rescope intent repository drifted during admission")

    try:
        expected_wait_2 = collect_initial_wait_source_expected(
            c_root,
            orig_ctrl_run_id,
            intent_epoch_2,
            intent_attempt_2,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"initial wait expected re-collect failed: {exc}") from exc

    if not _type_sensitive_equal(expected_wait_2, expected_wait):
        raise ContinuationAdmissionError("initial wait expected state drifted during admission")

    try:
        raw_wait_bytes_2 = read_initial_wait_record_bytes(c_root, orig_ctrl_run_id)
        wait_record_2 = read_initial_wait_record(c_root, orig_ctrl_run_id, expected_wait_2)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"initial wait record re-read failed: {exc}") from exc

    _check_wait_pair_identity(raw_wait_bytes_2, wait_record_2, label="initial wait record re-read")

    if raw_wait_bytes_2 != raw_wait_bytes:
        raise ContinuationAdmissionError("initial wait record bytes drifted during admission")
    if not _type_sensitive_equal(wait_record_2, wait_record):
        raise ContinuationAdmissionError("initial wait record drifted during admission")

    try:
        task_2, _ = load_task(c_root, task_path_1)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"task re-read failed: {exc}") from exc

    if not _type_sensitive_equal(task_2, task):
        raise ContinuationAdmissionError("task snapshot drifted during admission")

    try:
        semantic_attempt_2 = read_existing_semantic_attempt(
            c_root,
            pipeline_dir,
            task_2,
            orig_run_prefix,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"semantic attempt re-read failed: {exc}") from exc

    if not _type_sensitive_equal(semantic_attempt_2, semantic_attempt):
        raise ContinuationAdmissionError("semantic attempt drifted during admission")

    try:
        stored_claim_2 = read_known_reset_resume_claim(c_root, orig_ctrl_run_id, expected_claim)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"claim re-read failed: {exc}") from exc

    if not _type_sensitive_equal(stored_claim_2, stored_claim):
        raise ContinuationAdmissionError("stored claim drifted during admission")

    try:
        common_validated_2, task_path_2, scope_2 = validate_request(c_root, common_request, **val_kwargs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise ContinuationAdmissionError(f"common request validation re-run failed: {exc}") from exc

    if not _type_sensitive_equal(common_validated_1, common_validated_2):
        raise ContinuationAdmissionError("common validated request projection drifted between invocations")
    if type(task_path_1) is not type(task_path_2) or task_path_1 != task_path_2:
        raise ContinuationAdmissionError("task path drifted between common validation invocations")
    if not _scopes_equal(scope_1, scope_2):
        raise ContinuationAdmissionError("assignment scope drifted between common validation invocations")

    _check_stop(c_root)

    return copy.deepcopy(validated_request), task_path_2, scope_2


admit_quota_continuation_request = validate_quota_continuation_request
