"""Dormant final-acceptance bridge for two-site quota continuation.

Provides validated publication of version 1.0 accepted lineage records
linking an independently revalidated clean repository, eligible transition result,
and read-only upstream cold snapshot lineage under caller-held exclusive Supervisor lock.

Boundary and authority constraints:
1. Dormant proposal-only bridge: contains NO live hooks, NO monitor/supervisor loop,
   NO Provider execution, wake, replay, or queue dispatch authority.
2. Caller MUST hold exclusive Supervisor lock and provide an eligible transition result
   already validated by validate_transition before advisories. The bridge does not
   claim to prove lock ownership.
3. Fail-closed: on any pre-publication failure, the publisher is NEVER called.
4. Ambiguous post-publication: after durable publication, any repository drift or publisher
   failure is an explicit ambiguity error; the published record is retained and never
   deleted, retried, or overwritten.
"""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any

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
    publish_accepted_lineage_record_candidate,
)
from scripts.two_site_quota_final_acceptance_lineage import (
    collect_final_acceptance_upstream_lineage_from_snapshot_locked,
)

_HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")

__all__ = [
    "FinalAcceptanceBridgeAmbiguousPostPublicationError",
    "FinalAcceptanceBridgeError",
    "publish_validated_final_acceptance_lineage_locked",
]


class FinalAcceptanceBridgeError(AcceptedLineageStorageError, ValueError):
    """Raised when final acceptance bridge validation or pre-publication fails."""


class FinalAcceptanceBridgeAmbiguousPostPublicationError(FinalAcceptanceBridgeError):
    """Raised when repository drift or failure occurs after publication is initiated."""


AmbiguousPostPublicationError = FinalAcceptanceBridgeAmbiguousPostPublicationError
FinalAcceptanceAmbiguousPostPublicationError = FinalAcceptanceBridgeAmbiguousPostPublicationError


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
        raise FinalAcceptanceBridgeError(f"{label} missing required 'head' key")
    head = repo["head"]
    if type(head) is not str or not _HEX_40_RE.fullmatch(head):
        raise FinalAcceptanceBridgeError(
            f"{label} missing or invalid clean HEAD commit (must be 40 lower hex characters)"
        )
    return head


def _extract_protected_refs(repo: dict[str, Any], label: str) -> dict[str, str]:
    refs = repo.get("protected_refs")
    if type(refs) is not dict or set(refs.keys()) != PROTECTED_REF_NAMES:
        raise FinalAcceptanceBridgeError(
            f"{label} protected_refs must contain exact protected refs {sorted(PROTECTED_REF_NAMES)}"
        )
    for ref_name, ref_hash in refs.items():
        if type(ref_hash) is not str or not _HEX_40_RE.fullmatch(ref_hash):
            raise FinalAcceptanceBridgeError(
                f"{label} protected ref {ref_name!r} must be 40 lower hex characters"
            )
    return refs


def _extract_assignment_scope(
    repo: dict[str, Any], label: str
) -> tuple[AssignmentScope, str, str]:
    if "assignment_scope" not in repo:
        raise FinalAcceptanceBridgeError(f"{label} missing required 'assignment_scope' key")
    scope = repo["assignment_scope"]
    if not isinstance(scope, AssignmentScope):
        raise FinalAcceptanceBridgeError(
            f"{label} assignment_scope must be an AssignmentScope instance, got {type(scope).__name__}"
        )
    asgn_id = scope.assignment_id
    asgn_sha = scope.sha256
    if type(asgn_id) is not str or len(asgn_id) == 0:
        raise FinalAcceptanceBridgeError(f"{label} assignment_scope has invalid assignment_id")
    if type(asgn_sha) is not str or not _HEX_64_RE.fullmatch(asgn_sha):
        raise FinalAcceptanceBridgeError(
            f"{label} assignment_scope has invalid sha256 (must be 64 lower hex characters)"
        )
    return scope, asgn_id, asgn_sha


def publish_validated_final_acceptance_lineage_locked(
    root: Path,
    original_controller_run_id: str,
    continuation_controller_run_id: str,
    before_repository: dict[str, Any],
    expected_transition_result: dict[str, Any],
) -> dict[str, Any]:
    """Publish validated final acceptance lineage record under caller-held exclusive Supervisor lock.

    Caller precondition: caller holds exclusive Supervisor lock throughout invocation and
    has obtained expected_transition_result from validate_transition before adding advisories.
    This function does NOT claim to prove lock ownership.
    """
    if not isinstance(root, Path):
        raise FinalAcceptanceBridgeError("root must be a Path")
    if type(original_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        original_controller_run_id
    ):
        raise FinalAcceptanceBridgeError(
            f"original_controller_run_id {original_controller_run_id!r} is not a valid cycle run ID"
        )
    if type(continuation_controller_run_id) is not str or not CYCLE_RUN_RE.fullmatch(
        continuation_controller_run_id
    ):
        raise FinalAcceptanceBridgeError(
            f"continuation_controller_run_id {continuation_controller_run_id!r} is not a valid cycle run ID"
        )
    if original_controller_run_id == continuation_controller_run_id:
        raise FinalAcceptanceBridgeError(
            "original_controller_run_id and continuation_controller_run_id must be distinct"
        )
    if type(before_repository) is not dict:
        raise FinalAcceptanceBridgeError("before_repository must be a dict")
    if type(expected_transition_result) is not dict:
        raise FinalAcceptanceBridgeError("expected_transition_result must be a dict")

    # 1. Validate eligible transition result
    try:
        _validate_result(expected_transition_result)
    except AcceptedLineageRecordError as exc:
        raise FinalAcceptanceBridgeError(f"invalid expected_transition_result: {exc}") from exc

    # 2. Extract before_repository baseline facts
    before_head = _extract_head(before_repository, "before_repository")
    before_refs = _extract_protected_refs(before_repository, "before_repository")
    before_scope, before_asgn_id, before_asgn_sha = _extract_assignment_scope(
        before_repository, "before_repository"
    )

    if before_head != expected_transition_result["head_before"]:
        raise FinalAcceptanceBridgeError(
            f"before_repository head ({before_head}) does not match "
            f"result head_before ({expected_transition_result['head_before']})"
        )

    # 3. Independently validate repository immediately before collecting upstream
    try:
        repo_pre_upstream = validate_repository(root, require_clean=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceBridgeError(
            f"pre-upstream repository validation failed: {exc}"
        ) from exc

    if type(repo_pre_upstream) is not dict:
        raise FinalAcceptanceBridgeError("pre-upstream repository facts must be a dict")

    # 4. Collect upstream lineage from snapshot
    try:
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(
            root=root,
            original_controller_run_id=original_controller_run_id,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceBridgeError(
            f"failed to collect upstream lineage for {original_controller_run_id}: {exc}"
        ) from exc

    if type(upstream) is not dict:
        raise FinalAcceptanceBridgeError("upstream lineage must be a dict")

    # 5. Independently revalidate repository immediately before publication
    try:
        repo_pre_pub = validate_repository(root, require_clean=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceBridgeError(
            f"pre-publication repository validation failed: {exc}"
        ) from exc

    if type(repo_pre_pub) is not dict:
        raise FinalAcceptanceBridgeError("pre-publication repository facts must be a dict")

    # Compare both complete repository facts type-sensitively to detect drift
    if not _type_sensitive_equal(repo_pre_upstream, repo_pre_pub):
        raise FinalAcceptanceBridgeError(
            "repository drift detected between upstream collection and publication"
        )

    # 6. Verify pre-publication consistency
    current_head = _extract_head(repo_pre_pub, "current repository")
    current_refs = _extract_protected_refs(repo_pre_pub, "current repository")
    current_scope, current_asgn_id, current_asgn_sha = _extract_assignment_scope(
        repo_pre_pub, "current repository"
    )

    if current_head != expected_transition_result["head_after"]:
        raise FinalAcceptanceBridgeError(
            f"current clean HEAD ({current_head}) does not match "
            f"result head_after ({expected_transition_result['head_after']})"
        )

    if not _type_sensitive_equal(before_refs, current_refs):
        raise FinalAcceptanceBridgeError(
            "protected_refs drifted between before_repository and current repository"
        )

    if before_scope != current_scope:
        raise FinalAcceptanceBridgeError(
            "AssignmentScope object equality check failed between before_repository and current repository"
        )

    if before_asgn_id != current_asgn_id or before_asgn_sha != current_asgn_sha:
        raise FinalAcceptanceBridgeError(
            "assignment_id or assignment_sha256 mismatch between before_repository and current repository"
        )

    head_changed = expected_transition_result["head_changed"]
    res_head_before = expected_transition_result["head_before"]
    res_head_after = expected_transition_result["head_after"]
    res_commit = expected_transition_result["commit"]

    if head_changed:
        if res_head_before == res_head_after or res_commit != res_head_after:
            raise FinalAcceptanceBridgeError(
                "result head_changed is True but head_before matches head_after or commit != head_after"
            )
    else:
        if res_head_before != res_head_after or res_commit is not None:
            raise FinalAcceptanceBridgeError(
                "result head_changed is False but head_before differs from head_after or commit is not None"
            )

    # Cross-bind upstream against continuation ID, source_head, and AssignmentScope
    if upstream.get("continuation_controller_run_id") != continuation_controller_run_id:
        raise FinalAcceptanceBridgeError(
            f"upstream continuation_controller_run_id ({upstream.get('continuation_controller_run_id')!r}) "
            f"does not match supplied continuation ID ({continuation_controller_run_id!r})"
        )
    if upstream.get("source_head") != expected_transition_result["head_before"]:
        raise FinalAcceptanceBridgeError(
            f"upstream source_head ({upstream.get('source_head')!r}) does not match "
            f"result head_before ({expected_transition_result['head_before']!r})"
        )
    if upstream.get("assignment_id") != current_asgn_id:
        raise FinalAcceptanceBridgeError(
            f"upstream assignment_id ({upstream.get('assignment_id')!r}) does not match "
            f"repository assignment_id ({current_asgn_id!r})"
        )
    if upstream.get("assignment_sha256") != current_asgn_sha:
        raise FinalAcceptanceBridgeError(
            f"upstream assignment_sha256 ({upstream.get('assignment_sha256')!r}) does not match "
            f"repository assignment_sha256 ({current_asgn_sha!r})"
        )

    # 7. Build acceptance context with exact 5 keys
    acceptance_context = {
        "controller_run_id": continuation_controller_run_id,
        "protected_refs": copy.deepcopy(current_refs),
        "assignment_id": current_asgn_id,
        "assignment_sha256": current_asgn_sha,
        "current_head": current_head,
    }

    # 8. Publish accepted lineage record candidate
    try:
        published_record = publish_accepted_lineage_record_candidate(
            root=root,
            continuation_controller_run_id=continuation_controller_run_id,
            expected_transition_result=expected_transition_result,
            acceptance_context=acceptance_context,
            upstream=upstream,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceBridgeAmbiguousPostPublicationError(
            f"ambiguous post-publication failure: publication call failed: {exc}; "
            "immutable record retained in custody"
        ) from exc

    # 9. Revalidate repository once more after publication
    try:
        repo_post_pub = validate_repository(root, require_clean=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise FinalAcceptanceBridgeAmbiguousPostPublicationError(
            f"ambiguous post-publication failure: repository revalidation failed: {exc}; "
            "immutable record retained in custody"
        ) from exc

    if not _type_sensitive_equal(repo_pre_pub, repo_post_pub):
        raise FinalAcceptanceBridgeAmbiguousPostPublicationError(
            "ambiguous post-publication failure: repository state drifted after publication; "
            "immutable record retained in custody"
        )

    return copy.deepcopy(published_record)
