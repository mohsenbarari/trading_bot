"""Hermetic joined admission, derivation, and evidence fileproof for Worker-2 resume lineage.

This test establishes hermetic verification of same-lineage quota continuation:
1. A real Broker terminal quota record is published and read back for the original Worker-2 request.
2. Initial wait state is derived directly from this exact terminal receipt and validated via
   validate_initial_wait_record before being persisted via publish_initial_wait_record_bytes.
3. A known-reset claim is prepared via prepare_known_reset_resume_claim and published via
   publish_known_reset_resume_claim, bound to the exact wait record bytes.
4. A continuation request is built via build_quota_resume_request and verified through real
   validate_quota_continuation_request with unpatched wait/claim storage readers and pure validators.
5. Terminal quota record is read back using the admitted original_request_id, feeding real
   derive_worker_resume_artifacts and real load_worker_correction_evidence to verify exact
   W1/R1/receipt1/feedback2 file-backed lineage.

Explicit boundaries:
- No Provider/sandbox execution or Broker/Supervisor live daemon hooks.
- No replay authority or claim of full real admission or C5d completion.
- Repository, pending rescope intent, semantic attempt record, and common request validator are
  explicitly isolated mock seams.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.two_site_codex_autopilot import exclusive_supervisor_lock
    from scripts.two_site_provider_broker import (
        publish_terminal_quota_record,
        read_terminal_quota_record,
    )
    from scripts.two_site_quota_continuation_admission import (
        validate_quota_continuation_request,
    )
    from scripts.two_site_quota_resume_claim_admission import (
        prepare_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_claim_storage import (
        publish_known_reset_resume_claim,
    )
    from scripts.two_site_quota_resume_request_record import (
        build_quota_resume_request,
    )
    from scripts.two_site_quota_resume_worker_artifacts import (
        derive_worker_resume_artifacts,
    )
    from scripts.two_site_quota_resume_worker_evidence import (
        load_worker_correction_evidence,
    )
    import scripts.two_site_quota_resume_worker_evidence as evidence_mod
    from scripts.two_site_quota_wait_record import validate_initial_wait_record
    from scripts.two_site_quota_wait_schedule import initial_wait_schedule
    from scripts.two_site_quota_wait_storage import (
        publish_initial_wait_record_bytes,
    )
except ImportError:
    from two_site_codex_autopilot import exclusive_supervisor_lock  # type: ignore[no-redef]
    from two_site_provider_broker import (  # type: ignore[no-redef]
        publish_terminal_quota_record,
        read_terminal_quota_record,
    )
    from two_site_quota_continuation_admission import (  # type: ignore[no-redef]
        validate_quota_continuation_request,
    )
    from two_site_quota_resume_claim_admission import (  # type: ignore[no-redef]
        prepare_known_reset_resume_claim,
    )
    from two_site_quota_resume_claim_storage import (  # type: ignore[no-redef]
        publish_known_reset_resume_claim,
    )
    from two_site_quota_resume_request_record import (  # type: ignore[no-redef]
        build_quota_resume_request,
    )
    from two_site_quota_resume_worker_artifacts import (  # type: ignore[no-redef]
        derive_worker_resume_artifacts,
    )
    from two_site_quota_resume_worker_evidence import (  # type: ignore[no-redef]
        load_worker_correction_evidence,
    )
    import two_site_quota_resume_worker_evidence as evidence_mod  # type: ignore[no-redef]
    from two_site_quota_wait_record import validate_initial_wait_record  # type: ignore[no-redef]
    from two_site_quota_wait_schedule import initial_wait_schedule  # type: ignore[no-redef]
    from two_site_quota_wait_storage import (  # type: ignore[no-redef]
        publish_initial_wait_record_bytes,
    )

try:
    from tests.test_two_site_proposal_pipeline import HEAD
    from tests.test_two_site_provider_broker_cycle_reservation_read import (
        _setup_cycle_root,
        _write_startup_json,
    )
    from tests.test_two_site_quota_continuation_broker_admission import (
        _canonical_bytes,
        _FakeScope,
    )
    from tests.test_two_site_quota_resume_claim_admission import (
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )
    from tests.test_two_site_quota_resume_worker_two_history_materialized import (
        _fake_load_task,
        _patch_load_task,
    )
    from tests.test_two_site_quota_resume_worker_two_terminal_receipt_fileproof import (
        _setup_materialized_worker_two_terminal_scenario,
    )
except ImportError:
    from test_two_site_proposal_pipeline import HEAD  # type: ignore[no-redef]
    from test_two_site_provider_broker_cycle_reservation_read import (  # type: ignore[no-redef]
        _setup_cycle_root,
        _write_startup_json,
    )
    from test_two_site_quota_continuation_broker_admission import (  # type: ignore[no-redef]
        _canonical_bytes,
        _FakeScope,
    )
    from test_two_site_quota_resume_claim_admission import (  # type: ignore[no-redef]
        _build_valid_semantic_attempt,
        _build_valid_wait_fixtures,
        _step_iso_seconds,
    )
    from test_two_site_quota_resume_worker_two_history_materialized import (  # type: ignore[no-redef]
        _fake_load_task,
        _patch_load_task,
    )
    from test_two_site_quota_resume_worker_two_terminal_receipt_fileproof import (  # type: ignore[no-redef]
        _setup_materialized_worker_two_terminal_scenario,
    )


def test_two_site_quota_resume_worker_two_joined_admission_fileproof(tmp_path: Path) -> None:
    (
        root,
        request,
        response,
        admitted_request,
        canonical_task_path,
        orig_status_path,
        meta,
    ) = _setup_materialized_worker_two_terminal_scenario(tmp_path)

    # Reconcile semantic task fingerprint to canonical 64 lowercase hex
    raw_fp = admitted_request["semantic_task_fingerprint"]
    valid_fp = (
        raw_fp
        if len(raw_fp) == 64 and all(c in "0123456789abcdef" for c in raw_fp)
        else hashlib.sha256(raw_fp.encode("utf-8")).hexdigest()
    )
    admitted_request["semantic_task_fingerprint"] = valid_fp
    response = json.loads(json.dumps(response).replace(raw_fp, valid_fp))

    # 1. Publish real Broker terminal quota record and read back
    before_files = set(root.rglob("*.json"))
    published_receipt = publish_terminal_quota_record(root, request, response)
    created_receipt_files = [p for p in root.rglob("*.json") if p not in before_files]
    assert len(created_receipt_files) == 1
    receipt_file_path = created_receipt_files[0]
    receipt_bytes = receipt_file_path.read_bytes()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()

    read_receipt = read_terminal_quota_record(root, request["request_id"])
    assert read_receipt == published_receipt
    assert json.loads(receipt_bytes.decode("utf-8")) == read_receipt

    # 2. Derive initial wait record from this exact receipt and ordinary request
    reservation = {
        "controller_run_id": request["controller_run_id"],
        "run_prefix": request["run_prefix"],
        "task_path": request["task_path"],
        "task_sha256": request["task_sha256"],
        "source_head": request["source_head"],
        "assignment_id": request["assignment_id"],
        "assignment_sha256": request["assignment_sha256"],
    }
    request_sha = hashlib.sha256(_canonical_bytes(request)).hexdigest()
    response_sha = hashlib.sha256(_canonical_bytes(response)).hexdigest()
    quota_evidence = read_receipt["quota_evidence"]

    orig_w2_id = f"{meta['orig_prefix']}-worker-2"
    orig_status_sha = hashlib.sha256(orig_status_path.read_bytes()).hexdigest()
    quota_attempt = {
        "role": "worker",
        "run_id": orig_w2_id,
        "status_sha256": orig_status_sha,
    }

    approved_fixture, _ = _build_valid_wait_fixtures(known_reset=True)
    rescope_epoch = approved_fixture["rescope_epoch"]
    rescope_attempt = 1

    repo = copy.deepcopy(approved_fixture["repository"])
    repo["head"] = reservation["source_head"]
    repo["protected_refs"] = {"refs/heads/main": reservation["source_head"]}
    repo["control_blobs"] = {"config/rules.json": reservation["source_head"]}
    repo["assignment_scope"]["assignment_id"] = reservation["assignment_id"]
    repo["assignment_scope"]["sha256"] = reservation["assignment_sha256"]

    created_at_utc = datetime.fromtimestamp(request["created_at"], tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    schedule = initial_wait_schedule(quota_evidence, created_at_utc)

    wait_record = {
        "schema_version": "1.0",
        "state": "WAITING_QUOTA",
        "controller_run_id": request["controller_run_id"],
        "request_id": request["request_id"],
        "reservation": reservation,
        "request_sha256": request_sha,
        "terminal_receipt_sha256": receipt_sha,
        "response_sha256": response_sha,
        "semantic_task_fingerprint": admitted_request["semantic_task_fingerprint"],
        "rescope_epoch": rescope_epoch,
        "rescope_attempt": rescope_attempt,
        "quota_attempt": quota_attempt,
        "quota_evidence": quota_evidence,
        "schedule": schedule,
        "repository": repo,
    }
    expected_wait_keys = (
        "controller_run_id",
        "request_id",
        "reservation",
        "request_sha256",
        "terminal_receipt_sha256",
        "response_sha256",
        "semantic_task_fingerprint",
        "rescope_epoch",
        "rescope_attempt",
        "quota_attempt",
        "quota_evidence",
        "repository",
    )
    expected_wait = {k: copy.deepcopy(wait_record[k]) for k in expected_wait_keys}

    # Pure validation of initial wait record
    validated_wait = validate_initial_wait_record(wait_record, expected_wait)
    assert validated_wait == wait_record
    assert wait_record["terminal_receipt_sha256"] == receipt_sha
    assert wait_record["terminal_receipt_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()

    # 3. Materialize original cycle run dir and publish wait record bytes
    orig_ctrl_run_id = request["controller_run_id"]
    _, runs_dir = _setup_cycle_root(str(root), orig_ctrl_run_id)
    _write_startup_json(
        runs_dir,
        cycle_id=orig_ctrl_run_id,
        source_head=request["source_head"],
        assignment_sha256=request["assignment_sha256"],
    )
    autopilot_dir = root / ".local" / "two-site-refactor" / "autopilot"
    lock_file = autopilot_dir / "supervisor.lock"

    raw_wait_bytes = _canonical_bytes(wait_record)
    wait_sha = hashlib.sha256(raw_wait_bytes).hexdigest()

    with exclusive_supervisor_lock(lock_file):
        published_wait_bytes = publish_initial_wait_record_bytes(
            root,
            orig_ctrl_run_id,
            raw_wait_bytes,
        )
    assert published_wait_bytes == raw_wait_bytes

    # 4. Prepare and publish known-reset claim
    sem_attempt = _build_valid_semantic_attempt()
    sem_attempt["task_id"] = admitted_request["task_id"]
    sem_attempt["fingerprint"] = admitted_request["semantic_task_fingerprint"]
    sem_attempt["run_prefix"] = request["run_prefix"]

    due_time = schedule["next_check_at_utc"]
    claimed_at = _step_iso_seconds(due_time, 10)
    claim_dt = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
    effective_now = claim_dt.timestamp() + 5.0

    cont_ctrl_run_id = "cycle-2026-20260926T200000Z-999"
    resume_request_id = "fedcba9876543210fedcba9876543210"
    if resume_request_id == request["request_id"]:
        resume_request_id = "0123456789abcdef0123456789abcdef"

    claim_prep = prepare_known_reset_resume_claim(
        wait_record=wait_record,
        expected_wait=expected_wait,
        semantic_attempt=sem_attempt,
        expected_task_id=admitted_request["task_id"],
        original_wait_sha256=wait_sha,
        continuation_controller_run_id=cont_ctrl_run_id,
        resume_request_id=resume_request_id,
        claimed_at_utc=claimed_at,
    )
    claim = claim_prep["claim"]
    expected_claim = claim_prep["expected"]
    claim_bytes = _canonical_bytes(claim)
    claim_sha = hashlib.sha256(claim_bytes).hexdigest()

    with exclusive_supervisor_lock(lock_file):
        published_claim = publish_known_reset_resume_claim(
            root,
            orig_ctrl_run_id,
            claim,
            expected_claim,
        )
    assert published_claim == claim

    # 5. Build continuation request matching receipt lineage and byte digests
    continuation_request = build_quota_resume_request(
        claim=claim,
        expected_claim=expected_claim,
        expected_claim_sha256=claim_sha,
        timeout=60,
        now_epoch=effective_now,
    )

    assert continuation_request["original_request_id"] == request["request_id"]
    assert continuation_request["original_controller_run_id"] == request["controller_run_id"]
    assert continuation_request["task_path"] == request["task_path"]
    assert continuation_request["task_sha256"] == request["task_sha256"]
    assert continuation_request["source_head"] == request["source_head"]
    assert continuation_request["assignment_id"] == request["assignment_id"]
    assert continuation_request["assignment_sha256"] == request["assignment_sha256"]
    assert continuation_request["original_wait_sha256"] == wait_sha
    assert continuation_request["claim_sha256"] == claim_sha

    # 6. Real admission validation with ONLY external Git/repo/intent/semantic/task seams patched
    fake_scope = _FakeScope(request["assignment_id"], request["assignment_sha256"])
    common_dict = {k: continuation_request[k] for k in continuation_request if k != "request_type"}

    with (
        patch("scripts.two_site_quota_continuation_admission.validate_request") as mock_val,
        patch("scripts.two_site_quota_continuation_admission.read_pending_rescope_intent") as mock_intent,
        patch("scripts.two_site_quota_continuation_admission.collect_initial_wait_source_expected") as mock_collect,
        patch("scripts.two_site_quota_continuation_admission.load_task") as mock_task,
        patch("scripts.two_site_quota_continuation_admission.read_existing_semantic_attempt") as mock_sem,
    ):
        mock_val.return_value = (common_dict, canonical_task_path, fake_scope)
        mock_intent.return_value = (rescope_epoch, rescope_attempt, repo)
        mock_collect.return_value = expected_wait
        mock_task.return_value = ({"task_id": admitted_request["task_id"], "goal": "hermetic goal"}, {})
        mock_sem.return_value = sem_attempt

        admitted_continuation, out_task_path, out_scope = validate_quota_continuation_request(
            root,
            continuation_request,
            now_epoch=effective_now,
        )

    assert admitted_continuation == continuation_request
    assert out_task_path == canonical_task_path
    assert out_scope == fake_scope

    # 7. Real receipt readback on admitted original_request_id, derivation, and evidence loading
    admitted_orig_req_id = admitted_continuation["original_request_id"]
    terminal_receipt_readback = read_terminal_quota_record(root, admitted_orig_req_id)
    assert terminal_receipt_readback == published_receipt

    derived_artifacts = derive_worker_resume_artifacts(admitted_request, terminal_receipt_readback)
    assert derived_artifacts["worker_index"] == 2
    assert derived_artifacts["correction_origin"] == "REVIEWER"
    assert derived_artifacts["final_worker_run_id"] == meta["w1_id"]
    assert derived_artifacts["final_reviewer_run_id"] == meta["r1_id"]
    assert derived_artifacts["proposal_sha256"] == meta["w1_sha"]
    assert derived_artifacts["proposal_path"] == f".local/two-site-refactor/proposals/{meta['w1_id']}/result.json"
    assert derived_artifacts["reviewer_path"] == f".local/two-site-refactor/proposals/{meta['r1_id']}/result.json"
    assert derived_artifacts["pre_review_test_path"] == f".local/two-site-refactor/pipeline/{meta['orig_prefix']}/pre-review-tests-1.json"
    assert derived_artifacts["expected_feedback_path"] == f".local/two-site-refactor/pipeline/{meta['orig_prefix']}/feedback-correction-2.json"
    assert derived_artifacts["task_id"] == admitted_request["task_id"]
    assert derived_artifacts["source_head"] == HEAD
    assert derived_artifacts["semantic_task_fingerprint"] == admitted_request["semantic_task_fingerprint"]
    assert derived_artifacts["attempted_run_id"] == orig_w2_id
    assert derived_artifacts["quota_evidence"] == terminal_receipt_readback["quota_evidence"]

    with _patch_load_task():
        with patch.object(evidence_mod, "load_task", side_effect=_fake_load_task):
            loaded_evidence = load_worker_correction_evidence(
                root, canonical_task_path, derived_artifacts
            )

    assert loaded_evidence["artifacts"]["worker_index"] == 2
    assert loaded_evidence["feedback_path"] == derived_artifacts["expected_feedback_path"]
    assert loaded_evidence["feedback"]["reviewer"] == "gemini"
    assert loaded_evidence["feedback"]["reviewed_proposal_sha256"] == meta["w1_sha"]
    assert loaded_evidence["receipt"]["proposal_sha256"] == meta["w1_sha"]
    assert loaded_evidence["worker_info"]["status"]["run_id"] == meta["w1_id"]
    assert loaded_evidence["worker_info"]["result_sha"] == meta["w1_sha"]
    assert loaded_evidence["reviewer_status"]["run_id"] == meta["r1_id"]
    assert loaded_evidence["reviewer_result"]["status"] == "CHANGES_REQUIRED"

    # 8. Explicit verification of digest bindings and file-backed artifacts
    assert wait_record["terminal_receipt_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()
    assert claim["original_wait_sha256"] == hashlib.sha256(raw_wait_bytes).hexdigest()
    assert continuation_request["original_wait_sha256"] == hashlib.sha256(raw_wait_bytes).hexdigest()
    assert continuation_request["claim_sha256"] == hashlib.sha256(claim_bytes).hexdigest()

    w1_proposal_file = root / derived_artifacts["proposal_path"]
    assert w1_proposal_file.is_file()
    assert hashlib.sha256(w1_proposal_file.read_bytes()).hexdigest() == meta["w1_sha"]

    r1_reviewer_file = root / derived_artifacts["reviewer_path"]
    assert r1_reviewer_file.is_file()

    receipt1_test_file = root / derived_artifacts["pre_review_test_path"]
    assert receipt1_test_file.is_file()

    feedback2_file = root / derived_artifacts["expected_feedback_path"]
    assert feedback2_file.is_file()
