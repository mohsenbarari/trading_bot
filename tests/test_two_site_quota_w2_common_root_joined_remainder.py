"""Hermetic test-only proof of joined same-root Worker-2 once through remainder to READY_FOR_CODEX.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider and candidate test outcomes are synthetic.
This proves only the joined outer dispatch -> Worker-2 once -> remainder -> READY_FOR_CODEX flow,
not C5d completion, live replay hook, candidate sandbox, live Provider dispatch, or fault matrix.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_proposal_pipeline import (
    ProposalError,
    pipeline_lock,
)
from scripts.two_site_proposal_runner import load_task
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_resume_role_dispatch import (
    dispatch_quota_continuation_once,
)
from tests.test_two_site_proposal_pipeline import (
    make_runner,
    passed_receipt,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


@pytest.mark.parametrize("origin", ["REVIEWER", "BOOTSTRAP"])
def test_two_site_quota_w2_common_root_joined_remainder_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, frozen_clock_dt, origin=origin
    )

    root: Path = ctx["root"]
    task: Path = ctx["task"]
    real_task: dict[str, Any] = ctx["real_task"]
    sc: dict[str, Any] = ctx["sc"]
    sc_head: str = sc["head"]
    runs_dir: Path = ctx["runs_dir"]
    controller_run_id: str = ctx["controller_run_id"]
    wait_file: Path = ctx["wait_file"]
    raw_wait_bytes: bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256: str = ctx["orig_wait_sha256"]
    claim_file: Path = ctx["claim_file"]
    persisted_claim_bytes: bytes = ctx["persisted_claim_bytes"]
    claim_sha256: str = ctx["claim_sha256"]
    resume_request: dict[str, Any] = ctx["resume_request"]
    expected_runs: set[str] = ctx["expected_runs"]

    claim_epoch = datetime.fromisoformat(
        resume_request["claimed_at_utc"].replace("Z", "+00:00")
    ).timestamp()
    clock_delay = claim_epoch - time.time()
    assert clock_delay <= 2.0
    if clock_delay > 0:
        wait_duration = clock_delay + 0.05
        assert wait_duration <= 2.05
        time.sleep(wait_duration)

    worker_stub_calls: list[str] = []
    candidate_stub_calls: list[str] = []
    reviewer_stub_calls: list[str] = []

    expected_worker_id = f"{resume_request['run_prefix']}-worker-2"
    expected_reviewer_id = f"{resume_request['run_prefix']}-reviewer-2"
    expected_w1_proposal = (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / sc["meta"]["w1_id"]
        / "result.json"
    )
    expected_w2_feedback = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / sc["meta"]["orig_prefix"]
        / "feedback-correction-2.json"
    )
    expected_test_receipt_path = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / resume_request["run_prefix"]
        / "pre-review-tests-2.json"
    )

    def _worker_runner_stub(
        root_arg: Path,
        task_path_arg: Path,
        role_arg: str,
        run_id_arg: str,
        timeout_arg: int,
        *,
        proposal: Path | None = None,
        feedback: Path | None = None,
        test_evidence: Any = None,
        cli: Any = None,
        pre_provider_check: Any = None,
    ) -> Path:
        assert root_arg == root
        assert task_path_arg == task
        assert role_arg == "worker"
        assert run_id_arg == expected_worker_id
        assert timeout_arg == int(resume_request["timeout"])
        assert proposal == expected_w1_proposal
        assert proposal.is_file()
        assert feedback == expected_w2_feedback
        assert feedback.is_file()
        assert test_evidence is None
        assert cli is None
        assert callable(pre_provider_check)

        with pytest.raises(ProposalError) as exc_lock:
            with pipeline_lock(root):
                pass
        assert "another proposal pipeline is already active" in str(exc_lock.value)

        pre_provider_check()

        loaded_task, loaded_sources = load_task(root_arg, task_path_arg)
        assert loaded_task == real_task
        assert isinstance(loaded_sources, dict)

        w2_runner, _ = make_runner(
            root,
            [
                {
                    "status": "PROPOSAL_READY",
                    "content": "# valid distinct corrected w2 content\n",
                    "conv_id": f"conv-{run_id_arg}",
                    "task_override": {"sources": loaded_sources},
                }
            ],
            source_head=sc_head,
        )
        run_dir = w2_runner(
            root_arg,
            task_path_arg,
            role_arg,
            run_id_arg,
            timeout_arg,
            feedback=feedback,
            proposal=proposal,
            cli=cli,
            test_evidence=test_evidence,
        )
        worker_stub_calls.append(run_id_arg)
        return run_dir

    def _candidate_tests_stub(
        root_arg: Path,
        task_arg: dict,
        worker_result_arg: dict,
        worker_sha_arg: str,
        timeout: int = 1200,
    ) -> dict:
        assert root_arg == root
        assert task_arg == real_task
        assert worker_result_arg["status"] == "PROPOSAL_READY"
        assert isinstance(worker_sha_arg, str) and len(worker_sha_arg) == 64
        assert timeout == 1200

        with pytest.raises(ProposalError) as exc_lock:
            with pipeline_lock(root):
                pass
        assert "another proposal pipeline is already active" in str(exc_lock.value)

        receipt = passed_receipt(worker_sha_arg)
        candidate_stub_calls.append(worker_sha_arg)
        return receipt

    def _reviewer_stub(
        root_arg: Path,
        task_path_arg: Path,
        role_arg: str,
        base_run_id_arg: str,
        timeout_arg: int,
        state_check_arg: Callable[[], None],
        feedback: Path | None = None,
        proposal: Path | None = None,
        cli: Path | None = None,
        test_evidence: Path | None = None,
        expected_head: str | None = None,
    ) -> tuple[str, Path]:
        assert root_arg == root
        assert task_path_arg == task
        assert role_arg == "verifier"
        assert base_run_id_arg == expected_reviewer_id
        assert timeout_arg == int(resume_request["timeout"])
        assert feedback is None
        assert proposal == root / ".local/two-site-refactor/proposals" / expected_worker_id / "result.json"
        assert proposal.is_file()
        assert cli is None
        assert test_evidence == expected_test_receipt_path
        assert test_evidence.is_file()

        with pytest.raises(ProposalError) as exc_lock:
            with pipeline_lock(root):
                pass
        assert "another proposal pipeline is already active" in str(exc_lock.value)

        assert callable(state_check_arg)
        state_check_arg()

        assert expected_head == sc_head

        loaded_task, loaded_sources = load_task(root_arg, task_path_arg)
        assert loaded_task == real_task
        assert isinstance(loaded_sources, dict)

        reviewer_runner, _ = make_runner(
            root,
            [
                {
                    "status": "VERIFIED",
                    "conv_id": f"conv-{base_run_id_arg}",
                    "task_override": {"sources": loaded_sources},
                }
            ],
            source_head=sc_head,
        )
        run_dir = reviewer_runner(
            root_arg,
            task_path_arg,
            role_arg,
            base_run_id_arg,
            timeout_arg,
            feedback=feedback,
            proposal=proposal,
            cli=cli,
            test_evidence=test_evidence,
        )
        reviewer_stub_calls.append(base_run_id_arg)
        return base_run_id_arg, run_dir

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _worker_runner_stub,
    )
    monkeypatch.setattr(
        "scripts.two_site_proposal_pipeline.run_candidate_tests",
        _candidate_tests_stub,
    )
    monkeypatch.setattr(
        "scripts.two_site_proposal_pipeline.run_provider_session",
        _reviewer_stub,
    )

    final_result = dispatch_quota_continuation_once(root, resume_request)

    assert worker_stub_calls == [expected_worker_id]
    assert len(candidate_stub_calls) == 1
    assert reviewer_stub_calls == [expected_reviewer_id]

    assert final_result["state"] == "READY_FOR_CODEX"
    assert final_result["correction_count"] == 2
    assert final_result["task_id"] == real_task["task_id"]
    assert final_result["task_id"] == resume_request["task_id"]
    assert final_result["source_head"] == sc_head
    assert (
        final_result["semantic_task_fingerprint"]
        == resume_request["semantic_task_fingerprint"]
    )
    assert final_result["final_worker_run_id"] == expected_worker_id
    assert final_result["final_reviewer_run_id"] == expected_reviewer_id
    assert final_result["blockers"] == []

    worker_proposal_path = root / final_result["proposal_path"]
    assert (
        worker_proposal_path
        == root / ".local/two-site-refactor/proposals" / expected_worker_id / "result.json"
    )
    assert worker_proposal_path.is_file()
    worker_proposal_bytes = worker_proposal_path.read_bytes()
    worker_proposal_sha = hashlib.sha256(worker_proposal_bytes).hexdigest()
    assert final_result["proposal_sha256"] == worker_proposal_sha
    assert candidate_stub_calls == [worker_proposal_sha]

    receipt_path = root / final_result["pre_review_test_path"]
    assert receipt_path == expected_test_receipt_path
    assert receipt_path.is_file()
    receipt_bytes = receipt_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    assert final_result["pre_review_test_sha256"] == receipt_sha
    assert final_result["pre_review_test_state"] == "PASSED"

    reviewer_path = root / final_result["reviewer_path"]
    assert reviewer_path.is_file()
    reviewer_bytes = reviewer_path.read_bytes()
    reviewer_sha = hashlib.sha256(reviewer_bytes).hexdigest()

    reviewer_status_file = reviewer_path.with_name("status.json")
    assert reviewer_status_file.is_file()
    reviewer_st = json.loads(reviewer_status_file.read_text(encoding="utf-8"))
    assert reviewer_st["state"] == "VERIFIED"
    assert reviewer_st["run_id"] == expected_reviewer_id
    assert reviewer_st["role"] == "verifier"
    assert reviewer_st["head"] == sc_head
    assert reviewer_st["result_sha256"] == reviewer_sha
    assert reviewer_st["input_worker_run_id"] == expected_worker_id
    assert reviewer_st["input_proposal_sha256"] == worker_proposal_sha
    assert reviewer_st["input_test_evidence_sha256"] == receipt_sha

    reviewer_task_file = reviewer_path.with_name("task.json")
    assert reviewer_task_file.is_file()
    reviewer_task = json.loads(reviewer_task_file.read_text(encoding="utf-8"))
    assert reviewer_task["proposal"] == worker_proposal_bytes.decode("utf-8")
    assert reviewer_task["test_evidence"] == json.loads(receipt_bytes.decode("utf-8"))

    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    if origin == "REVIEWER":
        assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    elif origin == "BOOTSTRAP":
        assert sc["r1_status_path"] is None
        assert sc["r1_bytes_snapshot"] is None
        assert sc["meta"].get("r1_id") is None
    else:
        pytest.fail(f"unsupported origin: {origin!r}")
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs | {
        expected_worker_id,
        expected_reviewer_id,
    }

    evidence_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / resume_request["run_prefix"]
    )
    assert not (evidence_dir / "feedback-correction-3.json").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-worker-3").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-3").exists()

    if origin == "BOOTSTRAP":
        assert not any("reviewer-1" in name for name in os.listdir(proposals_dir))
        assert not (evidence_dir / "pre-review-tests-1.json").exists()
        orig_evidence_dir = (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["meta"]["orig_prefix"]
        )
        assert not (orig_evidence_dir / "pre-review-tests-1.json").exists()
        assert (proposals_dir / expected_reviewer_id).is_dir()
        assert reviewer_st["state"] == "VERIFIED"

    with pipeline_lock(root):
        pass

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
