"""Pure schema codec for known-reset quota resume claim records (version 1.0)."""

from __future__ import annotations

import copy
import re
from typing import Any

try:
    from scripts.two_site_provider_broker import (
        CYCLE_RUN_RE,
        REQUEST_ID_RE,
        RUN_PREFIX_RE,
    )
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
except ImportError:
    from two_site_provider_broker import (
        CYCLE_RUN_RE,
        REQUEST_ID_RE,
        RUN_PREFIX_RE,
    )
    from two_site_quota_wait_schedule import _parse_utc_iso

__all__ = [
    "ResumeClaimRecordError",
    "validate_known_reset_resume_claim",
]


class ResumeClaimRecordError(ValueError):
    """Raised when a quota resume claim record fails validation."""


_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_CANONICAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_CLAIM_KEYS = frozenset({
    "schema_version",
    "state",
    "original_controller_run_id",
    "continuation_controller_run_id",
    "original_request_id",
    "resume_request_id",
    "original_wait_sha256",
    "semantic_task_fingerprint",
    "task_id",
    "task_path",
    "task_sha256",
    "source_head",
    "assignment_id",
    "assignment_sha256",
    "original_run_prefix",
    "resume_run_prefix",
    "role",
    "reset_kind",
    "next_check_at_utc",
    "claimed_at_utc",
    "remaining_resume_budget_before",
    "remaining_resume_budget_after",
})

_EXPECTED_KEYS = frozenset(_CLAIM_KEYS - {"schema_version", "state"})


def validate_known_reset_resume_claim(claim: object, expected: object) -> dict[str, Any]:
    """Validate a version 1.0 known-reset quota resume claim against independent expected values.

    Returns a detached deepcopy of the claim dict. Never mutates input arguments.
    """
    if type(claim) is not dict:
        raise ResumeClaimRecordError(f"claim must be dict, got {type(claim).__name__}")
    if type(expected) is not dict:
        raise ResumeClaimRecordError(f"expected must be dict, got {type(expected).__name__}")

    claim_keys = set(claim.keys())
    if claim_keys != _CLAIM_KEYS:
        missing = sorted(_CLAIM_KEYS - claim_keys)
        extra = sorted(claim_keys - _CLAIM_KEYS)
        raise ResumeClaimRecordError(f"claim keys mismatch: missing={missing}, extra={extra}")

    expected_keys = set(expected.keys())
    if expected_keys != _EXPECTED_KEYS:
        missing = sorted(_EXPECTED_KEYS - expected_keys)
        extra = sorted(expected_keys - _EXPECTED_KEYS)
        raise ResumeClaimRecordError(f"expected keys mismatch: missing={missing}, extra={extra}")

    # Type-sensitive comparison of all expected fields
    for key in sorted(_EXPECTED_KEYS):
        c_val = claim[key]
        e_val = expected[key]
        if type(c_val) is not type(e_val) or c_val != e_val:
            raise ResumeClaimRecordError(
                f"Field {key!r} mismatch: claim={c_val!r} ({type(c_val).__name__}) != "
                f"expected={e_val!r} ({type(e_val).__name__})"
            )

    # Top-level version and state invariants
    if type(claim["schema_version"]) is not str or claim["schema_version"] != "1.0":
        raise ResumeClaimRecordError(f"schema_version must be '1.0', got {claim['schema_version']!r}")
    if type(claim["state"]) is not str or claim["state"] != "RESUME_CLAIMED":
        raise ResumeClaimRecordError(f"state must be 'RESUME_CLAIMED', got {claim['state']!r}")

    # Controller run IDs
    orig_cid = claim["original_controller_run_id"]
    if type(orig_cid) is not str or not CYCLE_RUN_RE.fullmatch(orig_cid):
        raise ResumeClaimRecordError(f"original_controller_run_id must match cycle run pattern: {orig_cid!r}")
    cont_cid = claim["continuation_controller_run_id"]
    if type(cont_cid) is not str or not CYCLE_RUN_RE.fullmatch(cont_cid):
        raise ResumeClaimRecordError(f"continuation_controller_run_id must match cycle run pattern: {cont_cid!r}")
    if orig_cid == cont_cid:
        raise ResumeClaimRecordError(
            "continuation_controller_run_id must differ from original_controller_run_id"
        )

    # Request IDs
    orig_rid = claim["original_request_id"]
    if type(orig_rid) is not str or not REQUEST_ID_RE.fullmatch(orig_rid):
        raise ResumeClaimRecordError(f"original_request_id must be 32 lowercase hex: {orig_rid!r}")
    resume_rid = claim["resume_request_id"]
    if type(resume_rid) is not str or not REQUEST_ID_RE.fullmatch(resume_rid):
        raise ResumeClaimRecordError(f"resume_request_id must be 32 lowercase hex: {resume_rid!r}")
    if orig_rid == resume_rid:
        raise ResumeClaimRecordError(
            "resume_request_id must differ from original_request_id"
        )

    # SHA256 fields
    for field in ("original_wait_sha256", "semantic_task_fingerprint", "task_sha256", "assignment_sha256"):
        val = claim[field]
        if type(val) is not str or not _HEX_64_RE.fullmatch(val):
            raise ResumeClaimRecordError(f"{field} must be 64 lowercase hex: {val!r}")

    # 40-hex source_head
    head = claim["source_head"]
    if type(head) is not str or not _HEX_40_RE.fullmatch(head):
        raise ResumeClaimRecordError(f"source_head must be 40 lowercase hex: {head!r}")

    # Canonical relative task_path
    tp = claim["task_path"]
    if type(tp) is not str:
        raise ResumeClaimRecordError(f"task_path must be str, got {type(tp).__name__}")
    if tp.startswith("/") or "\\" in tp or tp.endswith("/"):
        raise ResumeClaimRecordError(f"task_path is not canonical relative: {tp!r}")
    parts = tp.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ResumeClaimRecordError(f"task_path contains empty or dot components: {tp!r}")
    if len(parts) < 4 or parts[:3] != [".local", "two-site-refactor", "tasks"]:
        raise ResumeClaimRecordError(f"task_path must be under .local/two-site-refactor/tasks: {tp!r}")

    # Run prefixes
    orig_prefix = claim["original_run_prefix"]
    if type(orig_prefix) is not str or not RUN_PREFIX_RE.fullmatch(orig_prefix) or len(orig_prefix) > 60:
        raise ResumeClaimRecordError(f"original_run_prefix is invalid: {orig_prefix!r}")
    expected_resume_prefix = f"qresume-{orig_rid}"
    resume_prefix = claim["resume_run_prefix"]
    if type(resume_prefix) is not str or resume_prefix != expected_resume_prefix:
        raise ResumeClaimRecordError(
            f"resume_run_prefix must be exactly {expected_resume_prefix!r}, got {resume_prefix!r}"
        )
    if len(resume_prefix) > 60 or not RUN_PREFIX_RE.fullmatch(resume_prefix):
        raise ResumeClaimRecordError(f"resume_run_prefix is invalid: {resume_prefix!r}")

    # Canonical identifiers
    for field in ("task_id", "assignment_id"):
        val = claim[field]
        if type(val) is not str or not _CANONICAL_ID_RE.fullmatch(val):
            raise ResumeClaimRecordError(f"{field} is invalid: {val!r}")

    # Role
    role = claim["role"]
    if type(role) is not str or role not in ("worker", "verifier"):
        raise ResumeClaimRecordError(f"role must be 'worker' or 'verifier', got {role!r}")

    # reset_kind: known only
    reset_kind = claim["reset_kind"]
    if type(reset_kind) is not str or reset_kind != "QUOTA_RESET_KNOWN":
        raise ResumeClaimRecordError(f"reset_kind must be 'QUOTA_RESET_KNOWN', got {reset_kind!r}")

    # UTC timestamps
    try:
        next_check_dt = _parse_utc_iso(claim["next_check_at_utc"])
    except Exception as exc:
        raise ResumeClaimRecordError(f"next_check_at_utc invalid: {exc}") from exc

    try:
        claimed_dt = _parse_utc_iso(claim["claimed_at_utc"])
    except Exception as exc:
        raise ResumeClaimRecordError(f"claimed_at_utc invalid: {exc}") from exc

    if claimed_dt < next_check_dt:
        raise ResumeClaimRecordError(
            f"claimed_at_utc ({claim['claimed_at_utc']}) cannot be earlier than "
            f"next_check_at_utc ({claim['next_check_at_utc']})"
        )

    # Budgets: exact ints 1 then 0
    rem_before = claim["remaining_resume_budget_before"]
    if type(rem_before) is not int or type(rem_before) is bool or rem_before != 1:
        raise ResumeClaimRecordError(
            f"remaining_resume_budget_before must be int 1, got {rem_before!r} ({type(rem_before).__name__})"
        )
    rem_after = claim["remaining_resume_budget_after"]
    if type(rem_after) is not int or type(rem_after) is bool or rem_after != 0:
        raise ResumeClaimRecordError(
            f"remaining_resume_budget_after must be int 0, got {rem_after!r} ({type(rem_after).__name__})"
        )

    return copy.deepcopy(claim)
