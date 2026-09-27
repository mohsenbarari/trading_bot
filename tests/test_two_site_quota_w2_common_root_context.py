"""Hermetic test-only real-seam join from W2 admission to locked continuation context and history.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider outcome is synthetic.
This closes only the read-only admission -> continuation-context -> history seam,
not C5d, live replay hook, candidate sandbox, Provider dispatch, or fault matrix.
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
from scripts.two_site_quota_resume_worker_correction_context import (
    worker_correction_continuation_context,
)
from scripts.two_site_quota_resume_worker_two_history_complete import (
    complete_worker_two_history,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


def test_two_site_quota_w2_common_root_context_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
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
    if clock_delay > 0:
        time.sleep(clock_delay + 0.25)

    with worker_correction_continuation_context(root, resume_request) as bound:
        assert bound["admitted_request"] == resume_request
        assert bound["admitted_task_path"] == task

        artifacts = bound["artifacts"]
        assert artifacts["worker_index"] == 2
        assert artifacts["correction_origin"] == "REVIEWER"
        assert artifacts["final_worker_run_id"] == sc["meta"]["w1_id"]
        assert artifacts["final_reviewer_run_id"] == sc["meta"]["r1_id"]
        assert artifacts["proposal_sha256"] == sc["meta"]["w1_sha"]
        assert artifacts["attempted_run_id"] == sc["orig_w2_id"]
        assert artifacts["original_run_prefix"] == sc["meta"]["orig_prefix"]

        evidence = bound["evidence"]
        assert evidence["feedback"]["reviewed_proposal_sha256"] == sc["meta"]["w1_sha"]
        assert evidence["feedback"]["reviewer"] == "gemini"
        assert evidence["worker_info"]["status"]["run_id"] == sc["meta"]["w1_id"]
        assert evidence["reviewer_status"]["run_id"] == sc["meta"]["r1_id"]

        with pytest.raises(ProposalError) as exc_lock:
            with pipeline_lock(root):
                pass
        assert "another proposal pipeline is already active" in str(exc_lock.value)

        history = complete_worker_two_history(
            root,
            bound["admitted_task_path"],
            bound["artifacts"],
            bound["evidence"],
        )
        assert history["complete"] is True
        conv_ids = history["conversation_ids"]
        assert history["ordered_conversation_ids"] == conv_ids
        assert len(set(conv_ids)) == len(conv_ids)
        assert history["worker0_conversation_id"] == conv_ids[0]
        assert history["reviewer0_conversation_id"] == conv_ids[1]
        assert history["worker1_conversation_id"] == conv_ids[2]
        assert history["reviewer1_conversation_id"] == conv_ids[3]
        assert conv_ids[2] == evidence["worker_info"]["status"]["conversation_id"]
        assert conv_ids[3] == evidence["reviewer_status"]["conversation_id"]

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

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}


def test_two_site_quota_w2_common_root_context_negative_tampered_feedback_sha(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
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

    claim_epoch = datetime.fromisoformat(
        resume_request["claimed_at_utc"].replace("Z", "+00:00")
    ).timestamp()
    clock_delay = claim_epoch - time.time()
    if clock_delay > 0:
        time.sleep(clock_delay + 0.25)

    with pytest.raises(ProposalError) as exc_info:
        with worker_correction_continuation_context(root, resume_request):
            pytest.fail("worker_correction_continuation_context yielded unexpectedly")
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

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
