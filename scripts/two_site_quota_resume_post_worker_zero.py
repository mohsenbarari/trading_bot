from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from typing import Any, Iterator

try:
    from scripts.two_site_proposal_pipeline import ProposalError, SHA_PATTERN
    from scripts.two_site_quota_resume_worker_context import (
        worker_zero_continuation_context,
    )
    from scripts.two_site_quota_resume_worker_reader import load_prior_worker_zero
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        ProposalError,
        SHA_PATTERN,
    )
    from two_site_quota_resume_worker_context import (  # type: ignore[no-redef]
        worker_zero_continuation_context,
    )
    from two_site_quota_resume_worker_reader import (  # type: ignore[no-redef]
        load_prior_worker_zero,
    )

__all__ = ["worker_zero_post_result_context"]

_EXPECTED_RESULT_STATE_KEYS = frozenset({
    "state",
    "task_id",
    "source_head",
    "final_worker_run_id",
    "proposal_sha256",
    "changed_paths",
    "proposal_path",
    "correction_count",
    "semantic_task_fingerprint",
})


@contextmanager
def worker_zero_post_result_context(
    root: Path,
    continuation_request: dict,
    returned_state: dict,
) -> Iterator[dict[str, Any]]:
    """Yield detached post-result worker-0 binding context under pipeline lock."""
    if not isinstance(root, Path):
        raise ProposalError(f"root must be a Path, got {type(root).__name__}")
    if type(continuation_request) is not dict:
        raise ProposalError(
            f"continuation_request must be dict, got {type(continuation_request).__name__}"
        )
    if type(returned_state) is not dict:
        raise ProposalError(
            f"returned_state must be dict, got {type(returned_state).__name__}"
        )

    with worker_zero_continuation_context(root, continuation_request) as ctx:
        if set(returned_state.keys()) != _EXPECTED_RESULT_STATE_KEYS:
            raise ProposalError("returned_state keys do not match expected keys")

        if returned_state["state"] != "WORKER_PROPOSAL_READY":
            raise ProposalError(
                f"unexpected returned state: {returned_state['state']!r}"
            )

        if (
            ctx["admitted_request"].get("role") != "worker"
            or ctx["artifacts"].get("worker_index") != 0
        ):
            raise ProposalError("invalid role or worker_index in context")

        corr = returned_state["correction_count"]
        if type(corr) is not int or corr != 0:
            raise ProposalError(f"invalid correction_count: {corr!r}")

        if returned_state["task_id"] != ctx["task"].get("task_id"):
            raise ProposalError("task_id mismatch between returned_state and context")
        if returned_state["source_head"] != ctx["source_head"]:
            raise ProposalError(
                "source_head mismatch between returned_state and context"
            )
        if (
            returned_state["semantic_task_fingerprint"]
            != ctx["artifacts"].get("semantic_task_fingerprint")
        ):
            raise ProposalError(
                "semantic_task_fingerprint mismatch between returned_state and context"
            )

        expected_run_id = f"{ctx['continuation_run_prefix']}-worker-0"
        if returned_state["final_worker_run_id"] != expected_run_id:
            raise ProposalError(
                f"final_worker_run_id mismatch: expected {expected_run_id!r}, "
                f"got {returned_state['final_worker_run_id']!r}"
            )

        expected_proposal_path = (
            f".local/two-site-refactor/proposals/{expected_run_id}/result.json"
        )
        if returned_state["proposal_path"] != expected_proposal_path:
            raise ProposalError(
                f"proposal_path mismatch: expected {expected_proposal_path!r}, "
                f"got {returned_state['proposal_path']!r}"
            )

        sha = returned_state["proposal_sha256"]
        if not isinstance(sha, str) or not SHA_PATTERN.match(sha):
            raise ProposalError(f"invalid proposal_sha256 format: {sha!r}")

        changed_paths = returned_state["changed_paths"]
        if not isinstance(changed_paths, list) or not all(
            isinstance(p, str) for p in changed_paths
        ):
            raise ProposalError("changed_paths must be a list of strings")

        read_artifacts = {
            "correction_count": 0,
            "task_id": returned_state["task_id"],
            "source_head": returned_state["source_head"],
            "original_run_prefix": ctx["continuation_run_prefix"],
            "final_worker_run_id": expected_run_id,
            "proposal_path": expected_proposal_path,
            "proposal_sha256": sha,
        }
        loaded = load_prior_worker_zero(
            root, ctx["admitted_task_path"], read_artifacts
        )

        if loaded["result_sha"] != sha:
            raise ProposalError(
                f"loader result_sha mismatch: {loaded['result_sha']} != {sha}"
            )

        loader_changes = [c["path"] for c in loaded["result"].get("changes", [])]
        if loader_changes != changed_paths:
            raise ProposalError(
                f"loader changed_paths mismatch: {loader_changes} != {changed_paths}"
            )

        if loaded["result"].get("status") != "PROPOSAL_READY":
            raise ProposalError(
                f"unexpected result status: {loaded['result'].get('status')!r}"
            )

        conv_id = loaded.get("conversation_id")
        if not isinstance(conv_id, str) or not conv_id.strip():
            raise ProposalError("missing or empty conversation_id in loaded status")

        yield {
            "base_context": copy.deepcopy(ctx),
            "worker_info": copy.deepcopy(loaded),
            "returned_state": copy.deepcopy(returned_state),
            "current_worker": (expected_run_id, sha, loaded["result_text"], conv_id),
            "seen_convs": {conv_id},
        }
