"""Hermetic test-only positive W2 TEST_FAILED original-receipt provenance proof.

Sentinel: observational quota, no replay authority
Note: The fixture's Provider outcome is synthetic.
This closes only the read-only admission -> correction-evidence seam for TEST_FAILED origin,
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
from scripts.two_site_proposal_pipeline import (
    ProposalError,
    test_gate_feedback,
)
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


def test_w2_failed_receipt_provenance_positive(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, frozen_clock_dt, origin="TEST_FAILED"
    )

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

    # Assert exact Worker-2 / TEST_FAILED lineage and absence of Reviewer-1
    assert artifacts["worker_index"] == 2
    assert artifacts["correction_origin"] == "TEST_FAILED"
    assert artifacts["final_worker_run_id"] == sc["meta"]["w1_id"]
    assert artifacts["final_reviewer_run_id"] is None
    assert artifacts["reviewer_path"] is None
    assert artifacts["pre_review_test_state"] == "FAILED"
    assert artifacts["proposal_sha256"] == sc["meta"]["w1_sha"]
    assert artifacts["attempted_run_id"] == sc["orig_w2_id"]
    assert artifacts["original_run_prefix"] == sc["meta"]["orig_prefix"]
    expected_feedback_rel = (
        f".local/two-site-refactor/pipeline/{sc['meta']['orig_prefix']}/feedback-correction-2.json"
    )
    assert artifacts["expected_feedback_path"] == expected_feedback_rel

    assert evidence["artifacts"] == artifacts
    assert evidence["feedback_path"] == expected_feedback_rel
    assert evidence["reviewer_result"] is None
    assert evidence["reviewer_status"] is None
    assert evidence["reviewer_sha"] is None
    assert sc["r1_status_path"] is None
    assert sc["r1_bytes_snapshot"] is None
    assert sc["meta"].get("r1_id") is None
    assert not (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{sc['request']['run_prefix']}-reviewer-1"
    ).exists()

    # Pre-review test receipt assertions on disk, artifacts, and terminal state
    receipt1_rel = (
        f".local/two-site-refactor/pipeline/{sc['request']['run_prefix']}/pre-review-tests-1.json"
    )
    receipt1_path = root / receipt1_rel
    assert receipt1_path.is_file()
    receipt1_bytes = receipt1_path.read_bytes()
    receipt1_sha = hashlib.sha256(receipt1_bytes).hexdigest()
    receipt1_data = json.loads(receipt1_bytes.decode("utf-8"))

    assert receipt1_data["state"] == "FAILED"
    assert isinstance(receipt1_data["returncode"], int)
    assert receipt1_data["returncode"] != 0
    assert artifacts["pre_review_test_path"] == receipt1_rel
    assert artifacts["pre_review_test_sha256"] == receipt1_sha
    assert artifacts["pre_review_test_state"] == "FAILED"

    terminal_pipeline_state = (
        terminal_receipt.get("pipeline_state")
        or (
            json.loads(terminal_receipt["response"]["stdout"])
            if "response" in terminal_receipt and "stdout" in terminal_receipt["response"]
            else json.loads(terminal_receipt.get("stdout", "{}"))
        )
    )
    assert terminal_pipeline_state["pre_review_test_path"] == receipt1_rel
    assert terminal_pipeline_state["pre_review_test_sha256"] == receipt1_sha
    assert terminal_pipeline_state["pre_review_test_state"] == "FAILED"

    # Loaded receipt equals parsed original bytes
    assert evidence["receipt"] == receipt1_data

    # Independently compare loaded feedback against authoritative producer contract
    expected_gate_feedback = test_gate_feedback(receipt1_data, sc["meta"]["w1_sha"])
    assert evidence["feedback"] == expected_gate_feedback
    assert evidence["feedback"]["reviewer"] == "test-gate"
    assert evidence["feedback"]["reviewed_proposal_sha256"] == sc["meta"]["w1_sha"]
    assert evidence["feedback"]["findings"] == expected_gate_feedback["findings"]

    test_evidence = evidence["feedback"]["test_evidence"]
    assert len(test_evidence) == 3
    assert test_evidence == expected_gate_feedback["test_evidence"]
    assert test_evidence[0] == f"returncode: {receipt1_data['returncode']}"
    assert test_evidence[1].startswith(
        f"selectors (count={len(receipt1_data['selectors'])}):"
    )
    assert test_evidence[2].startswith("normalized_failure_facts: ")
    assert not any("receipt_sha256" in item for item in test_evidence)
    assert "receipt_sha256" not in evidence["feedback"]

    # Feedback file raw SHA matches evidence feedback_sha
    feedback_file = root / artifacts["expected_feedback_path"]
    assert feedback_file.is_file()
    feedback_raw_bytes = feedback_file.read_bytes()
    feedback_raw_sha = hashlib.sha256(feedback_raw_bytes).hexdigest()
    assert evidence["feedback_sha"] == feedback_raw_sha

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

    # Task, sources, and worker info status verification
    assert evidence["task"] == ctx["real_task"]
    assert isinstance(evidence["sources"], dict)
    w1_status_dict = json.loads(sc["w1_bytes_snapshot"].decode("utf-8"))
    assert evidence["worker_info"]["status"] == w1_status_dict
    assert evidence["worker_info"]["status"]["run_id"] == sc["meta"]["w1_id"]

    # State and inventory preservation
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


def test_w2_failed_receipt_provenance_tamper_rejected(
    broker_repo: tuple[Path, Path, Any, dict[str, str]],
) -> None:
    # observational quota, no replay authority
    frozen_clock_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    ctx = _setup_w2_admission_claim_scenario(
        broker_repo, frozen_clock_dt, origin="TEST_FAILED"
    )

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

    # Assert exact Worker-2 / TEST_FAILED lineage and absence of Reviewer-1
    assert artifacts["worker_index"] == 2
    assert artifacts["correction_origin"] == "TEST_FAILED"
    assert artifacts["final_worker_run_id"] == sc["meta"]["w1_id"]
    assert artifacts["final_reviewer_run_id"] is None
    assert artifacts["reviewer_path"] is None
    assert artifacts["pre_review_test_state"] == "FAILED"
    assert artifacts["proposal_sha256"] == sc["meta"]["w1_sha"]
    assert artifacts["attempted_run_id"] == sc["orig_w2_id"]
    assert artifacts["original_run_prefix"] == sc["meta"]["orig_prefix"]
    expected_feedback_rel = (
        f".local/two-site-refactor/pipeline/{sc['meta']['orig_prefix']}/feedback-correction-2.json"
    )
    assert artifacts["expected_feedback_path"] == expected_feedback_rel

    # Pre-review test receipt path and legitimate original bytes snapshot
    receipt1_rel = (
        f".local/two-site-refactor/pipeline/{sc['request']['run_prefix']}/pre-review-tests-1.json"
    )
    receipt1_path = root / receipt1_rel
    assert receipt1_path.is_file()
    receipt1_bytes = receipt1_path.read_bytes()
    receipt1_sha = hashlib.sha256(receipt1_bytes).hexdigest()
    assert artifacts["pre_review_test_path"] == receipt1_rel
    assert artifacts["pre_review_test_sha256"] == receipt1_sha
    assert artifacts["pre_review_test_state"] == "FAILED"

    # Pre-tamper snapshot of feedback file
    feedback_file = root / artifacts["expected_feedback_path"]
    assert feedback_file.is_file()
    feedback_bytes_snapshot = feedback_file.read_bytes()
    feedback_sha_snapshot = hashlib.sha256(feedback_bytes_snapshot).hexdigest()

    # Tamper only original pre-review-tests-1.json receipt bytes
    # while keeping a regular 0600 file and a valid JSON object with FAILED/nonzero semantics
    receipt1_data = json.loads(receipt1_bytes.decode("utf-8"))
    assert receipt1_data["state"] == "FAILED"
    assert isinstance(receipt1_data["returncode"], int) and receipt1_data["returncode"] != 0

    tampered_receipt_data = dict(receipt1_data)
    tampered_receipt_data["returncode"] = (
        2 if receipt1_data["returncode"] != 2 else 3
    )
    tampered_bytes = (
        json.dumps(
            tampered_receipt_data,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    assert tampered_bytes != receipt1_bytes
    tampered_sha = hashlib.sha256(tampered_bytes).hexdigest()
    assert tampered_sha != receipt1_sha

    receipt1_path.write_bytes(tampered_bytes)
    os.chmod(receipt1_path, 0o600)

    # Verify tampered receipt file properties on disk
    assert receipt1_path.is_file()
    assert stat.S_ISREG(receipt1_path.stat().st_mode)
    assert stat.S_IMODE(receipt1_path.stat().st_mode) == 0o600
    assert receipt1_path.stat().st_nlink == 1
    tampered_on_disk = json.loads(receipt1_path.read_bytes().decode("utf-8"))
    assert tampered_on_disk["state"] == "FAILED"
    assert isinstance(tampered_on_disk["returncode"], int)
    assert tampered_on_disk["returncode"] != 0

    # Call unpatched load_worker_correction_evidence and assert ProposalError for exact pre-review receipt SHA mismatch
    with pytest.raises(ProposalError) as exc_info:
        load_worker_correction_evidence(root, admitted_task_path, artifacts)
    assert f"pre-review test receipt SHA mismatch: computed {tampered_sha!r} != expected {receipt1_sha!r}" == str(exc_info.value)

    # Assert unchanged task, reservation, initial wait, claim, terminal quota record, W1/W2 status, feedback, and proposal/run directory inventory, and no Reviewer-1 lineage
    assert task.read_bytes() == sc["task_bytes_snapshot"]
    assert hashlib.sha256(task.read_bytes()).hexdigest() == sc["task_sha_snapshot"]

    assert sc["reservation_file"].read_bytes() == sc["reservation_bytes_snapshot"]
    assert hashlib.sha256(sc["reservation_file"].read_bytes()).hexdigest() == hashlib.sha256(sc["reservation_bytes_snapshot"]).hexdigest()

    assert stat.S_IMODE(wait_file.stat().st_mode) == 0o600
    assert wait_file.read_bytes() == raw_wait_bytes
    assert hashlib.sha256(wait_file.read_bytes()).hexdigest() == orig_wait_sha256

    assert stat.S_IMODE(claim_file.stat().st_mode) == 0o600
    assert claim_file.read_bytes() == persisted_claim_bytes
    assert hashlib.sha256(claim_file.read_bytes()).hexdigest() == claim_sha256

    assert (
        read_terminal_quota_bytes(root, sc["request"]["request_id"])
        == sc["raw_receipt_bytes"]
    )
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

    assert sc["w1_status_path"].read_bytes() == sc["w1_bytes_snapshot"]
    assert (
        hashlib.sha256(sc["orig_w2_status_path"].read_bytes()).hexdigest()
        == sc["orig_w2_status_sha"]
    )

    assert feedback_file.is_file()
    assert feedback_file.read_bytes() == feedback_bytes_snapshot
    assert hashlib.sha256(feedback_file.read_bytes()).hexdigest() == feedback_sha_snapshot

    assert sc["r1_status_path"] is None
    assert sc["r1_bytes_snapshot"] is None
    assert sc["meta"].get("r1_id") is None
    assert not (
        root
        / ".local"
        / "two-site-refactor"
        / "proposals"
        / f"{sc['request']['run_prefix']}-reviewer-1"
    ).exists()

    proposals_dir = root / ".local" / "two-site-refactor" / "proposals"
    assert set(os.listdir(proposals_dir)) == expected_runs
    assert not (proposals_dir / sc["orig_w2_id"] / "result.json").exists()
    assert not (proposals_dir / sc["orig_w2_id"] / "reviewer.json").exists()

    assert set(os.listdir(runs_dir)) == ctx["runs_dir_entries"]
    runs_parent = runs_dir.parent
    assert set(os.listdir(runs_parent)) == {controller_run_id}
