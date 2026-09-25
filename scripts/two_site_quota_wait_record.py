"""Pure validation for durable quota wait state records.

This module provides shape and equality verification for initial quota wait records.
It performs pure validation only and NEVER grants wake or replay execution authority.
"""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from scripts.two_site_quota_wait_schedule import initial_wait_schedule
except ImportError:
    from two_site_quota_wait_schedule import initial_wait_schedule

__all__ = ["WaitRecordError", "validate_initial_wait_record"]

_CYCLE_RUN_RE = re.compile(r"^cycle-[0-9]{4,8}-[0-9]{8}T[0-9]{6}Z-[0-9]{1,10}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
_RESCOPE_EPOCH_RE = re.compile(r"^rescope-[0-9a-f]{64}$")
_RUN_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
_CANONICAL_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ASSIGNMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_RECORD_KEYS = frozenset({
    "schema_version",
    "state",
    "controller_run_id",
    "request_id",
    "reservation",
    "request_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "semantic_task_fingerprint",
    "rescope_epoch",
    "rescope_attempt",
    "quota_attempt",
    "quota_evidence",
    "schedule",
    "repository",
})

_EXPECTED_KEYS = frozenset({
    "controller_run_id",
    "request_id",
    "reservation",
    "request_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "semantic_task_fingerprint",
    "rescope_epoch",
    "rescope_attempt",
    "quota_attempt",
    "quota_evidence",
    "repository",
})

_RESERVATION_KEYS = frozenset({
    "controller_run_id",
    "run_prefix",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
})

_QUOTA_ATTEMPT_KEYS = frozenset({
    "role",
    "run_id",
    "status_sha256",
})

_QUOTA_EVIDENCE_KEYS = frozenset({
    "kind",
    "provider",
    "model",
    "conversation_id",
    "reset_at_utc",
})

_SCHEDULE_KEYS = frozenset({
    "reset_kind",
    "reset_at_utc",
    "created_at_utc",
    "next_check_at_utc",
    "remaining_check_budget",
    "remaining_resume_budget",
})

_REPOSITORY_KEYS = frozenset({
    "head",
    "status",
    "protected_refs",
    "control_blobs",
    "assignment_scope",
})

_ASSIGNMENT_SCOPE_KEYS = frozenset({
    "assignment_id",
    "stage_id",
    "sha256",
    "expires_at",
    "allowed_paths",
    "forbidden_paths",
})


class WaitRecordError(ValueError):
    """Raised when a quota wait record is invalid or fails expected binding."""


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


def validate_initial_wait_record(record: object, expected: object) -> dict[str, Any]:
    """Validate shape and expected identity binding for an initial quota wait record."""
    if type(record) is not dict:
        raise WaitRecordError(f"record must be dict, got {type(record).__name__}")
    if set(record.keys()) != _RECORD_KEYS:
        raise WaitRecordError(f"record keys must exactly match {sorted(_RECORD_KEYS)!r}")

    if type(expected) is not dict:
        raise WaitRecordError(f"expected must be dict, got {type(expected).__name__}")
    if set(expected.keys()) != _EXPECTED_KEYS:
        raise WaitRecordError(f"expected keys must exactly match {sorted(_EXPECTED_KEYS)!r}")

    if record["schema_version"] != "1.0" or type(record["schema_version"]) is not str:
        raise WaitRecordError(f"schema_version must be '1.0', got {record['schema_version']!r}")

    if record["state"] != "WAITING_QUOTA" or type(record["state"]) is not str:
        raise WaitRecordError(f"state must be 'WAITING_QUOTA', got {record['state']!r}")

    ctrl_id = record["controller_run_id"]
    if type(ctrl_id) is not str or not _CYCLE_RUN_RE.fullmatch(ctrl_id):
        raise WaitRecordError(f"controller_run_id must match cycle run pattern: {ctrl_id!r}")

    req_id = record["request_id"]
    if type(req_id) is not str or not _REQUEST_ID_RE.fullmatch(req_id):
        raise WaitRecordError(f"request_id must be 32 lowercase hex: {req_id!r}")

    res = record["reservation"]
    if type(res) is not dict or set(res.keys()) != _RESERVATION_KEYS:
        raise WaitRecordError("reservation must contain exact seven reservation keys")
    for k in _RESERVATION_KEYS:
        if type(res[k]) is not str:
            raise WaitRecordError(f"reservation field {k} must be str, got {type(res[k]).__name__}")

    if res["controller_run_id"] != ctrl_id:
        raise WaitRecordError("reservation controller_run_id does not match outer controller_run_id")
    if not _RUN_PREFIX_RE.fullmatch(res["run_prefix"]):
        raise WaitRecordError(f"reservation run_prefix is invalid: {res['run_prefix']!r}")

    task_path = res["task_path"]
    if "\\" in task_path or task_path.startswith("/") or task_path.endswith("/"):
        raise WaitRecordError(f"reservation task_path is not canonical relative: {task_path!r}")
    parts = task_path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise WaitRecordError(f"reservation task_path contains empty or dot components: {task_path!r}")
    if parts[:3] != [".local", "two-site-refactor", "tasks"] or len(parts) < 4:
        raise WaitRecordError(f"reservation task_path must be under .local/two-site-refactor/tasks: {task_path!r}")

    if not _SHA256_RE.fullmatch(res["task_sha256"]):
        raise WaitRecordError(f"reservation task_sha256 must be 64 lowercase hex: {res['task_sha256']!r}")
    if not _HEAD_RE.fullmatch(res["source_head"]):
        raise WaitRecordError(f"reservation source_head must be 40 lowercase hex: {res['source_head']!r}")
    if not _ASSIGNMENT_ID_RE.fullmatch(res["assignment_id"]):
        raise WaitRecordError(f"reservation assignment_id is invalid: {res['assignment_id']!r}")
    if not _SHA256_RE.fullmatch(res["assignment_sha256"]):
        raise WaitRecordError(f"reservation assignment_sha256 must be 64 lowercase hex: {res['assignment_sha256']!r}")

    for digest_key in ("request_sha256", "terminal_receipt_sha256", "response_sha256", "semantic_task_fingerprint"):
        val = record[digest_key]
        if type(val) is not str or not _SHA256_RE.fullmatch(val):
            raise WaitRecordError(f"{digest_key} must be 64 lowercase hex: {val!r}")

    epoch = record["rescope_epoch"]
    if type(epoch) is not str or not _RESCOPE_EPOCH_RE.fullmatch(epoch):
        raise WaitRecordError(f"rescope_epoch must match 'rescope-[0-9a-f]{{64}}': {epoch!r}")

    attempt = record["rescope_attempt"]
    if type(attempt) is not int or not (1 <= attempt <= 3):
        raise WaitRecordError(f"rescope_attempt must be int in range 1..3: {attempt!r}")

    qa = record["quota_attempt"]
    if type(qa) is not dict or set(qa.keys()) != _QUOTA_ATTEMPT_KEYS:
        raise WaitRecordError("quota_attempt must contain exact keys ('role', 'run_id', 'status_sha256')")
    if qa["role"] not in ("worker", "verifier") or type(qa["role"]) is not str:
        raise WaitRecordError(f"quota_attempt role must be 'worker' or 'verifier': {qa.get('role')!r}")
    if type(qa["run_id"]) is not str or not _CANONICAL_RUN_ID_RE.fullmatch(qa["run_id"]):
        raise WaitRecordError(f"quota_attempt run_id is invalid: {qa.get('run_id')!r}")
    if type(qa["status_sha256"]) is not str or not _SHA256_RE.fullmatch(qa["status_sha256"]):
        raise WaitRecordError(f"quota_attempt status_sha256 must be 64 lowercase hex: {qa.get('status_sha256')!r}")

    qe = record["quota_evidence"]
    if type(qe) is not dict or set(qe.keys()) != _QUOTA_EVIDENCE_KEYS:
        raise WaitRecordError("quota_evidence must contain exact five producer keys")

    sch = record["schedule"]
    if type(sch) is not dict or set(sch.keys()) != _SCHEDULE_KEYS:
        raise WaitRecordError("schedule must contain exact six schedule keys")

    created_at_utc = sch.get("created_at_utc")
    try:
        recomputed = initial_wait_schedule(qe, created_at_utc)
    except Exception as exc:
        raise WaitRecordError(f"schedule recomputation failed: {exc}") from exc

    if not _type_sensitive_equal(sch, recomputed):
        raise WaitRecordError("schedule does not match type-sensitive recomputation from evidence")

    repo = record["repository"]
    if type(repo) is not dict or set(repo.keys()) != _REPOSITORY_KEYS:
        raise WaitRecordError("repository must contain exact five repository keys")
    if repo["status"] != "" or type(repo["status"]) is not str:
        raise WaitRecordError(f"repository status must be '': {repo.get('status')!r}")
    if type(repo["head"]) is not str or not _HEAD_RE.fullmatch(repo["head"]):
        raise WaitRecordError(f"repository head must be 40 lowercase hex: {repo.get('head')!r}")
    if repo["head"] != res["source_head"]:
        raise WaitRecordError("repository head does not match reservation source_head")

    protected_refs = repo["protected_refs"]
    if type(protected_refs) is not dict or not protected_refs:
        raise WaitRecordError("repository protected_refs must be a nonempty dict")
    for r_name, r_sha in protected_refs.items():
        if type(r_name) is not str or not r_name:
            raise WaitRecordError("protected_refs ref name must be a nonempty string")
        if type(r_sha) is not str or not _HEAD_RE.fullmatch(r_sha):
            raise WaitRecordError(f"protected_refs {r_name} sha must be 40 lowercase hex: {r_sha!r}")

    control_blobs = repo["control_blobs"]
    if type(control_blobs) is not dict or not control_blobs:
        raise WaitRecordError("repository control_blobs must be a nonempty dict")
    for c_path, c_sha in control_blobs.items():
        if type(c_path) is not str or not c_path:
            raise WaitRecordError("control_blobs path must be a nonempty string")
        if type(c_sha) is not str or not _HEAD_RE.fullmatch(c_sha):
            raise WaitRecordError(f"control_blobs {c_path} sha must be 40 lowercase hex: {c_sha!r}")

    scope = repo["assignment_scope"]
    if type(scope) is not dict or set(scope.keys()) != _ASSIGNMENT_SCOPE_KEYS:
        raise WaitRecordError("assignment_scope must contain exact six keys")
    for str_key in ("assignment_id", "stage_id", "sha256", "expires_at"):
        val = scope[str_key]
        if type(val) is not str or not val.strip():
            raise WaitRecordError(f"assignment_scope {str_key} must be a nonempty string")
    for list_key in ("allowed_paths", "forbidden_paths"):
        val = scope[list_key]
        if type(val) is not list or any(type(p) is not str for p in val):
            raise WaitRecordError(f"assignment_scope {list_key} must be a list of strings")

    if scope["assignment_id"] != res["assignment_id"]:
        raise WaitRecordError("assignment_scope assignment_id does not match reservation")
    if scope["sha256"] != res["assignment_sha256"]:
        raise WaitRecordError("assignment_scope sha256 does not match reservation assignment_sha256")

    for k in _EXPECTED_KEYS:
        if not _type_sensitive_equal(record[k], expected[k]):
            raise WaitRecordError(f"record {k!r} does not match expected {k!r}")

    return copy.deepcopy(record)
