"""Read-only helper to derive base history for Worker-2 resume."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from scripts.two_site_proposal_pipeline import (
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
    )
except ImportError:
    from two_site_proposal_pipeline import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        ProposalError,
        RUN_PREFIX_RE,
        SHA_PATTERN,
    )

try:
    from scripts.two_site_proposal_runner import (
        bounded_file_bytes,
        validate_correction_feedback,
    )
except ImportError:
    try:
        from two_site_proposal_runner import (  # type: ignore[no-redef]
            bounded_file_bytes,
            validate_correction_feedback,
        )
    except ImportError:
        from scripts.two_site_proposal_pipeline import (  # type: ignore[no-redef]
            bounded_file_bytes,
            validate_correction_feedback,
        )


def derive_worker_two_history_base(
    root: Path | str,
    artifacts: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive read-only detached base history for Worker-2 quota resume."""
    if not isinstance(root, (str, Path)):
        raise ProposalError(f"root must be str or Path, got {type(root).__name__}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ProposalError(f"canonical root directory does not exist: {root_path}")

    if not isinstance(artifacts, Mapping):
        raise ProposalError(f"artifacts must be Mapping, got {type(artifacts).__name__}")
    if not isinstance(evidence, Mapping):
        raise ProposalError(f"evidence must be Mapping, got {type(evidence).__name__}")

    raw_origin = artifacts.get("correction_origin")
    if "correction_origin" in evidence and evidence.get("correction_origin") != raw_origin:
        raise ProposalError(
            f"correction_origin mismatch: artifacts {raw_origin!r} != evidence {evidence.get('correction_origin')!r}"
        )
    if raw_origin is None or raw_origin == "REVIEWER":
        origin = "REVIEWER"
    elif raw_origin in ("BOOTSTRAP", "TEST_FAILED"):
        origin = raw_origin
    else:
        raise ProposalError(f"unknown correction_origin: {raw_origin!r}")

    worker_index = artifacts.get("worker_index")
    if type(worker_index) is not int or type(worker_index) is bool or worker_index != 2:
        raise ProposalError(f"worker_index must be exact int 2, got {worker_index!r}")

    orig_prefix = artifacts.get("original_run_prefix")
    if not isinstance(orig_prefix, str) or not RUN_PREFIX_RE.fullmatch(orig_prefix):
        raise ProposalError(f"invalid original_run_prefix: {orig_prefix!r}")

    final_worker_run_id = artifacts.get("final_worker_run_id")
    if not isinstance(final_worker_run_id, str):
        raise ProposalError(f"missing or non-string final_worker_run_id: {final_worker_run_id!r}")
    worker1_id_re = re.compile(rf"^{re.escape(orig_prefix)}-worker-1(?:-retry-[12])?$")
    if not worker1_id_re.fullmatch(final_worker_run_id):
        raise ProposalError(f"invalid final_worker_run_id: {final_worker_run_id!r}")

    final_reviewer_run_id: str | None = None
    if origin == "REVIEWER":
        final_reviewer_run_id = artifacts.get("final_reviewer_run_id")
        if not isinstance(final_reviewer_run_id, str):
            raise ProposalError(f"missing or non-string final_reviewer_run_id: {final_reviewer_run_id!r}")
        reviewer1_id_re = re.compile(rf"^{re.escape(orig_prefix)}-reviewer-1(?:-retry-[12])?$")
        if not reviewer1_id_re.fullmatch(final_reviewer_run_id):
            raise ProposalError(f"invalid final_reviewer_run_id: {final_reviewer_run_id!r}")
    else:
        if artifacts.get("final_reviewer_run_id") is not None:
            raise ProposalError(
                f"artifacts final_reviewer_run_id must be None for {origin}, got {artifacts.get('final_reviewer_run_id')!r}"
            )
        if artifacts.get("reviewer_path") is not None:
            raise ProposalError(
                f"artifacts reviewer_path must be None for {origin}, got {artifacts.get('reviewer_path')!r}"
            )
        if evidence.get("reviewer_status") is not None:
            raise ProposalError(f"evidence reviewer_status must be None for {origin}")
        if evidence.get("reviewer_result") is not None:
            raise ProposalError(f"evidence reviewer_result must be None for {origin}")
        if (
            evidence.get("reviewer_sha") is not None
            or evidence.get("reviewer_sha256") is not None
            or evidence.get("reviewer_result_sha") is not None
        ):
            raise ProposalError(f"evidence reviewer sha must be None for {origin}")

    proposal_sha256 = artifacts.get("proposal_sha256")
    if not isinstance(proposal_sha256, str) or not SHA_PATTERN.fullmatch(proposal_sha256):
        raise ProposalError(f"invalid proposal_sha256: {proposal_sha256!r}")

    worker_info = evidence.get("worker_info")
    if not isinstance(worker_info, Mapping):
        raise ProposalError(f"evidence missing valid worker_info Mapping, got {type(worker_info).__name__}")

    worker1_result_sha = worker_info.get("result_sha")
    if worker1_result_sha != proposal_sha256:
        raise ProposalError(
            f"Worker1 result_sha mismatch: validated {worker1_result_sha!r} != artifacts {proposal_sha256!r}"
        )

    worker1_status = worker_info.get("status")
    if not isinstance(worker1_status, Mapping):
        raise ProposalError(f"worker_info missing valid status Mapping, got {type(worker1_status).__name__}")

    status_worker1_id = worker1_status.get("run_id")
    if (
        type(status_worker1_id) is not str
        or not status_worker1_id.strip()
        or status_worker1_id != final_worker_run_id
    ):
        raise ProposalError(
            f"Worker1 status run_id mismatch: {status_worker1_id!r} != artifacts {final_worker_run_id!r}"
        )

    worker0_run_id = worker1_status.get("input_worker_run_id")
    if not isinstance(worker0_run_id, str):
        raise ProposalError(f"missing or non-string input_worker_run_id in Worker1 status: {worker0_run_id!r}")
    worker0_id_re = re.compile(rf"^{re.escape(orig_prefix)}-worker-0(?:-retry-[12])?$")
    if not worker0_id_re.fullmatch(worker0_run_id):
        raise ProposalError(f"invalid input_worker_run_id for Worker0: {worker0_run_id!r}")

    worker0_proposal_sha = worker1_status.get("input_proposal_sha256")
    if not isinstance(worker0_proposal_sha, str) or not SHA_PATTERN.fullmatch(worker0_proposal_sha):
        raise ProposalError(f"invalid input_proposal_sha256 in Worker1 status: {worker0_proposal_sha!r}")

    worker0_conv_id = worker1_status.get("input_worker_conversation_id")
    if not isinstance(worker0_conv_id, str) or not worker0_conv_id.strip():
        raise ProposalError(f"invalid Worker0 conversation_id: {worker0_conv_id!r}")

    worker1_conv_id = worker_info.get("conversation_id")
    if worker1_conv_id is None:
        worker1_conv_id = worker1_status.get("conversation_id")
    if not isinstance(worker1_conv_id, str) or not worker1_conv_id.strip():
        raise ProposalError(f"invalid Worker1 conversation_id: {worker1_conv_id!r}")
    if (
        worker1_status.get("conversation_id") is not None
        and worker_info.get("conversation_id") is not None
        and worker1_status.get("conversation_id") != worker_info.get("conversation_id")
    ):
        raise ProposalError("mismatch between worker_info conversation_id and status conversation_id")

    reviewer1_conv_id: str | None = None
    if origin == "REVIEWER":
        reviewer_status = evidence.get("reviewer_status")
        if not isinstance(reviewer_status, Mapping):
            raise ProposalError(f"evidence missing valid reviewer_status Mapping, got {type(reviewer_status).__name__}")

        reviewer1_conv_id = reviewer_status.get("conversation_id")
        if not isinstance(reviewer1_conv_id, str) or not reviewer1_conv_id.strip():
            raise ProposalError(f"invalid Reviewer1 conversation_id: {reviewer1_conv_id!r}")

        reviewer_status_run_id = reviewer_status.get("run_id")
        if (
            type(reviewer_status_run_id) is not str
            or not reviewer_status_run_id.strip()
            or reviewer_status_run_id != final_reviewer_run_id
        ):
            raise ProposalError(
                f"Reviewer1 status run_id mismatch: {reviewer_status_run_id!r} != artifacts {final_reviewer_run_id!r}"
            )

        conv_ids = [worker0_conv_id, worker1_conv_id, reviewer1_conv_id]
        if len(set(conv_ids)) != 3:
            raise ProposalError(f"duplicate conversation IDs detected across Worker0, Worker1, Reviewer1: {conv_ids}")
    else:
        if worker0_conv_id == worker1_conv_id:
            raise ProposalError(
                f"duplicate conversation IDs detected across Worker0 and Worker1: {[worker0_conv_id, worker1_conv_id]}"
            )

    feedback1_rel = Path(".local/two-site-refactor/pipeline") / orig_prefix / "feedback-correction-1.json"
    raw_feedback1 = bounded_file_bytes(
        root_path,
        feedback1_rel,
        MAX_CONTEXT,
        "feedback-correction-1",
    )
    feedback1 = validate_correction_feedback(raw_feedback1.decode("utf-8"), worker0_proposal_sha)

    fb_reviewer = feedback1.get("reviewer")
    if fb_reviewer == "test-gate":
        needs_reviewer0 = False
    elif fb_reviewer == "gemini":
        needs_reviewer0 = True
    elif fb_reviewer == "codex":
        raise ProposalError(f"unsupported original-pipeline history reviewer: {fb_reviewer!r}")
    else:
        raise ProposalError(f"unsupported feedback1 reviewer: {fb_reviewer!r}")

    return {
        "worker0_id": str(worker0_run_id),
        "worker0_run_id": str(worker0_run_id),
        "worker0_conversation_id": str(worker0_conv_id),
        "worker1_id": str(final_worker_run_id),
        "worker1_run_id": str(final_worker_run_id),
        "worker1_conversation_id": str(worker1_conv_id),
        "reviewer1_id": str(final_reviewer_run_id) if origin == "REVIEWER" else None,
        "reviewer1_run_id": str(final_reviewer_run_id) if origin == "REVIEWER" else None,
        "reviewer1_conversation_id": str(reviewer1_conv_id) if origin == "REVIEWER" else None,
        "prior_worker0_sha": str(worker0_proposal_sha),
        "feedback1_kind": str(fb_reviewer),
        "feedback1_reviewer": str(fb_reviewer),
        "feedback1": copy.deepcopy(feedback1),
        "needs_reviewer0": needs_reviewer0,
        "correction_origin": str(origin),
        "needs_reviewer1": origin == "REVIEWER",
    }
