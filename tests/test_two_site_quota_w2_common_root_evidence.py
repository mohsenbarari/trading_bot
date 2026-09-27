"""Hermetic test-only real-seam join from W2 admission to prior correction evidence.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider outcome is synthetic.
This closes only the read-only admission -> correction-evidence seam,
not C5d, live replay hook, candidate sandbox, Provider dispatch, or fault matrix.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts.two_site_assignment_scope import load_active_assignment_scope
from scripts.two_site_proposal_pipeline import ProposalError
from scripts.two_site_provider_broker import (
    read_terminal_quota_bytes,
    read_terminal_quota_record,
)
from scripts.two_site_quota_continuation_admission import (
    validate_quota_continuation_request,
)
from scripts.two_site_quota_resume_worker_artifacts import (
    derive_worker_resume_artifacts,
)
from scripts.two_site_quota_resume_worker_evidence import (
    load_worker_correction_evidence,
)
from tests.test_two_site_provider_broker import broker_repo
from tests.test_two_site_quota_w2_common_root_admission import (
    _setup_w2_admission_claim_scenario,
)


def test_two_site_quota_w2_common_root_evidence_positive(
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
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    # Unpatched admission
    admitted_request, admitted_task_path, admitted_scope = (
        validate_quota_continuation_request(
            root,
            resume_request,
            now_epoch=now_epoch,
        )
    )
    expected_scope = load_active_assignment_scope(root)
    assert admitted_request == resume_request
    assert admitted_task_path == task
    assert admitted_scope == expected_scope
    assert admitted_request["original_request_id"] == sc["request"]["request_id"]

    # Unpatched terminal quota record read
    terminal_receipt = read_terminal_quota_record(
        root, admitted_request["original_request_id"]
    )
    assert isinstance(terminal_receipt, dict)

    # Unpatched derivation of worker resume artifacts
    artifacts = derive_worker_resume_artifacts(admitted_request, terminal_receipt)

    # Unpatched loading of detached correction evidence
    evidence = load_worker_correction_evidence(root, admitted_task_path, artifacts)

    # Assert exact Worker-2 / REVIEWER lineage
    assert artifacts["worker_index"] == 2
    assert artifacts["correction_origin"] == "REVIEWER"
    assert artifacts["final_worker_run_id"] == sc["meta"]["w1_id"]
    assert artifacts["final_reviewer_run_id"] == sc["meta"]["r1_id"]
    assert artifacts["proposal_sha256"] == sc["meta"]["w1_sha"]
    assert artifacts["attempted_run_id"] == sc["orig_w2_id"]
    assert artifacts["original_run_prefix"] == sc["meta"]["orig_prefix"]
    expected_feedback_rel = (
        f".local/two-site-refactor/pipeline/{sc['meta']['orig_prefix']}/feedback-correction-2.json"
    )
    assert artifacts["expected_feedback_path"] == expected_feedback_rel

    assert evidence["artifacts"] == artifacts
    assert evidence["feedback_path"] == expected_feedback_rel
    expected_fb_bytes = (root / artifacts["expected_feedback_path"]).read_bytes()
    assert evidence["feedback_sha"] == hashlib.sha256(expected_fb_bytes).hexdigest()
    assert evidence["feedback"]["reviewer"] == "gemini"
    assert evidence["feedback"]["reviewed_proposal_sha256"] == sc["meta"]["w1_sha"]

    # Pre-review test receipt assertions (evidence['receipt'] holds pre-review tests)
    pre_review_test_file = root / artifacts["pre_review_test_path"]
    assert pre_review_test_file.is_file()
    pre_review_test_bytes = pre_review_test_file.read_bytes()
    pre_review_test_sha = hashlib.sha256(pre_review_test_bytes).hexdigest()
    assert pre_review_test_sha == artifacts["pre_review_test_sha256"]
    assert evidence["receipt"] == json.loads(pre_review_test_bytes.decode("utf-8"))
    assert any(
        f"receipt_sha256={pre_review_test_sha}" in item
        for item in evidence["feedback"]["test_evidence"]
    )

    # Terminal quota record bytes/SHA preservation
    assert (
        hashlib.sha256(sc["raw_receipt_bytes"]).hexdigest()
        == sc["receipt_sha_snapshot"]
    )
    assert (
        hashlib.sha256(
            read_terminal_quota_bytes(root, admitted_request["original_request_id"])
        ).hexdigest()
        == sc["receipt_sha_snapshot"]
    )

    assert evidence["task"] == ctx["real_task"]
    assert isinstance(evidence["sources"], dict)
    w1_status_dict = json.loads(sc["w1_bytes_snapshot"].decode("utf-8"))
    r1_status_dict = json.loads(sc["r1_bytes_snapshot"].decode("utf-8"))
    assert evidence["worker_info"]["status"] == w1_status_dict
    assert evidence["worker_info"]["status"]["run_id"] == sc["meta"]["w1_id"]
    assert evidence["reviewer_status"] == r1_status_dict
    assert evidence["reviewer_status"]["run_id"] == sc["meta"]["r1_id"]

    # Independently assert original receipt/wait/claim/reservation/task/W1/R1/W2 status bytes and SHA
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
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    # Verify no new proposals or runs were created
    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}


def test_two_site_quota_w2_common_root_evidence_negative_tampered_feedback_sha(
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
    now_epoch = ctx["now_epoch"]
    resume_request = ctx["resume_request"]
    expected_runs = ctx["expected_runs"]

    # SAME unpatched admission, terminal quota record read, and derivation
    admitted_request, admitted_task_path, _ = validate_quota_continuation_request(
        root,
        resume_request,
        now_epoch=now_epoch,
    )
    receipt = read_terminal_quota_record(root, admitted_request["original_request_id"])
    artifacts = derive_worker_resume_artifacts(admitted_request, receipt)

    # Mutate ONLY existing root / artifacts['expected_feedback_path']
    feedback_file = root / artifacts["expected_feedback_path"]
    assert feedback_file.is_file()
    orig_feedback_bytes = feedback_file.read_bytes()
    feedback_dict = json.loads(orig_feedback_bytes.decode("utf-8"))
    orig_reviewed_sha = feedback_dict["reviewed_proposal_sha256"]
    tampered_reviewed_sha = (
        "0" * 64 if orig_reviewed_sha != "0" * 64 else "1" * 64
    )
    feedback_dict["reviewed_proposal_sha256"] = tampered_reviewed_sha
    tampered_feedback_bytes = json.dumps(feedback_dict, indent=2).encode("utf-8")
    feedback_file.write_bytes(tampered_feedback_bytes)

    # Snapshot core state and inventory before evidence read
    assert wait_file.read_bytes() == raw_wait_bytes
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert sc["r1_status_path"].read_bytes() == sc["r1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    # Call unpatched load_worker_correction_evidence and assert specific rejection
    with pytest.raises(ProposalError) as exc_info:
        load_worker_correction_evidence(root, admitted_task_path, artifacts)
    assert "correction feedback targets a different proposal" in str(exc_info.value)

    # Verify feedback remains the only deliberate mutation and no new artifacts exist
    assert feedback_file.read_bytes() == tampered_feedback_bytes
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256
    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
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
