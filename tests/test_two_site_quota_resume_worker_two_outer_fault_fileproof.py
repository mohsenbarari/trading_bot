"""Materialized outer fault proof for Worker-2 resume lineage."""

from __future__ import annotations

from contextlib import contextmanager
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
import tests.test_two_site_quota_resume_worker_two_fileproof as w2_fileproof
from tests.test_two_site_quota_resume_worker_two_history_materialized import (
    _patch_load_task,
)


def test_worker_two_outer_remainder_feedback_sha_tamper_rejected(
    tmp_path: Path,
) -> None:
    # Explicit test-only proof scope:
    # Proves outer execute_worker_two_post_result_remainder rejects real file-backed
    # W2 status input_feedback_sha256 tampering in binder before candidate tests or Provider.
    # Does not claim to prove live hooks, replay, or full external quota admission.
    root, ctx, returned_state, meta = w2_fileproof._setup_materialized_worker_two_env(
        tmp_path
    )

    r1_id = meta["r1_id"]
    ctx["artifacts"]["reviewer_path"] = (
        f".local/two-site-refactor/proposals/{r1_id}/result.json"
    )
    ctx["admitted_request"] = {"role": "worker"}

    # Tamper actual W2 status.json input_feedback_sha256
    w2_id = meta["w2_id"]
    w2_status_path = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / w2_id
        / "status.json"
    )
    st = json.loads(w2_status_path.read_text(encoding="utf-8"))
    st["input_feedback_sha256"] = "f" * 64
    w2_status_path.write_text(json.dumps(st), encoding="utf-8")

    @contextmanager
    def fake_worker_correction_continuation_context(
        r: Path, req: dict[str, Any]
    ):
        with pipeline_lock(r):
            yield ctx

    def _fail_sentinel(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Provider/candidate tests sentinel called unexpectedly")

    with (
        _patch_load_task(),
        patch(
            "scripts.two_site_quota_resume_post_worker_two.worker_correction_continuation_context",
            side_effect=fake_worker_correction_continuation_context,
        ),
        patch(
            "scripts.two_site_proposal_pipeline.run_candidate_tests",
            side_effect=_fail_sentinel,
        ),
        patch(
            "scripts.two_site_proposal_pipeline.run_provider_session",
            side_effect=_fail_sentinel,
        ),
    ):
        with pytest.raises(ProposalError, match="feedback SHA mismatch"):
            execute_worker_two_post_result_remainder(
                root, {"role": "worker"}, returned_state
            )

    # 1. Held lock released and reacquirable after exception
    with pipeline_lock(root):
        pass

    # 2. No continuation evidence directory created for remainder run
    continuation_run_prefix = ctx["continuation_run_prefix"]
    evidence_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / continuation_run_prefix
    )
    assert not evidence_dir.exists()

    # 3. No feedback-correction-3.json or w3 directory created
    assert not (evidence_dir / "feedback-correction-3.json").exists()
    w3_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{continuation_run_prefix}-worker-3"
    )
    assert not w3_dir.exists()
    r2_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{continuation_run_prefix}-reviewer-2"
    )
    assert not r2_dir.exists()
