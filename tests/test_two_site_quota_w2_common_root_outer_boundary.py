"""Hermetic test-only proof of outer dispatcher through Worker-2 once to external Provider boundary.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider outcome is synthetic.
This proves only the outer dispatch -> inner Worker-2 once -> Provider runner boundary seam,
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

from scripts.two_site_proposal_pipeline import ProposalError, pipeline_lock
from scripts.two_site_provider_broker import read_terminal_quota_bytes
from scripts.two_site_quota_resume_role_dispatch import (
    dispatch_quota_continuation_once,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


class _ProviderBoundaryReached(RuntimeError):
    """Private sentinel indicating runner_run boundary was successfully reached."""


def test_two_site_quota_w2_common_root_outer_boundary_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    task = ctx["task"]
    sc = ctx["sc"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    wait_file = ctx["wait_file"]
    raw_wait_bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256 = ctx["orig_wait_sha256"]
    claim_file = ctx["claim_file"]
    persisted_claim_bytes = ctx["persisted_claim_bytes"]
    claim_sha256 = ctx["claim_sha256"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    claim_epoch = datetime.fromisoformat(
        resume_request["claimed_at_utc"].replace("Z", "+00:00")
    ).timestamp()
    clock_delay = claim_epoch - time.time()
    assert clock_delay <= 2.0
    if clock_delay > 0:
        time.sleep(clock_delay + 0.05)

    boundary_hits: list[int] = []

    def _runner_stub(
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
        assert run_id_arg == f"{resume_request['run_prefix']}-worker-2"
        assert timeout_arg == int(resume_request["timeout"])
        expected_proposal = (
            root
            / ".local"
            / "two-site-refactor"
            / "proposals"
            / sc["meta"]["w1_id"]
            / "result.json"
        )
        assert proposal == expected_proposal
        assert proposal.is_file()
        expected_feedback = (
            root
            / ".local"
            / "two-site-refactor"
            / "pipeline"
            / sc["meta"]["orig_prefix"]
            / "feedback-correction-2.json"
        )
        assert feedback == expected_feedback
        assert feedback.is_file()
        assert test_evidence is None
        assert cli is None
        assert callable(pre_provider_check)

        with pytest.raises(ProposalError) as exc_lock:
            with pipeline_lock(root):
                pass
        assert "another proposal pipeline is already active" in str(exc_lock.value)

        pre_provider_check()
        boundary_hits.append(1)
        raise _ProviderBoundaryReached("external provider boundary reached")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _runner_stub,
    )

    with pytest.raises(_ProviderBoundaryReached) as exc_boundary:
        dispatch_quota_continuation_once(root, resume_request)
    assert "external provider boundary reached" in str(exc_boundary.value)
    assert boundary_hits == [1]

    with pipeline_lock(root):
        pass

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

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()
    continuation_run_id = f"{resume_request['run_prefix']}-worker-2"
    assert not (proposals_dir / continuation_run_id).exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}


def test_two_site_quota_w2_common_root_outer_boundary_negative_tampered_feedback_sha(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(broker_repo, frozen_clock_dt)

    root = ctx["root"]
    task = ctx["task"]
    sc = ctx["sc"]
    runs_dir = ctx["runs_dir"]
    controller_run_id = ctx["controller_run_id"]
    wait_file = ctx["wait_file"]
    raw_wait_bytes = ctx["raw_wait_bytes"]
    orig_wait_sha256 = ctx["orig_wait_sha256"]
    claim_file = ctx["claim_file"]
    persisted_claim_bytes = ctx["persisted_claim_bytes"]
    claim_sha256 = ctx["claim_sha256"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    feedback_file = (
        root
        / ".local"
        / "two-site-refactor"
        / "pipeline"
        / sc["meta"]["orig_prefix"]
        / "feedback-correction-2.json"
    )
    assert feedback_file.is_file()
    orig_feedback_bytes = feedback_file.read_bytes()
    feedback_dict = json.loads(orig_feedback_bytes.decode("utf-8"))
    orig_sha = feedback_dict["reviewed_proposal_sha256"]
    tampered_sha = "0" * 64 if orig_sha != "0" * 64 else "1" * 64
    feedback_dict["reviewed_proposal_sha256"] = tampered_sha
    tampered_feedback_bytes = json.dumps(feedback_dict, indent=2).encode("utf-8")
    feedback_file.write_bytes(tampered_feedback_bytes)

    def _fail_runner(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("runner_run must not be called when feedback SHA is tampered")

    monkeypatch.setattr(
        "scripts.two_site_quota_resume_worker_two_once.runner_run",
        _fail_runner,
    )

    claim_epoch = datetime.fromisoformat(
        resume_request["claimed_at_utc"].replace("Z", "+00:00")
    ).timestamp()
    clock_delay = claim_epoch - time.time()
    assert clock_delay <= 2.0
    if clock_delay > 0:
        time.sleep(clock_delay + 0.05)

    with pytest.raises(ProposalError) as exc_info:
        dispatch_quota_continuation_once(root, resume_request)
    assert "correction feedback targets a different proposal" in str(exc_info.value)

    with pipeline_lock(root):
        pass

    assert feedback_file.read_bytes() == tampered_feedback_bytes
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

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()
    continuation_run_id = f"{resume_request['run_prefix']}-worker-2"
    assert not (proposals_dir / continuation_run_id).exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
