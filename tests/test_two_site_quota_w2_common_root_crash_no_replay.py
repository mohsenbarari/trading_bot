"""Hermetic test-only proof of same-root W2 crash after worker with no replay.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider outcome is synthetic.
This proves only the outer dispatch -> Worker-2 once materialization -> crash before remainder -> duplicate fail-closed rejection flow,
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
from typing import Any

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
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


class CrashAfterWorker(RuntimeError):
    """Private sentinel indicating crash injected after Worker-2 materialization before remainder."""


def test_two_site_quota_w2_common_root_crash_no_replay(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

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
    remainder_hits: list[int] = []

    expected_worker_id = f"{resume_request['run_prefix']}-worker-2"
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
                    "content": "# valid distinct corrected w2 crash content\n",
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

    def _crash_remainder_stub(
        root_arg: Path,
        admitted_arg: dict[str, Any],
        intermediate_arg: dict[str, Any],
        cli_arg: Path | None = None,
    ) -> dict[str, Any]:
        assert root_arg == root
        assert admitted_arg["original_request_id"] == sc["request"]["request_id"]
        assert intermediate_arg["state"] == "WORKER_PROPOSAL_READY"
        assert intermediate_arg["correction_count"] == 2
        assert intermediate_arg["final_worker_run_id"] == expected_worker_id
        assert cli_arg is None
        remainder_hits.append(1)
        raise CrashAfterWorker(
            "injected crash after worker materialization before remainder"
        )

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _worker_runner_stub,
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder",
        _crash_remainder_stub,
    )

    with pytest.raises(CrashAfterWorker) as exc_crash:
        dispatch_quota_continuation_once(root, resume_request)
    assert "injected crash after worker materialization before remainder" in str(
        exc_crash.value
    )

    assert worker_stub_calls == [expected_worker_id]
    assert remainder_hits == [1]

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    pipeline_dir = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / resume_request["run_prefix"]
    )

    assert not expected_test_receipt_path.exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-2").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-worker-3").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-3").exists()
    assert not (pipeline_dir / "feedback-correction-3.json").exists()

    w2_proposal_dir = proposals_dir / expected_worker_id
    w2_result_path = w2_proposal_dir / "result.json"
    w2_status_path = w2_proposal_dir / "status.json"
    w2_task_path = w2_proposal_dir / "task.json"

    assert w2_result_path.is_file()
    assert w2_status_path.is_file()
    assert w2_task_path.is_file()

    w2_result_bytes = w2_result_path.read_bytes()
    w2_status_bytes = w2_status_path.read_bytes()
    w2_task_bytes = w2_task_path.read_bytes()
    w2_result_sha = hashlib.sha256(w2_result_bytes).hexdigest()
    w2_status_sha = hashlib.sha256(w2_status_bytes).hexdigest()
    w2_task_sha = hashlib.sha256(w2_task_bytes).hexdigest()

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
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    assert set(os.listdir(proposals_dir)) == expected_runs | {expected_worker_id}

    with pipeline_lock(root):
        pass

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}

    def _fail_runner_second(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("runner_run must not be called on second invocation of the same request")

    def _fail_remainder_second(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("execute_worker_two_post_result_remainder must not be reached on second invocation")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _fail_runner_second,
    )
    monkeypatch.setattr(
        "scripts.two_site_quota_resume_role_dispatch.execute_worker_two_post_result_remainder",
        _fail_remainder_second,
    )

    with pytest.raises(ProposalError) as exc_second:
        dispatch_quota_continuation_once(root, resume_request)
    assert f"worker run ID already exists: {expected_worker_id}" in str(exc_second.value)

    assert worker_stub_calls == [expected_worker_id]
    assert remainder_hits == [1]

    assert not expected_test_receipt_path.exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-2").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-worker-3").exists()
    assert not (proposals_dir / f"{resume_request['run_prefix']}-reviewer-3").exists()
    assert not (pipeline_dir / "feedback-correction-3.json").exists()

    assert w2_result_path.read_bytes() == w2_result_bytes
    assert hashlib.sha256(w2_result_path.read_bytes()).hexdigest() == w2_result_sha
    assert w2_status_path.read_bytes() == w2_status_bytes
    assert hashlib.sha256(w2_status_path.read_bytes()).hexdigest() == w2_status_sha
    assert w2_task_path.read_bytes() == w2_task_bytes
    assert hashlib.sha256(w2_task_path.read_bytes()).hexdigest() == w2_task_sha

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
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    assert set(os.listdir(proposals_dir)) == expected_runs | {expected_worker_id}

    with pipeline_lock(root):
        pass

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
