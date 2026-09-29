"""Dormant final acknowledgement bridge for two-site quota continuation.

Provides validated publication of version 1.0 final acknowledgement records
linking an independently revalidated clean repository, eligible transition result,
and independently read file-bound accepted lineage under caller-held exclusive Supervisor lock.

Boundary and authority constraints:
1. Dormant proposal-only bridge: contains NO live hooks, NO monitor/supervisor loop,
   NO Provider execution, wake, replay, pruning, or queue dispatch authority.
2. Caller MUST hold exclusive Supervisor lock throughout invocation and provide an eligible
   transition result already validated by validate_transition. This is called only after
   publish_validated_final_acceptance_lineage_locked has returned. The bridge does NOT
   claim to prove lock ownership.
3. Fail-closed: on any pre-publication failure, the publisher is NEVER called.
4. Ambiguous post-publication: after durable publication, any repository drift,
   readback failure, or publisher uncertainty is an explicit ambiguity error;
   the published record is retained and never deleted, retried, or overwritten.
5. No mutable status.json authority.
"""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any

try:
    from scripts.two_site_assignment_scope import AssignmentScope
    from scripts.two_site_codex_autopilot import validate_repository
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
    from scripts.two_site_quota_accepted_lineage_record import (
        PROTECTED_REF_NAMES,
        AcceptedLineageRecordError,
        _validate_result,
    )
    from scripts.two_site_quota_accepted_lineage_storage import (
        AcceptedLineageStorageError,
        read_accepted_lineage_record_candidate,
    )
    from scripts.two_site_quota_final_ack_storage import (
        FinalAckStorageAmbiguousPostPublicationError,
        FinalAckStorageError,
        publish_final_ack_record_candidate,
        read_final_ack_record_candidate,
    )
except ImportError:
    from two_site_assignment_scope import AssignmentScope  # type: ignore
    from two_site_codex_autopilot import validate_repository  # type: ignore
    from two_site_provider_broker import CYCLE_RUN_RE  # type: ignore
    from two_site_quota_accepted_lineage_record import (  # type: ignore
        PROTECTED_REF_NAMES,
        AcceptedLineageRecordError,
        _validate_result,
    )
    from two_site_quota_accepted_lineage_storage import (  # type: ignore
        AcceptedLineageStorageError,
        read_accepted_lineage_record_candidate,
    )
    from two_site_quota_final_ack_storage import (  # type: ignore
        FinalAckStorageAmbiguousPostPublicationError,
        FinalAckStorageError,
        publish_final_ack_record_candidate,
        read_final_ack_record_candidate,
    )

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

__all__ = [
    "AmbiguousPostPublicationError",
    "FinalAckAmbiguousPostPublicationError",
    "FinalAckBridgeAmbiguousPostPublicationError",
    "FinalAckBridgeError",
    "publish_validated_final_ack_locked",
]


class FinalAckBridgeError(FinalAckStorageError, ValueError):
    """Raised when final acknowledgement bridge validation or pre-publication fails."""


class FinalAckBridgeAmbiguousPostPublicationError(FinalAckBridgeError):
    """Raised when failure or drift occurs after acknowledgement publication is initiated."""


AmbiguousPostPublicationError = FinalAckBridgeAmbiguousPostPublicationError
FinalAckAmbiguousPostPublicationError = FinalAckBridgeAmbiguousPostPublicationError


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


def _extract_head(repo: dict[str, Any], label: str) -> str:
    if "head" not in repo:
        raise FinalAckBridgeError(f"{label} missing required 'head' key")
    head = repo["head"]
    if type(head) is not str or not _HEX_40_RE.fullmatch(head):
        raise FinalAckBridgeError(
            f"{label} missing or invalid clean HEAD commit (must be 40 lower hex characters)"
        )
    return head


def _extract_protected_refs(repo: dict[str, Any], label: str) -> dict[str, str]:
    refs = repo.get("protected_refs")
    if type(refs) is not dict or set(refs.keys()) != PROTECTED_REF_NAMES:
        raise FinalAckBridgeError(
            f"{label} protected_refs must contain exact protected refs"
        )
    for ref_name, ref_hash in refs.items():
        if type(ref_hash) is not str or not _HEX_40_RE.fullmatch(ref_hash):
            raise FinalAckBridgeError(
                f"{label} protected ref must be 40 lower hex characters"
            )
    return refs


def _extract_assignment_scope(
    repo: dict[str, Any], label: str
) -> tuple[AssignmentScope, str, str]:
    if "assignment_scope" not in repo:
        raise FinalAckBridgeError(f"{label} missing required 'assignment_scope' key")
    scope = repo["assignment_scope"]
    if not isinstance(scope, AssignmentScope):
        raise FinalAckBridgeError(
            f"{label} assignment_scope must be an AssignmentScope instance"
        )
    asgn_id = scope.assignment_id
    asgn_sha = scope.sha256
    if type(asgn_id) is not str or len(asgn_id) == 0:
        raise FinalAckBridgeError(f"{label} assignment_scope has invalid assignment_id")
    if type(asgn_sha) is not str or not _HEX_64_RE.fullmatch(asgn_sha):
        raise FinalAckBridgeError(
            f"{label} assignment_scope has invalid sha256 (must be 64 lower hex characters)"
        )
    return scope, asgn_id, asgn_sha


def publish_validated_final_ack_locked(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    expected_transition_result: dict[str, Any],
    accepted_lineage_record: dict[str, Any],
    acknowledged_at_utc: str,
) -> dict[str, Any]:
    """Publish validated final acknowledgement record under caller-held exclusive Supervisor lock.

    Caller precondition: caller holds exclusive Supervisor lock throughout invocation and
    has already called publish_validated_final_acceptance_lineage_locked.
    This function does NOT claim to prove lock ownership.
    """
    if not isinstance(root, Path):
        raise FinalAckBridgeError("root must be a Path")
    if type(original_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        original_controller_run_id
    ):
        raise FinalAckBridgeError(
            "original_controller_run_id is not a valid cycle run ID"
        )
    if type(continuation_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        continuation_controller_run_id
    ):
        raise FinalAckBridgeError(
            "continuation_controller_run_id is not a valid cycle run ID"
        )
    if original_controller_run_id == continuation_controller_run_id:
        raise FinalAckBridgeError(
            "original_controller_run_id and continuation_controller_run_id must be distinct"
        )
    if type(expected_transition_result) is not dict:
        raise FinalAckBridgeError("expected_transition_result must be a dict")
    if type(accepted_lineage_record) is not dict:
        raise FinalAckBridgeError("accepted_lineage_record must be a dict")
    if type(acknowledged_at_utc) is not str or len(acknowledged_at_utc.strip()) == 0:
        raise FinalAckBridgeError("acknowledged_at_utc must be a non-empty string")

    # 1. Validate eligible transition result
    try:
        _validate_result(expected_transition_result)
    except AcceptedLineageRecordError as exc:
        raise FinalAckBridgeError("invalid expected_transition_result") from exc

    # 2. Derive expected acceptance context and upstream from supplied accepted_lineage_record
    if "controller_run_id" not in accepted_lineage_record:
        raise FinalAckBridgeError("accepted_lineage_record missing required 'controller_run_id' key")
    lineage_run_id = accepted_lineage_record["controller_run_id"]
    if type(lineage_run_id) is not str or not CYCLE_RUN_RE.fullmatch(lineage_run_id):
        raise FinalAckBridgeError("accepted_lineage_record controller_run_id is not a valid cycle run ID")
    if lineage_run_id != continuation_controller_run_id:
        raise FinalAckBridgeError(
            "accepted_lineage_record controller_run_id does not match continuation_controller_run_id"
        )

    if "protected_refs" not in accepted_lineage_record:
        raise FinalAckBridgeError("accepted_lineage_record missing required 'protected_refs' key")
    lineage_refs = accepted_lineage_record["protected_refs"]
    if type(lineage_refs) is not dict or set(lineage_refs.keys()) != PROTECTED_REF_NAMES:
        raise FinalAckBridgeError("accepted_lineage_record protected_refs must contain exact protected refs")
    for ref_name, ref_hash in lineage_refs.items():
        if type(ref_hash) is not str or not _HEX_40_RE.fullmatch(ref_hash):
            raise FinalAckBridgeError("accepted_lineage_record protected ref must be 40 lower hex characters")

    if "assignment_id" not in accepted_lineage_record:
        raise FinalAckBridgeError("accepted_lineage_record missing required 'assignment_id' key")
    lineage_asgn_id = accepted_lineage_record["assignment_id"]
    if type(lineage_asgn_id) is not str or len(lineage_asgn_id) == 0:
        raise FinalAckBridgeError("accepted_lineage_record assignment_id must be non-empty string")

    if "assignment_sha256" not in accepted_lineage_record:
        raise FinalAckBridgeError("accepted_lineage_record missing required 'assignment_sha256' key")
    lineage_asgn_sha = accepted_lineage_record["assignment_sha256"]
    if type(lineage_asgn_sha) is not str or not _HEX_64_RE.fullmatch(lineage_asgn_sha):
        raise FinalAckBridgeError("accepted_lineage_record assignment_sha256 must be 64 lower hex characters")

    lineage_head_after = accepted_lineage_record.get("head_after")
    if lineage_head_after is None and "current_head" in accepted_lineage_record:
        lineage_head_after = accepted_lineage_record["current_head"]
    if type(lineage_head_after) is not str or not _HEX_40_RE.fullmatch(lineage_head_after):
        raise FinalAckBridgeError("accepted_lineage_record head_after must be 40 lower hex characters")

    if "upstream" not in accepted_lineage_record:
        raise FinalAckBridgeError("accepted_lineage_record missing required 'upstream' key")
    lineage_upstream = accepted_lineage_record["upstream"]
    if type(lineage_upstream) is not dict:
        raise FinalAckBridgeError("accepted_lineage_record upstream must be a dict")

    expected_context = {
        "controller_run_id": continuation_controller_run_id,
        "protected_refs": copy.deepcopy(lineage_refs),
        "assignment_id": lineage_asgn_id,
        "assignment_sha256": lineage_asgn_sha,
        "current_head": lineage_head_after,
    }
    expected_upstream = copy.deepcopy(lineage_upstream)

    # 3. Independently re-read file-bound accepted lineage candidate and require type-sensitive exact equality
    try:
        persisted_lineage = read_accepted_lineage_record_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=expected_context,
            upstream=expected_upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAckBridgeError(
            "failed to read persisted accepted lineage record"
        ) from exc

    if not _type_sensitive_equal(accepted_lineage_record, persisted_lineage):
        raise FinalAckBridgeError(
            "supplied accepted_lineage_record does not match persisted accepted lineage record"
        )

    # 4. Independently validate repository facts immediately before publication
    try:
        repo_pre_pub = validate_repository(root, require_clean=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAckBridgeError(
            "pre-publication repository validation failed"
        ) from exc

    if type(repo_pre_pub) is not dict:
        raise FinalAckBridgeError("pre-publication repository facts must be a dict")

    current_head = _extract_head(repo_pre_pub, "current repository")
    current_refs = _extract_protected_refs(repo_pre_pub, "current repository")
    current_scope, current_asgn_id, current_asgn_sha = _extract_assignment_scope(
        repo_pre_pub, "current repository"
    )

    # 5. Bind repository facts to persisted lineage and expected result
    if current_head != expected_transition_result["head_after"]:
        raise FinalAckBridgeError(
            "current clean HEAD does not match result head_after"
        )
    if current_head != persisted_lineage["head_after"]:
        raise FinalAckBridgeError(
            "current clean HEAD does not match persisted lineage head_after"
        )

    if not _type_sensitive_equal(current_refs, persisted_lineage["protected_refs"]):
        raise FinalAckBridgeError(
            "repository protected_refs does not match persisted lineage protected_refs"
        )

    if current_asgn_id != persisted_lineage["assignment_id"]:
        raise FinalAckBridgeError(
            "repository assignment_id does not match persisted lineage assignment_id"
        )
    if current_asgn_sha != persisted_lineage["assignment_sha256"]:
        raise FinalAckBridgeError(
            "repository assignment_sha256 does not match persisted lineage assignment_sha256"
        )

    persisted_upstream = persisted_lineage["upstream"]
    if current_asgn_id != persisted_upstream.get("assignment_id"):
        raise FinalAckBridgeError(
            "repository assignment_id does not match upstream assignment_id"
        )
    if current_asgn_sha != persisted_upstream.get("assignment_sha256"):
        raise FinalAckBridgeError(
            "repository assignment_sha256 does not match upstream assignment_sha256"
        )
    if persisted_upstream.get("original_controller_run_id") != original_controller_run_id:
        raise FinalAckBridgeError(
            "upstream original_controller_run_id does not match original_controller_run_id"
        )
    if persisted_upstream.get("continuation_controller_run_id") != continuation_controller_run_id:
        raise FinalAckBridgeError(
            "upstream continuation_controller_run_id does not match continuation_controller_run_id"
        )

    head_changed = expected_transition_result["head_changed"]
    res_head_before = expected_transition_result["head_before"]
    res_head_after = expected_transition_result["head_after"]
    res_commit = expected_transition_result["commit"]

    if head_changed:
        if res_head_before == res_head_after or res_commit != res_head_after:
            raise FinalAckBridgeError(
                "result head_changed is True but head_before matches head_after or commit != head_after"
            )
    else:
        if res_head_before != res_head_after or res_commit is not None:
            raise FinalAckBridgeError(
                "result head_changed is False but head_before differs from head_after or commit is not None"
            )

    if persisted_lineage["head_before"] != res_head_before:
        raise FinalAckBridgeError(
            "persisted lineage head_before does not match result head_before"
        )
    if persisted_lineage["head_after"] != res_head_after:
        raise FinalAckBridgeError(
            "persisted lineage head_after does not match result head_after"
        )
    if persisted_lineage["head_changed"] != head_changed:
        raise FinalAckBridgeError(
            "persisted lineage head_changed does not match result head_changed"
        )
    if persisted_lineage["accepted_state"] != expected_transition_result["state"]:
        raise FinalAckBridgeError(
            "persisted lineage accepted_state does not match result state"
        )

    # 6. Build file-bound context and upstream
    file_bound_context = {
        "controller_run_id": continuation_controller_run_id,
        "protected_refs": copy.deepcopy(persisted_lineage["protected_refs"]),
        "assignment_id": persisted_lineage["assignment_id"],
        "assignment_sha256": persisted_lineage["assignment_sha256"],
        "current_head": persisted_lineage["head_after"],
    }
    file_bound_upstream = copy.deepcopy(persisted_lineage["upstream"])

    # 7. Publish final ack record candidate exactly once
    try:
        published_ack = publish_final_ack_record_candidate(
            root=root,
            original_controller_run_id=original_controller_run_id,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=file_bound_context,
            upstream=file_bound_upstream,
            acknowledged_at_utc=acknowledged_at_utc,
        )
    except FinalAckStorageAmbiguousPostPublicationError as exc:
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: publication call status ambiguous; "
            "immutable record retained in custody"
        ) from exc
    except FinalAckStorageError as exc:
        raise FinalAckBridgeError(
            "pre-publication ack storage failure"
        ) from exc
    except BaseException as exc:
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: unexpected exception during publication; "
            "immutable record retained in custody"
        ) from exc

    # 8. Post-publication repository revalidation
    try:
        repo_post_pub = validate_repository(root, require_clean=True)
    except BaseException as exc:
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: post-publication repository revalidation failed; "
            "immutable record retained in custody"
        ) from exc

    if type(repo_post_pub) is not dict:
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: post-publication repository facts must be a dict; "
            "immutable record retained in custody"
        )

    if not _type_sensitive_equal(repo_pre_pub, repo_post_pub):
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: repository state drifted after publication; "
            "immutable record retained in custody"
        )

    # 9. Post-publication strict readback of target final-ack.json
    try:
        readback_ack = read_final_ack_record_candidate(
            root=root,
            original_controller_run_id=original_controller_run_id,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=file_bound_context,
            upstream=file_bound_upstream,
        )
    except BaseException as exc:
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: readback of final ack record failed; "
            "immutable record retained in custody"
        ) from exc

    if not _type_sensitive_equal(readback_ack, published_ack):
        raise FinalAckBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: readback final ack record does not match publication result; "
            "immutable record retained in custody"
        )

    return copy.deepcopy(readback_ack)
