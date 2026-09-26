"""Hermetic file-backed proof for Worker-2 terminal quota receipt, derivation, and evidence.

This test suite establishes honest file-backed verification that:
1. Real Broker publish and readback of an original Worker-2 terminal quota record succeeds
   with an exact status witness in .local/two-site-refactor/proposals/{orig_prefix}-worker-2.
2. Real Worker-2 resume artifact derivation and correction evidence loading match the
   materialized W1/R1/receipt1/feedback2 lineage.
3. Tampering the original Worker-2 quota status causes read_terminal_quota_record to fail closed
   before derivation or Provider invocation, while leaving prior W1/R1 status intact.

Scope limitation: Real admission/claim, Provider/sandbox, Broker/Supervisor live hook,
replay authority, and full C5d end-to-end integration remain unproven.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.two_site_provider_broker import (
        BrokerError,
        broker_paths,
        expected_role_model,
        publish_terminal_quota_record,
        read_terminal_quota_record,
    )
    from scripts.two_site_quota_resume_worker_artifacts import (
        derive_worker_resume_artifacts,
    )
    from scripts.two_site_quota_resume_worker_evidence import (
        load_worker_correction_evidence,
    )
    import scripts.two_site_quota_resume_worker_evidence as evidence_mod
except ImportError:
    from two_site_provider_broker import (  # type: ignore[no-redef]
        BrokerError,
        broker_paths,
        expected_role_model,
        publish_terminal_quota_record,
        read_terminal_quota_record,
    )
    from two_site_quota_resume_worker_artifacts import (  # type: ignore[no-redef]
        derive_worker_resume_artifacts,
    )
    from two_site_quota_resume_worker_evidence import (  # type: ignore[no-redef]
        load_worker_correction_evidence,
    )
    import two_site_quota_resume_worker_evidence as evidence_mod  # type: ignore[no-redef]

try:
    from tests.test_two_site_proposal_pipeline import HEAD
    from tests.test_two_site_provider_broker import (
        _make_quota_response,
        _write_runner_status,
    )
    from tests.test_two_site_quota_resume_worker_artifacts import (
        _build_worker_correction_fixtures,
    )
    from tests.test_two_site_quota_resume_worker_two_fileproof import (
        _setup_materialized_worker_two_env,
    )
    from tests.test_two_site_quota_resume_worker_two_history_materialized import (
        _fake_load_task,
        _patch_load_task,
    )
except ImportError:
    from test_two_site_proposal_pipeline import HEAD  # type: ignore[no-redef]
    from test_two_site_provider_broker import (  # type: ignore[no-redef]
        _make_quota_response,
        _write_runner_status,
    )
    from test_two_site_quota_resume_worker_artifacts import (  # type: ignore[no-redef]
        _build_worker_correction_fixtures,
    )
    from test_two_site_quota_resume_worker_two_fileproof import (  # type: ignore[no-redef]
        _setup_materialized_worker_two_env,
    )
    from test_two_site_quota_resume_worker_two_history_materialized import (  # type: ignore[no-redef]
        _fake_load_task,
        _patch_load_task,
    )


def _setup_materialized_worker_two_terminal_scenario(
    tmp_path: Path,
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any], Path, Path, dict[str, Any]]:
    root, ctx, returned_state, meta = _setup_materialized_worker_two_env(tmp_path)
    broker_paths(root)

    orig_prefix = meta["orig_prefix"]
    task_data = ctx["task"]
    task_id = task_data["task_id"]

    # Copy actual task bytes to canonical Broker path .local/two-site-refactor/tasks/...json
    canonical_task_rel = f".local/two-site-refactor/tasks/{task_id}.json"
    canonical_task_path = root / canonical_task_rel
    canonical_task_path.parent.mkdir(parents=True, exist_ok=True)
    task_bytes = (root / "task.json").read_bytes()
    canonical_task_path.write_bytes(task_bytes)
    task_sha = hashlib.sha256(task_bytes).hexdigest()

    # Reuse _build_worker_correction_fixtures(2) only for exact schema starting point
    admitted_request, sample_receipt, pipeline_state = _build_worker_correction_fixtures(2)

    # Reconcile Broker request with materialized lineage
    request = copy.deepcopy(sample_receipt["request"])
    request["run_prefix"] = orig_prefix
    request["source_head"] = HEAD
    request["task_path"] = canonical_task_rel
    request["task_sha256"] = task_sha
    request["created_at"] = time.time()

    # Reconcile admitted continuation request lineage
    admitted_request["task_id"] = task_id
    admitted_request["source_head"] = HEAD
    admitted_request["semantic_task_fingerprint"] = returned_state["semantic_task_fingerprint"]
    admitted_request["original_request_id"] = request["request_id"]
    admitted_request["original_controller_run_id"] = request["controller_run_id"]
    admitted_request["task_path"] = canonical_task_rel
    admitted_request["task_sha256"] = task_sha
    admitted_request["assignment_id"] = request["assignment_id"]
    admitted_request["assignment_sha256"] = request["assignment_sha256"]

    # Reconcile prior W1 / R1 / receipt1 / changed_paths with materialized files
    receipt1_path = root / ".local/two-site-refactor/pipeline" / orig_prefix / "pre-review-tests-1.json"
    receipt1_sha = hashlib.sha256(receipt1_path.read_bytes()).hexdigest()

    pipeline_state["task_id"] = task_id
    pipeline_state["source_head"] = HEAD
    pipeline_state["semantic_task_fingerprint"] = returned_state["semantic_task_fingerprint"]
    pipeline_state["correction_count"] = 1
    pipeline_state["final_worker_run_id"] = meta["w1_id"]
    pipeline_state["final_reviewer_run_id"] = meta["r1_id"]
    pipeline_state["proposal_sha256"] = meta["w1_sha"]
    pipeline_state["proposal_path"] = f".local/two-site-refactor/proposals/{meta['w1_id']}/result.json"
    pipeline_state["reviewer_path"] = f".local/two-site-refactor/proposals/{meta['r1_id']}/result.json"
    pipeline_state["pre_review_test_path"] = f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-1.json"
    pipeline_state["pre_review_test_sha256"] = receipt1_sha
    pipeline_state["pre_review_test_state"] = "PASSED"
    pipeline_state["changed_paths"] = ["src.py"]

    # Distinct attempted original {orig_prefix}-worker-2 quota status witness
    orig_w2_id = f"{orig_prefix}-worker-2"
    model = expected_role_model("worker", task_id)
    cid = f"conv-{orig_w2_id}"
    quota_evidence = {
        "kind": "QUOTA_RESET_KNOWN",
        "provider": "gemini",
        "model": model,
        "conversation_id": cid,
        "reset_at_utc": "2099-01-01T00:00:00Z",
    }
    orig_status_path, status_sha = _write_runner_status(
        root,
        orig_w2_id,
        role="worker",
        head=HEAD,
        model=model,
        task_id=task_id,
        quota_evidence=quota_evidence,
        conversation_id=cid,
        state="BLOCKED",
    )

    pipeline_state["quota_evidence"] = quota_evidence
    pipeline_state["quota_attempt"] = {
        "role": "worker",
        "run_id": orig_w2_id,
        "status_sha256": status_sha,
    }

    response = _make_quota_response(request, pipeline_state)
    return root, request, response, admitted_request, canonical_task_path, orig_status_path, meta


def test_materialized_worker_two_terminal_receipt_publish_read_and_derive(tmp_path: Path) -> None:
    (
        root,
        request,
        response,
        admitted_request,
        canonical_task_path,
        _orig_status_path,
        meta,
    ) = _setup_materialized_worker_two_terminal_scenario(tmp_path)

    # 1. Real Broker publication and validated readback
    published_receipt = publish_terminal_quota_record(root, request, response)
    read_receipt = read_terminal_quota_record(root, request["request_id"])

    assert read_receipt == published_receipt
    assert read_receipt["request"] == request
    assert read_receipt["response"] == response
    assert read_receipt["quota_evidence"] == published_receipt["quota_evidence"]

    # Verify original attempted W2 quota status is distinct from continuation W2 proposal
    orig_w2_id = f"{meta['orig_prefix']}-worker-2"
    assert orig_w2_id != meta["w2_id"]
    assert not (root / ".local/two-site-refactor/proposals" / orig_w2_id / "result.json").exists()
    assert (root / ".local/two-site-refactor/proposals" / meta["w2_id"] / "result.json").exists()

    # 2. Real Worker-2 artifact derivation
    derived_artifacts = derive_worker_resume_artifacts(admitted_request, read_receipt)

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
    assert derived_artifacts["quota_evidence"] == read_receipt["quota_evidence"]

    # 3. Real evidence loader with scoped load_task seam
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


def test_materialized_worker_two_terminal_receipt_rejects_tampered_original_status(
    tmp_path: Path,
) -> None:
    (
        root,
        request,
        response,
        _admitted_request,
        _canonical_task_path,
        orig_status_path,
        meta,
    ) = _setup_materialized_worker_two_terminal_scenario(tmp_path)

    # Publish valid record first
    publish_terminal_quota_record(root, request, response)

    # Record prior successful W1 and R1 status contents to verify they are unaltered
    w1_status_path = root / ".local/two-site-refactor/proposals" / meta["w1_id"] / "status.json"
    r1_status_path = root / ".local/two-site-refactor/proposals" / meta["r1_id"] / "status.json"
    w1_status_before = w1_status_path.read_bytes()
    r1_status_before = r1_status_path.read_bytes()

    # Tamper ONLY the original quota-attempt W2 status witness after publication
    tampered_status = json.loads(orig_status_path.read_text(encoding="utf-8"))
    tampered_status["drifted"] = True
    tampered_status["state"] = "CORRUPTED"
    orig_status_path.write_text(json.dumps(tampered_status), encoding="utf-8")

    # Real read_terminal_quota_record fails closed before derivation or Provider
    with pytest.raises(BrokerError, match="terminal quota response not detected"):
        read_terminal_quota_record(root, request["request_id"])

    # Prior successful W1/R1 status files remain completely unaltered
    assert w1_status_path.read_bytes() == w1_status_before
    assert r1_status_path.read_bytes() == r1_status_before
