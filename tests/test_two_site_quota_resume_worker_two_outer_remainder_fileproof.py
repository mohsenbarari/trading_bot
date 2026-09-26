"""Materialized outer remainder proof for Worker-2 resume lineage."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch
import pytest

from scripts.two_site_proposal_pipeline import (
    ProposalError,
    pipeline_lock,
)
from scripts.two_site_quota_resume_post_worker_two_remainder import (
    execute_worker_two_post_result_remainder,
)
from tests.test_two_site_proposal_pipeline import (
    make_runner,
    passed_receipt,
)
import tests.test_two_site_quota_resume_worker_two_fileproof as w2_fileproof
from tests.test_two_site_quota_resume_worker_two_history_materialized import (
    _patch_load_task,
)


def test_worker_two_outer_remainder_materialized_positive_proof(
    tmp_path: Path,
) -> None:
    # Explicit test-only proof scope:
    # Does not claim to prove real admission/request/terminal quota receipt storage,
    # real external sandbox/Provider execution, Broker/Supervisor hooks, or full fault matrix.
    root, ctx, returned_state, meta = w2_fileproof._setup_materialized_worker_two_env(
        tmp_path
    )

    continuation_run_prefix = ctx["continuation_run_prefix"]
    admitted_request = {
        "role": "worker",
        "original_request_id": "req-orig-20260926",
        "timeout": 300,
        "run_prefix": continuation_run_prefix,
    }
    original_terminal_receipt = {
        "status": "TERMINAL_RECORDED",
        "request_id": "req-orig-20260926",
    }
    initial_refs: dict[str, str | None] = {}

    r1_id = meta["r1_id"]
    ctx["artifacts"]["reviewer_path"] = (
        f".local/two-site-refactor/proposals/{r1_id}/result.json"
    )
    ctx["admitted_request"] = admitted_request
    ctx["original_terminal_receipt"] = original_terminal_receipt
    ctx["initial_refs"] = initial_refs

    task_path = ctx["admitted_task_path"]
    request = {"role": "worker"}

    lock_checked_during_provider = False

    runner, calls = make_runner(
        root,
        [
            {"status": "VERIFIED"},
        ],
    )

    def provider_adapter(
        r: Path,
        t: Path,
        role: str,
        run_id: str,
        timeout: int,
        _state_check: object,
        proposal: Path | None = None,
        feedback: Path | None = None,
        cli: Path | None = None,
        test_evidence: Path | None = None,
        **_kw: object,
    ) -> tuple[str, Path]:
        nonlocal lock_checked_during_provider
        # Verify real pipeline lock remains held while provider adapter runs
        with pytest.raises(
            ProposalError, match="another proposal pipeline is already active"
        ):
            with pipeline_lock(root):
                pass
        lock_checked_during_provider = True

        out = runner(
            r,
            t,
            role,
            run_id,
            timeout,
            feedback=feedback,
            proposal=proposal,
            cli=cli,
            test_evidence=test_evidence,
        )
        return run_id, out

    @contextmanager
    def fake_worker_correction_continuation_context(
        r: Path, req: dict[str, Any]
    ):
        with pipeline_lock(r):
            yield ctx

    with (
        _patch_load_task(),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.validate_quota_continuation_request",
            return_value=(admitted_request, task_path, None),
        ),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.read_terminal_quota_record",
            return_value=original_terminal_receipt,
        ),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.derive_worker_resume_artifacts",
            return_value=ctx["artifacts"],
        ),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.load_worker_correction_evidence",
            return_value=ctx["evidence"],
        ),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.check_state",
            return_value=None,
        ),
        patch(
            "scripts.two_site_proposal_pipeline.check_state",
            return_value=None,
        ),
        patch(
            "scripts.two_site_proposal_pipeline.run_candidate_tests",
            side_effect=lambda _r, _t, _w, w_sha, **_kw: passed_receipt(w_sha),
        ),
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=provider_adapter,
        ),
    ):
        final_state = execute_worker_two_post_result_remainder(
            root, request, returned_state
        )

    # 1. Verify lock checked during provider adapter
    assert lock_checked_during_provider is True

    # 2. Verify lock released afterward (we can re-acquire without error)
    with pipeline_lock(root):
        pass

    # 3. Verify receipt pre-review-tests-2.json PASSED SHA/state
    evidence_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / continuation_run_prefix
    )
    receipt_path = evidence_dir / "pre-review-tests-2.json"
    assert receipt_path.is_file()
    receipt_bytes = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    parsed_receipt = json.loads(receipt_bytes.decode("utf-8"))

    assert parsed_receipt["state"] == "PASSED"
    assert parsed_receipt["proposal_sha256"] == meta["w2_sha"]

    # 4. Verify final READY_FOR_CODEX with bound reviewer ID and path
    expected_reviewer_id = f"{continuation_run_prefix}-reviewer-2"
    expected_reviewer_dir = (
        root / ".local" / "two-site-refactor" / "proposals" / expected_reviewer_id
    )
    expected_reviewer_path = (
        expected_reviewer_dir / "result.json"
    ).relative_to(root).as_posix()

    assert final_state["state"] == "READY_FOR_CODEX"
    assert final_state["final_worker_run_id"] == meta["w2_id"]
    assert final_state["proposal_sha256"] == meta["w2_sha"]
    assert final_state["final_reviewer_run_id"] == expected_reviewer_id
    assert final_state["reviewer_path"] == expected_reviewer_path
    assert final_state["pre_review_test_state"] == "PASSED"
    assert (
        final_state["pre_review_test_path"]
        == receipt_path.relative_to(root).as_posix()
    )
    assert final_state["pre_review_test_sha256"] == receipt_sha
    assert final_state["correction_count"] == 2
    assert final_state["blockers"] == []

    # 5. Verify no Worker-3 and no feedback3
    assert not (evidence_dir / "feedback-correction-3.json").exists()
    w3_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{continuation_run_prefix}-worker-3"
    )
    assert not w3_dir.exists()

    # 6. Verify reviewer invocation and bindings
    assert len(calls) == 1
    assert calls[0][0] == "verifier"
    assert calls[0][1] == expected_reviewer_id

    reviewer_st = json.loads(
        (expected_reviewer_dir / "status.json").read_text(encoding="utf-8")
    )
    assert reviewer_st["state"] == "VERIFIED"
    assert reviewer_st["input_proposal_sha256"] == meta["w2_sha"]
    assert reviewer_st["input_worker_run_id"] == meta["w2_id"]
    assert reviewer_st["input_worker_conversation_id"] == meta["w2_conv_id"]
    assert reviewer_st["input_test_evidence_sha256"] == receipt_sha

    reviewer_task = json.loads(
        (expected_reviewer_dir / "task.json").read_text(encoding="utf-8")
    )
    assert reviewer_task["proposal"] == meta["w2_result_text"]
    assert reviewer_task["test_evidence"] == parsed_receipt
