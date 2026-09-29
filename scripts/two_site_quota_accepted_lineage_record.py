"""Pure accepted-lineage record builder and validator for two-site final acceptance."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from scripts.two_site_provider_broker import CYCLE_RUN_RE

MAX_ACCEPTED_LINEAGE_RECORD_BYTES: int = 16_384

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RUN_ID_EVIDENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

PROTECTED_REF_NAMES = frozenset({
    "refs/heads/main",
    "refs/remotes/origin/main",
    "refs/heads/candidate/wa-ir-standby-v1",
})

_RECORD_KEYS = frozenset({
    "schema_version",
    "controller_run_id",
    "result_sha256",
    "accepted_state",
    "head_before",
    "head_after",
    "head_changed",
    "protected_refs",
    "assignment_id",
    "assignment_sha256",
    "upstream",
})

_CONTEXT_KEYS = frozenset({
    "controller_run_id",
    "protected_refs",
    "assignment_id",
    "assignment_sha256",
    "current_head",
})

_RESULT_KEYS = frozenset({
    "schema_version",
    "state",
    "disposition",
    "category",
    "summary",
    "commit",
    "next_action",
    "blockers",
    "gemini_worker_run_id",
    "gemini_reviewer_run_id",
    "proposal_sha256",
    "head_before",
    "head_after",
    "head_changed",
})

_UPSTREAM_KEYS = frozenset({
    "original_controller_run_id",
    "continuation_controller_run_id",
    "original_request_id",
    "resume_request_id",
    "task_id",
    "assignment_id",
    "source_head",
    "task_sha256",
    "assignment_sha256",
    "semantic_task_fingerprint",
    "original_wait_sha256",
    "claim_sha256",
    "request_sha256",
    "submission_intent_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "response_state",
    "exit_code",
    "expected_lineage_snapshot_sha256",
})

_UPSTREAM_DIGEST_KEYS = frozenset({
    "task_sha256",
    "assignment_sha256",
    "semantic_task_fingerprint",
    "original_wait_sha256",
    "claim_sha256",
    "request_sha256",
    "submission_intent_sha256",
    "terminal_receipt_sha256",
    "response_sha256",
    "expected_lineage_snapshot_sha256",
})


class AcceptedLineageRecordError(ValueError):
    """Raised when accepted lineage record building, cross-binding, or validation fails."""


__all__ = [
    "AcceptedLineageRecordError",
    "MAX_ACCEPTED_LINEAGE_RECORD_BYTES",
    "PROTECTED_REF_NAMES",
    "build_accepted_lineage_record",
    "validate_accepted_lineage_record",
]


def _canonical_bytes(obj: Any) -> bytes:
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcceptedLineageRecordError(
            f"record cannot be serialized to canonical JSON: {exc}"
        ) from exc


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


def _validate_result(result: Any) -> None:
    if type(result) is not dict:
        raise AcceptedLineageRecordError("result must be a dict")
    if set(result.keys()) != _RESULT_KEYS:
        raise AcceptedLineageRecordError(
            f"result must contain exact 14 keys, missing={sorted(_RESULT_KEYS - set(result.keys()))}, "
            f"extra={sorted(set(result.keys()) - _RESULT_KEYS)}"
        )

    if type(result["schema_version"]) is not str or result["schema_version"] != "1.0":
        raise AcceptedLineageRecordError(
            f"result schema_version must be '1.0', got {result['schema_version']!r}"
        )

    state = result["state"]
    if state not in ("CONTINUE", "COMPLETE"):
        raise AcceptedLineageRecordError(
            f"result state must be 'CONTINUE' or 'COMPLETE', got {state!r}"
        )

    disposition = result["disposition"]
    if disposition != "NONE":
        raise AcceptedLineageRecordError(
            f"result disposition must be 'NONE', got {disposition!r}"
        )

    category = result["category"]
    if category != "NONE":
        raise AcceptedLineageRecordError(
            f"result category must be 'NONE', got {category!r}"
        )

    summary = result["summary"]
    if type(summary) is not str or len(summary.strip()) == 0:
        raise AcceptedLineageRecordError(
            f"result summary must be non-empty string, got {summary!r}"
        )

    next_action = result["next_action"]
    if state == "CONTINUE" and next_action is None:
        raise AcceptedLineageRecordError(
            "result next_action cannot be None when state is 'CONTINUE'"
        )
    if next_action is not None:
        if type(next_action) is not str or len(next_action.strip()) == 0:
            raise AcceptedLineageRecordError(
                f"result next_action must be non-empty string when provided, got {next_action!r}"
            )

    blockers = result["blockers"]
    if type(blockers) is not list or len(blockers) != 0:
        raise AcceptedLineageRecordError(
            f"result blockers must be empty list, got {blockers!r}"
        )

    head_before = result["head_before"]
    if type(head_before) is not str or not _HEX_40_RE.fullmatch(head_before):
        raise AcceptedLineageRecordError(
            f"result head_before must be 40 lower hex characters, got {head_before!r}"
        )

    head_after = result["head_after"]
    if type(head_after) is not str or not _HEX_40_RE.fullmatch(head_after):
        raise AcceptedLineageRecordError(
            f"result head_after must be 40 lower hex characters, got {head_after!r}"
        )

    head_changed = result["head_changed"]
    if type(head_changed) is not bool:
        raise AcceptedLineageRecordError(
            f"result head_changed must be exact bool, got {head_changed!r}"
        )

    commit = result["commit"]
    worker_id = result["gemini_worker_run_id"]
    reviewer_id = result["gemini_reviewer_run_id"]
    prop_sha = result["proposal_sha256"]

    if head_changed:
        if head_before == head_after:
            raise AcceptedLineageRecordError(
                "result head_changed is True but head_before matches head_after"
            )
        if commit != head_after:
            raise AcceptedLineageRecordError(
                f"result commit must equal head_after ({head_after}) when head_changed is True, "
                f"got {commit!r}"
            )
        if type(worker_id) is not str or not _RUN_ID_EVIDENCE_RE.fullmatch(worker_id):
            raise AcceptedLineageRecordError(
                f"result gemini_worker_run_id must match run ID pattern when commit exists, got {worker_id!r}"
            )
        if type(reviewer_id) is not str or not _RUN_ID_EVIDENCE_RE.fullmatch(reviewer_id):
            raise AcceptedLineageRecordError(
                f"result gemini_reviewer_run_id must match run ID pattern when commit exists, got {reviewer_id!r}"
            )
        if type(prop_sha) is not str or not _HEX_64_RE.fullmatch(prop_sha):
            raise AcceptedLineageRecordError(
                f"result proposal_sha256 must be 64 lower hex characters when commit exists, got {prop_sha!r}"
            )
    else:
        if head_before != head_after:
            raise AcceptedLineageRecordError(
                "result head_changed is False but head_before differs from head_after"
            )
        if commit is not None:
            raise AcceptedLineageRecordError(
                f"result commit must be None when head_changed is False, got {commit!r}"
            )
        if worker_id is not None:
            raise AcceptedLineageRecordError(
                f"result gemini_worker_run_id must be None when commit is None, got {worker_id!r}"
            )
        if reviewer_id is not None:
            raise AcceptedLineageRecordError(
                f"result gemini_reviewer_run_id must be None when commit is None, got {reviewer_id!r}"
            )
        if prop_sha is not None:
            raise AcceptedLineageRecordError(
                f"result proposal_sha256 must be None when commit is None, got {prop_sha!r}"
            )

    if state == "CONTINUE" and not head_changed:
        raise AcceptedLineageRecordError(
            "result state CONTINUE requires head_changed to be True"
        )


def _validate_result_sha256(result_sha256: Any) -> None:
    if type(result_sha256) is not str or not _HEX_64_RE.fullmatch(result_sha256):
        raise AcceptedLineageRecordError(
            f"result_sha256 must be 64 lower hex characters, got {result_sha256!r}"
        )


def _validate_context(context: Any) -> None:
    if type(context) is not dict:
        raise AcceptedLineageRecordError("context must be a dict")
    if set(context.keys()) != _CONTEXT_KEYS:
        raise AcceptedLineageRecordError(
            f"context must have exact keys {_CONTEXT_KEYS}, got {set(context.keys())}"
        )

    run_id = context["controller_run_id"]
    if type(run_id) is not str or not CYCLE_RUN_RE.fullmatch(run_id):
        raise AcceptedLineageRecordError(
            f"context controller_run_id must match cycle run pattern, got {run_id!r}"
        )

    current_head = context["current_head"]
    if type(current_head) is not str or not _HEX_40_RE.fullmatch(current_head):
        raise AcceptedLineageRecordError(
            f"context current_head must be 40 lower hex characters, got {current_head!r}"
        )

    assignment_id = context["assignment_id"]
    if type(assignment_id) is not str or len(assignment_id) == 0:
        raise AcceptedLineageRecordError("context assignment_id must be non-empty string")

    assignment_sha256 = context["assignment_sha256"]
    if type(assignment_sha256) is not str or not _HEX_64_RE.fullmatch(assignment_sha256):
        raise AcceptedLineageRecordError(
            f"context assignment_sha256 must be 64 lower hex characters, got {assignment_sha256!r}"
        )

    protected_refs = context["protected_refs"]
    if type(protected_refs) is not dict:
        raise AcceptedLineageRecordError("context protected_refs must be a dict")
    if set(protected_refs.keys()) != PROTECTED_REF_NAMES:
        raise AcceptedLineageRecordError(
            f"context protected_refs must contain exact refs {sorted(PROTECTED_REF_NAMES)}, "
            f"got {sorted(protected_refs.keys())}"
        )
    for ref_name, ref_hash in protected_refs.items():
        if type(ref_hash) is not str or not _HEX_40_RE.fullmatch(ref_hash):
            raise AcceptedLineageRecordError(
                f"protected ref {ref_name!r} value must be 40 lower hex characters, got {ref_hash!r}"
            )


def _validate_upstream(upstream: Any) -> None:
    if type(upstream) is not dict:
        raise AcceptedLineageRecordError("upstream must be a dict")
    if set(upstream.keys()) != _UPSTREAM_KEYS:
        raise AcceptedLineageRecordError(
            f"upstream must contain exact keys, missing={sorted(_UPSTREAM_KEYS - set(upstream.keys()))}, "
            f"extra={sorted(set(upstream.keys()) - _UPSTREAM_KEYS)}"
        )

    resp_state = upstream["response_state"]
    if resp_state != "COMPLETED":
        raise AcceptedLineageRecordError(
            f"upstream response_state must be 'COMPLETED', got {resp_state!r}"
        )

    exit_code = upstream["exit_code"]
    if type(exit_code) is not int or type(exit_code) is bool or exit_code != 0:
        raise AcceptedLineageRecordError(
            f"upstream exit_code must be exact int 0, got {exit_code!r}"
        )

    source_head = upstream["source_head"]
    if type(source_head) is not str or not _HEX_40_RE.fullmatch(source_head):
        raise AcceptedLineageRecordError(
            f"upstream source_head must be 40 lower hex characters, got {source_head!r}"
        )

    for field in _UPSTREAM_DIGEST_KEYS:
        val = upstream[field]
        if type(val) is not str or not _HEX_64_RE.fullmatch(val):
            raise AcceptedLineageRecordError(
                f"upstream digest field {field!r} must be 64 lower hex characters, got {val!r}"
            )

    orig_run_id = upstream["original_controller_run_id"]
    if type(orig_run_id) is not str or not CYCLE_RUN_RE.fullmatch(orig_run_id):
        raise AcceptedLineageRecordError(
            f"upstream original_controller_run_id must match cycle run pattern, got {orig_run_id!r}"
        )

    cont_run_id = upstream["continuation_controller_run_id"]
    if type(cont_run_id) is not str or not CYCLE_RUN_RE.fullmatch(cont_run_id):
        raise AcceptedLineageRecordError(
            f"upstream continuation_controller_run_id must match cycle run pattern, got {cont_run_id!r}"
        )

    orig_req_id = upstream["original_request_id"]
    if type(orig_req_id) is not str or not _REQUEST_ID_RE.fullmatch(orig_req_id):
        raise AcceptedLineageRecordError(
            f"upstream original_request_id must be 32 lower hex characters, got {orig_req_id!r}"
        )

    resume_req_id = upstream["resume_request_id"]
    if type(resume_req_id) is not str or not _REQUEST_ID_RE.fullmatch(resume_req_id):
        raise AcceptedLineageRecordError(
            f"upstream resume_request_id must be 32 lower hex characters, got {resume_req_id!r}"
        )

    for id_field in (
        "task_id",
        "assignment_id",
    ):
        v = upstream[id_field]
        if type(v) is not str or len(v) == 0:
            raise AcceptedLineageRecordError(
                f"upstream field {id_field!r} must be non-empty string, got {v!r}"
            )


def _validate_record_structure(record: Any) -> None:
    if type(record) is not dict or set(record.keys()) != _RECORD_KEYS:
        raise AcceptedLineageRecordError(
            f"record must be dict with exact keys {_RECORD_KEYS}"
        )

    if type(record["schema_version"]) is not str or record["schema_version"] != "1.0":
        raise AcceptedLineageRecordError(
            f"record schema_version must be '1.0', got {record['schema_version']!r}"
        )

    accepted_state = record["accepted_state"]
    if accepted_state not in ("CONTINUE", "COMPLETE"):
        raise AcceptedLineageRecordError(
            f"record accepted_state must be 'CONTINUE' or 'COMPLETE', got {accepted_state!r}"
        )

    _validate_result_sha256(record["result_sha256"])

    head_before = record["head_before"]
    if type(head_before) is not str or not _HEX_40_RE.fullmatch(head_before):
        raise AcceptedLineageRecordError(
            f"record head_before must be 40 lower hex characters, got {head_before!r}"
        )

    head_after = record["head_after"]
    if type(head_after) is not str or not _HEX_40_RE.fullmatch(head_after):
        raise AcceptedLineageRecordError(
            f"record head_after must be 40 lower hex characters, got {head_after!r}"
        )

    head_changed = record["head_changed"]
    if type(head_changed) is not bool:
        raise AcceptedLineageRecordError(
            f"record head_changed must be exact bool, got {head_changed!r}"
        )

    if head_changed:
        if head_before == head_after:
            raise AcceptedLineageRecordError(
                "record head_changed is True but head_before matches head_after"
            )
    else:
        if head_before != head_after:
            raise AcceptedLineageRecordError(
                "record head_changed is False but head_before differs from head_after"
            )

    if accepted_state == "CONTINUE" and not head_changed:
        raise AcceptedLineageRecordError(
            "record accepted_state CONTINUE requires head_changed to be True"
        )

    run_id = record["controller_run_id"]
    if type(run_id) is not str or not CYCLE_RUN_RE.fullmatch(run_id):
        raise AcceptedLineageRecordError(
            f"record controller_run_id must match cycle run pattern, got {run_id!r}"
        )

    assignment_id = record["assignment_id"]
    if type(assignment_id) is not str or len(assignment_id) == 0:
        raise AcceptedLineageRecordError(
            "record assignment_id must be non-empty string"
        )

    assignment_sha256 = record["assignment_sha256"]
    if type(assignment_sha256) is not str or not _HEX_64_RE.fullmatch(assignment_sha256):
        raise AcceptedLineageRecordError(
            f"record assignment_sha256 must be 64 lower hex characters, got {assignment_sha256!r}"
        )

    protected_refs = record["protected_refs"]
    if type(protected_refs) is not dict or set(protected_refs.keys()) != PROTECTED_REF_NAMES:
        raise AcceptedLineageRecordError(
            f"record protected_refs must contain exact refs {sorted(PROTECTED_REF_NAMES)}"
        )
    for ref_name, ref_hash in protected_refs.items():
        if type(ref_hash) is not str or not _HEX_40_RE.fullmatch(ref_hash):
            raise AcceptedLineageRecordError(
                f"record protected ref {ref_name!r} value must be 40 lower hex characters, got {ref_hash!r}"
            )

    upstream = record["upstream"]
    _validate_upstream(upstream)

    if record["assignment_id"] != upstream["assignment_id"]:
        raise AcceptedLineageRecordError(
            f"record assignment_id mismatch with upstream: "
            f"{record['assignment_id']!r} != {upstream['assignment_id']!r}"
        )
    if record["assignment_sha256"] != upstream["assignment_sha256"]:
        raise AcceptedLineageRecordError(
            f"record assignment_sha256 mismatch with upstream: "
            f"{record['assignment_sha256']!r} != {upstream['assignment_sha256']!r}"
        )
    if record["controller_run_id"] != upstream["continuation_controller_run_id"]:
        raise AcceptedLineageRecordError(
            f"record controller_run_id mismatch with upstream continuation_controller_run_id: "
            f"{record['controller_run_id']!r} != {upstream['continuation_controller_run_id']!r}"
        )
    if record["head_before"] != upstream["source_head"]:
        raise AcceptedLineageRecordError(
            f"record head_before mismatch with upstream source_head: "
            f"{record['head_before']!r} != {upstream['source_head']!r}"
        )


def build_accepted_lineage_record(
    result: dict[str, Any],
    acceptance_context: dict[str, Any],
    upstream: dict[str, Any],
    result_sha256: str,
) -> dict[str, Any]:
    """Build and self-validate a version 1.0 accepted lineage record."""
    _validate_result(result)
    _validate_result_sha256(result_sha256)
    _validate_context(acceptance_context)
    _validate_upstream(upstream)

    if acceptance_context["current_head"] != result["head_after"]:
        raise AcceptedLineageRecordError(
            f"context current_head ({acceptance_context['current_head']}) "
            f"does not match result head_after ({result['head_after']})"
        )

    if acceptance_context["assignment_id"] != upstream["assignment_id"]:
        raise AcceptedLineageRecordError(
            f"context assignment_id ({acceptance_context['assignment_id']!r}) "
            f"does not match upstream assignment_id ({upstream['assignment_id']!r})"
        )
    if acceptance_context["assignment_sha256"] != upstream["assignment_sha256"]:
        raise AcceptedLineageRecordError(
            f"context assignment_sha256 ({acceptance_context['assignment_sha256']!r}) "
            f"does not match upstream assignment_sha256 ({upstream['assignment_sha256']!r})"
        )

    if acceptance_context["controller_run_id"] != upstream["continuation_controller_run_id"]:
        raise AcceptedLineageRecordError(
            f"context controller_run_id ({acceptance_context['controller_run_id']!r}) "
            f"does not match upstream continuation_controller_run_id ({upstream['continuation_controller_run_id']!r})"
        )

    if upstream["source_head"] != result["head_before"]:
        raise AcceptedLineageRecordError(
            f"upstream source_head ({upstream['source_head']}) "
            f"does not match result head_before ({result['head_before']})"
        )

    record = {
        "schema_version": "1.0",
        "controller_run_id": acceptance_context["controller_run_id"],
        "result_sha256": result_sha256,
        "accepted_state": result["state"],
        "head_before": result["head_before"],
        "head_after": result["head_after"],
        "head_changed": result["head_changed"],
        "protected_refs": copy.deepcopy(acceptance_context["protected_refs"]),
        "assignment_id": acceptance_context["assignment_id"],
        "assignment_sha256": acceptance_context["assignment_sha256"],
        "upstream": copy.deepcopy(upstream),
    }

    return validate_accepted_lineage_record(
        record,
        expected_result=result,
        expected_context=acceptance_context,
        expected_upstream=upstream,
        expected_result_sha256=result_sha256,
    )


def validate_accepted_lineage_record(
    record: Any,
    expected_result: dict[str, Any],
    expected_context: dict[str, Any],
    expected_upstream: dict[str, Any],
    expected_result_sha256: str,
) -> dict[str, Any]:
    """Validate an accepted lineage record against independent expected inputs and return a detached deep copy."""
    _validate_record_structure(record)
    _validate_result(expected_result)
    _validate_result_sha256(expected_result_sha256)
    _validate_context(expected_context)
    _validate_upstream(expected_upstream)

    if not _type_sensitive_equal(record["accepted_state"], expected_result["state"]):
        raise AcceptedLineageRecordError(
            f"record accepted_state ({record['accepted_state']!r}) "
            f"does not match expected_result state ({expected_result['state']!r})"
        )
    if not _type_sensitive_equal(record["head_before"], expected_result["head_before"]):
        raise AcceptedLineageRecordError(
            f"record head_before ({record['head_before']!r}) "
            f"does not match expected_result head_before ({expected_result['head_before']!r})"
        )
    if not _type_sensitive_equal(record["head_after"], expected_result["head_after"]):
        raise AcceptedLineageRecordError(
            f"record head_after ({record['head_after']!r}) "
            f"does not match expected_result head_after ({expected_result['head_after']!r})"
        )
    if not _type_sensitive_equal(record["head_changed"], expected_result["head_changed"]):
        raise AcceptedLineageRecordError(
            f"record head_changed ({record['head_changed']!r}) "
            f"does not match expected_result head_changed ({expected_result['head_changed']!r})"
        )

    if not _type_sensitive_equal(record["result_sha256"], expected_result_sha256):
        raise AcceptedLineageRecordError(
            f"record result_sha256 ({record['result_sha256']!r}) "
            f"does not match expected_result_sha256 ({expected_result_sha256!r})"
        )

    if not _type_sensitive_equal(record["controller_run_id"], expected_context["controller_run_id"]):
        raise AcceptedLineageRecordError(
            f"record controller_run_id ({record['controller_run_id']!r}) "
            f"does not match expected_context controller_run_id ({expected_context['controller_run_id']!r})"
        )
    if not _type_sensitive_equal(record["protected_refs"], expected_context["protected_refs"]):
        raise AcceptedLineageRecordError(
            "record protected_refs does not match expected_context protected_refs"
        )
    if not _type_sensitive_equal(record["assignment_id"], expected_context["assignment_id"]):
        raise AcceptedLineageRecordError(
            f"record assignment_id ({record['assignment_id']!r}) "
            f"does not match expected_context assignment_id ({expected_context['assignment_id']!r})"
        )
    if not _type_sensitive_equal(record["assignment_sha256"], expected_context["assignment_sha256"]):
        raise AcceptedLineageRecordError(
            f"record assignment_sha256 ({record['assignment_sha256']!r}) "
            f"does not match expected_context assignment_sha256 ({expected_context['assignment_sha256']!r})"
        )
    if not _type_sensitive_equal(record["head_after"], expected_context["current_head"]):
        raise AcceptedLineageRecordError(
            f"record head_after ({record['head_after']!r}) "
            f"does not match expected_context current_head ({expected_context['current_head']!r})"
        )

    if not _type_sensitive_equal(record["upstream"], expected_upstream):
        raise AcceptedLineageRecordError(
            "record upstream does not match expected_upstream"
        )

    canonical_record_bytes = _canonical_bytes(record)
    if len(canonical_record_bytes) > MAX_ACCEPTED_LINEAGE_RECORD_BYTES:
        raise AcceptedLineageRecordError(
            f"record canonical JSON size ({len(canonical_record_bytes)} bytes) "
            f"exceeds limit ({MAX_ACCEPTED_LINEAGE_RECORD_BYTES} bytes)"
        )

    return copy.deepcopy(record)
