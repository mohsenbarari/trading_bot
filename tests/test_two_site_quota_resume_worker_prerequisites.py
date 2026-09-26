from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch
import pytest

try:
    from scripts.two_site_proposal_pipeline import ProposalError
    import scripts.two_site_quota_resume_worker_prerequisites as target_module
    from scripts.two_site_quota_resume_worker_prerequisites import (
        load_prior_worker_and_receipt,
    )
except ImportError:
    from two_site_proposal_pipeline import ProposalError  # type: ignore[no-redef]
    import two_site_quota_resume_worker_prerequisites as target_module  # type: ignore[no-redef]
    from two_site_quota_resume_worker_prerequisites import (  # type: ignore[no-redef]
        load_prior_worker_and_receipt,
    )


def _make_real_artifacts(worker_index: Any = 1, **overrides: Any) -> dict[str, Any]:
    orig_prefix = "run-20260925-001"
    prior_idx = (
        worker_index - 1
        if isinstance(worker_index, int) and not isinstance(worker_index, bool)
        else 0
    )
    data: dict[str, Any] = {
        "task_id": "AE03-TASK-260",
        "semantic_task_fingerprint": "a" * 64,
        "source_head": "924c24269cc5d2e9f88ffcd01bdfb49948864086",
        "original_run_prefix": orig_prefix,
        "attempted_run_id": f"{orig_prefix}-worker-{worker_index}",
        "status_sha256": "b" * 64,
        "status_sha": "b" * 64,
        "quota_evidence": {
            "kind": "quota",
            "provider": "google",
            "model": "gemini",
            "conversation_id": "conv-1",
            "reset_at_utc": "2026-09-25T18:00:00Z",
        },
        "worker_index": worker_index,
        "final_worker_run_id": f"{orig_prefix}-worker-{prior_idx}",
        "final_reviewer_run_id": f"{orig_prefix}-reviewer-{prior_idx}",
        "proposal_path": f".local/two-site-refactor/proposals/{orig_prefix}-worker-{prior_idx}/result.json",
        "proposal_sha256": "1" * 64,
        "reviewer_path": f".local/two-site-refactor/proposals/{orig_prefix}-reviewer-{prior_idx}/result.json",
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-{prior_idx}.json",
        "pre_review_test_sha256": "2" * 64,
        "pre_review_test_state": "PASSED",
        "changed_paths": ["scripts/two_site_proposal_pipeline.py"],
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{worker_index}.json",
    }
    data.update(overrides)
    return data


def test_positive_worker_index_1(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    task_path = "tasks/task.json"
    artifacts = _make_real_artifacts(worker_index=1)
    expected_sha = artifacts["proposal_sha256"]

    worker_val = {"result_sha": expected_sha, "status": {"state": "DONE"}}
    receipt_val = {"state": "PASSED", "proposal_sha256": expected_sha}

    with patch.object(
        target_module,
        "load_prior_worker_chain",
        return_value=worker_val,
    ) as mock_chain, patch.object(
        target_module,
        "_load_verifier_pre_review_receipt",
        return_value=receipt_val,
    ) as mock_receipt:
        result = load_prior_worker_and_receipt(root, task_path, artifacts)

    assert mock_chain.call_count == 1
    c_root, c_task, c_artifacts = mock_chain.call_args[0]
    assert c_root == root
    assert c_task == task_path
    assert c_artifacts["correction_count"] == 0
    assert "correction_count" not in artifacts

    assert mock_receipt.call_count == 1
    r_root, r_artifacts = mock_receipt.call_args[0]
    assert r_root == root
    assert r_artifacts["correction_count"] == 0

    assert result["prior_index"] == 0
    assert result["worker_info"] == worker_val
    assert result["worker_info"] is not worker_val
    assert result["receipt"] == receipt_val
    assert result["receipt"] is not receipt_val


def test_positive_worker_index_2(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    task_path = Path("tasks/task.json")
    artifacts = _make_real_artifacts(worker_index=2)
    expected_sha = artifacts["proposal_sha256"]

    worker_val = {"result_sha": expected_sha, "status": {"state": "DONE"}}
    receipt_val = {"state": "PASSED", "proposal_sha256": expected_sha}

    with patch.object(
        target_module,
        "load_prior_worker_chain",
        return_value=worker_val,
    ) as mock_chain, patch.object(
        target_module,
        "_load_verifier_pre_review_receipt",
        return_value=receipt_val,
    ) as mock_receipt:
        result = load_prior_worker_and_receipt(root, task_path, artifacts)

    assert mock_chain.call_args[0][2]["correction_count"] == 1
    assert mock_receipt.call_args[0][1]["correction_count"] == 1
    assert result["prior_index"] == 1
    assert result["worker_info"] == worker_val
    assert result["receipt"] == receipt_val


@pytest.mark.parametrize("bad_index", [True, False, 1.0, 2.0, 0, 3, -1, None, "1"])
def test_negative_worker_indices(tmp_path: Path, bad_index: Any) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=bad_index)
    with pytest.raises(ProposalError, match="artifacts worker_index must be exact int 1 or 2"):
        load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


@pytest.mark.parametrize("bad_sha", [None, "", "abc", "1" * 63, "G" * 64, 12345])
def test_negative_proposal_sha(tmp_path: Path, bad_sha: Any) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=1, proposal_sha256=bad_sha)
    with pytest.raises(ProposalError, match="artifacts proposal_sha256 must be 64 lowercase hex"):
        load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


def test_negative_wrong_worker_sha(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=1)
    bad_worker = {"result_sha": "f" * 64}

    with patch.object(target_module, "load_prior_worker_chain", return_value=bad_worker):
        with pytest.raises(ProposalError, match="worker_info.result_sha mismatch"):
            load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


def test_negative_failed_receipt(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=1)
    expected_sha = artifacts["proposal_sha256"]
    worker_val = {"result_sha": expected_sha}
    failed_receipt = {"state": "FAILED", "proposal_sha256": expected_sha}

    with patch.object(
        target_module, "load_prior_worker_chain", return_value=worker_val
    ), patch.object(
        target_module, "_load_verifier_pre_review_receipt", return_value=failed_receipt
    ):
        with pytest.raises(ProposalError, match="validated receipt state must be 'PASSED'"):
            load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


def test_negative_wrong_proposal_receipt(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=1)
    expected_sha = artifacts["proposal_sha256"]
    worker_val = {"result_sha": expected_sha}
    wrong_sha_receipt = {"state": "PASSED", "proposal_sha256": "9" * 64}

    with patch.object(
        target_module, "load_prior_worker_chain", return_value=worker_val
    ), patch.object(
        target_module, "_load_verifier_pre_review_receipt", return_value=wrong_sha_receipt
    ):
        with pytest.raises(ProposalError, match="validated receipt proposal_sha256 mismatch"):
            load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


def test_input_immutability(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    artifacts = _make_real_artifacts(worker_index=1)
    snapshot = copy.deepcopy(artifacts)
    expected_sha = artifacts["proposal_sha256"]

    worker_val = {"result_sha": expected_sha}
    receipt_val = {"state": "PASSED", "proposal_sha256": expected_sha}

    with patch.object(
        target_module, "load_prior_worker_chain", return_value=worker_val
    ), patch.object(
        target_module, "_load_verifier_pre_review_receipt", return_value=receipt_val
    ):
        load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)

    assert artifacts == snapshot
    assert "correction_count" not in artifacts


def test_root_validation(tmp_path: Path) -> None:
    artifacts = _make_real_artifacts(worker_index=1)
    with pytest.raises(ProposalError, match="root must be a Path"):
        load_prior_worker_and_receipt(str(tmp_path), "tasks/task.json", artifacts)  # type: ignore[arg-type]

    non_existent = tmp_path / "missing_dir"
    with pytest.raises(ProposalError, match="project root does not exist"):
        load_prior_worker_and_receipt(non_existent, "tasks/task.json", artifacts)

    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    with pytest.raises(ProposalError, match="project root must be a directory"):
        load_prior_worker_and_receipt(file_path, "tasks/task.json", artifacts)


def _make_bootstrap_artifacts(worker_index: int = 1, **overrides: Any) -> dict[str, Any]:
    orig_prefix = "run-20260925-001"
    prior_idx = (
        worker_index - 1
        if isinstance(worker_index, int) and not isinstance(worker_index, bool)
        else 0
    )
    data: dict[str, Any] = {
        "task_id": "AE03-TASK-260",
        "semantic_task_fingerprint": "a" * 64,
        "source_head": "924c24269cc5d2e9f88ffcd01bdfb49948864086",
        "original_run_prefix": orig_prefix,
        "attempted_run_id": f"{orig_prefix}-worker-{worker_index}",
        "status_sha256": "b" * 64,
        "status_sha": "b" * 64,
        "quota_evidence": {
            "kind": "quota",
            "provider": "google",
            "model": "gemini",
            "conversation_id": "conv-1",
            "reset_at_utc": "2026-09-25T18:00:00Z",
        },
        "worker_index": worker_index,
        "correction_origin": "BOOTSTRAP",
        "final_worker_run_id": f"{orig_prefix}-worker-{prior_idx}",
        "final_reviewer_run_id": None,
        "proposal_path": f".local/two-site-refactor/proposals/{orig_prefix}-worker-{prior_idx}/result.json",
        "proposal_sha256": "1" * 64,
        "reviewer_path": None,
        "pre_review_test_path": None,
        "pre_review_test_sha256": None,
        "pre_review_test_state": None,
        "changed_paths": ["scripts/two_site_proposal_pipeline.py"],
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{worker_index}.json",
    }
    data.update(overrides)
    return data


def _make_failed_receipt(proposal_sha: str = "1" * 64, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "state": "FAILED",
        "proposal_sha256": proposal_sha,
        "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [],
        "returncode": 1,
        "stdout_tail": "FAILED test_sample",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }
    data.update(overrides)
    return data


def _make_test_failed_artifacts(
    worker_index: int = 1, receipt_sha: str = "3" * 64, **overrides: Any
) -> dict[str, Any]:
    orig_prefix = "run-20260925-001"
    prior_idx = (
        worker_index - 1
        if isinstance(worker_index, int) and not isinstance(worker_index, bool)
        else 0
    )
    data: dict[str, Any] = {
        "task_id": "AE03-TASK-260",
        "semantic_task_fingerprint": "a" * 64,
        "source_head": "924c24269cc5d2e9f88ffcd01bdfb49948864086",
        "original_run_prefix": orig_prefix,
        "attempted_run_id": f"{orig_prefix}-worker-{worker_index}",
        "status_sha256": "b" * 64,
        "status_sha": "b" * 64,
        "quota_evidence": {
            "kind": "quota",
            "provider": "google",
            "model": "gemini",
            "conversation_id": "conv-1",
            "reset_at_utc": "2026-09-25T18:00:00Z",
        },
        "worker_index": worker_index,
        "correction_origin": "TEST_FAILED",
        "final_worker_run_id": f"{orig_prefix}-worker-{prior_idx}",
        "final_reviewer_run_id": None,
        "proposal_path": f".local/two-site-refactor/proposals/{orig_prefix}-worker-{prior_idx}/result.json",
        "proposal_sha256": "1" * 64,
        "reviewer_path": None,
        "pre_review_test_path": f".local/two-site-refactor/pipeline/{orig_prefix}/pre-review-tests-{prior_idx}.json",
        "pre_review_test_sha256": receipt_sha,
        "pre_review_test_state": "FAILED",
        "changed_paths": ["scripts/two_site_proposal_pipeline.py"],
        "expected_feedback_path": f".local/two-site-refactor/pipeline/{orig_prefix}/feedback-correction-{worker_index}.json",
    }
    data.update(overrides)
    return data


def _write_receipt_file(root: Path, rel_path: str, data: dict[str, Any]) -> tuple[bytes, str]:
    full_path = root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    full_path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    return raw, sha


@pytest.mark.parametrize("worker_index", [1, 2])
def test_bootstrap_positive_worker_indices(tmp_path: Path, worker_index: int) -> None:
    root = tmp_path.resolve()
    task_path = "tasks/task.json"
    artifacts = _make_bootstrap_artifacts(worker_index=worker_index)
    expected_sha = artifacts["proposal_sha256"]
    worker_val = {"result_sha": expected_sha, "status": {"state": "DONE"}}

    with patch.object(
        target_module,
        "load_prior_worker_chain",
        return_value=worker_val,
    ) as mock_chain, patch.object(
        target_module,
        "_load_verifier_pre_review_receipt",
    ) as mock_receipt:
        result = load_prior_worker_and_receipt(root, task_path, artifacts)

    assert mock_chain.call_count == 1
    assert mock_receipt.call_count == 0
    assert result["receipt"] is None
    assert result["prior_index"] == worker_index - 1
    assert result["worker_info"] == worker_val
    assert result["worker_info"] is not worker_val


@pytest.mark.parametrize(
    "bad_key,bad_val",
    [
        ("final_reviewer_run_id", "run-20260925-001-reviewer-0"),
        ("reviewer_path", ".local/some/path.json"),
        ("pre_review_test_path", ".local/some/tests.json"),
        ("pre_review_test_sha256", "2" * 64),
        ("pre_review_test_state", "FAILED"),
        ("proposal_path", ".local/wrong/proposal.json"),
        ("expected_feedback_path", ".local/wrong/feedback.json"),
    ],
)
def test_bootstrap_negative_fields(tmp_path: Path, bad_key: str, bad_val: Any) -> None:
    root = tmp_path.resolve()
    artifacts = _make_bootstrap_artifacts(worker_index=1, **{bad_key: bad_val})
    with pytest.raises(ProposalError):
        load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


@pytest.mark.parametrize("worker_index", [1, 2])
def test_test_failed_positive_worker_indices(tmp_path: Path, worker_index: int) -> None:
    root = tmp_path.resolve()
    task_path = "tasks/task.json"
    proposal_sha = "1" * 64
    receipt_data = _make_failed_receipt(proposal_sha=proposal_sha)
    prior_idx = worker_index - 1
    receipt_rel = f".local/two-site-refactor/pipeline/run-20260925-001/pre-review-tests-{prior_idx}.json"
    _, receipt_sha = _write_receipt_file(root, receipt_rel, receipt_data)

    artifacts = _make_test_failed_artifacts(
        worker_index=worker_index, receipt_sha=receipt_sha
    )
    worker_val = {"result_sha": proposal_sha, "status": {"state": "DONE"}}

    with patch.object(
        target_module,
        "load_prior_worker_chain",
        return_value=worker_val,
    ) as mock_chain, patch.object(
        target_module,
        "_load_verifier_pre_review_receipt",
    ) as mock_receipt:
        result = load_prior_worker_and_receipt(root, task_path, artifacts)

    assert mock_chain.call_count == 1
    assert mock_receipt.call_count == 0
    assert result["prior_index"] == prior_idx
    assert result["worker_info"] == worker_val
    assert result["worker_info"] is not worker_val
    assert result["receipt"]["state"] == "FAILED"
    assert result["receipt"]["proposal_sha256"] == proposal_sha
    assert result["receipt"]["returncode"] == 1
    assert result["receipt"] is not receipt_data


def test_test_failed_negative_tampered_and_missing_receipt(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    receipt_data = _make_failed_receipt()
    receipt_rel = ".local/two-site-refactor/pipeline/run-20260925-001/pre-review-tests-0.json"
    _, receipt_sha = _write_receipt_file(root, receipt_rel, receipt_data)

    artifacts = _make_test_failed_artifacts(worker_index=1, receipt_sha=receipt_sha)
    worker_val = {"result_sha": artifacts["proposal_sha256"]}

    (root / receipt_rel).write_bytes(b'{"tampered": true}')
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="pre-review test receipt SHA mismatch"):
            load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)

    (root / receipt_rel).unlink()
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="does not exist"):
            load_prior_worker_and_receipt(root, "tasks/task.json", artifacts)


def test_test_failed_negative_malformed_and_duplicate_json(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    receipt_rel = ".local/two-site-refactor/pipeline/run-20260925-001/pre-review-tests-0.json"
    full_path = root / receipt_rel
    full_path.parent.mkdir(parents=True, exist_ok=True)

    bad_bytes = b"{not json}"
    full_path.write_bytes(bad_bytes)
    bad_sha = hashlib.sha256(bad_bytes).hexdigest()
    art = _make_test_failed_artifacts(worker_index=1, receipt_sha=bad_sha)
    worker_val = {"result_sha": art["proposal_sha256"]}
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="failed to parse pre-review test receipt JSON"):
            load_prior_worker_and_receipt(root, "tasks/task.json", art)

    dup_bytes = b'{"state":"FAILED","state":"FAILED"}'
    full_path.write_bytes(dup_bytes)
    dup_sha = hashlib.sha256(dup_bytes).hexdigest()
    art_dup = _make_test_failed_artifacts(worker_index=1, receipt_sha=dup_sha)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="failed to parse pre-review test receipt JSON"):
            load_prior_worker_and_receipt(root, "tasks/task.json", art_dup)


def test_test_failed_negative_fake_passed_and_schema_violations(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    task_path = "tasks/task.json"
    proposal_sha = "1" * 64
    receipt_rel = ".local/two-site-refactor/pipeline/run-20260925-001/pre-review-tests-0.json"
    worker_val = {"result_sha": proposal_sha}

    fake_passed = {
        "state": "PASSED",
        "proposal_sha256": proposal_sha,
        "selectors": ["tests/test_sample.py"],
        "unsupported_specs": [],
        "returncode": 0,
        "stdout_tail": "ok",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }
    _, passed_sha = _write_receipt_file(root, receipt_rel, fake_passed)
    art_passed = _make_test_failed_artifacts(worker_index=1, receipt_sha=passed_sha)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="validated receipt state must be 'FAILED'"):
            load_prior_worker_and_receipt(root, task_path, art_passed)

    incomplete = {"state": "FAILED", "proposal_sha256": proposal_sha}
    _, inc_sha = _write_receipt_file(root, receipt_rel, incomplete)
    art_inc = _make_test_failed_artifacts(worker_index=1, receipt_sha=inc_sha)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="pre-review test receipt validation failed"):
            load_prior_worker_and_receipt(root, task_path, art_inc)

    wrong_sha_rec = _make_failed_receipt(proposal_sha="9" * 64)
    _, wrong_sha = _write_receipt_file(root, receipt_rel, wrong_sha_rec)
    art_wrong = _make_test_failed_artifacts(worker_index=1, receipt_sha=wrong_sha)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="pre-review test receipt validation failed"):
            load_prior_worker_and_receipt(root, task_path, art_wrong)


def test_test_failed_negative_symlink_and_hardlink(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    receipt_data = _make_failed_receipt()
    real_file = root / "real_receipt.json"
    raw = json.dumps(receipt_data).encode("utf-8")
    real_file.write_bytes(raw)
    receipt_sha = hashlib.sha256(raw).hexdigest()

    receipt_rel = ".local/two-site-refactor/pipeline/run-20260925-001/pre-review-tests-0.json"
    target_path = root / receipt_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    art = _make_test_failed_artifacts(worker_index=1, receipt_sha=receipt_sha)
    worker_val = {"result_sha": art["proposal_sha256"]}

    os.symlink(real_file, target_path)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="symlink"):
            load_prior_worker_and_receipt(root, "tasks/task.json", art)

    target_path.unlink()
    os.link(real_file, target_path)
    with patch.object(target_module, "load_prior_worker_chain", return_value=worker_val):
        with pytest.raises(ProposalError, match="hardlink"):
            load_prior_worker_and_receipt(root, "tasks/task.json", art)


def test_negative_mixed_origins_and_unknown_origin(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    art_unk = _make_real_artifacts(worker_index=1, correction_origin="UNKNOWN")
    with pytest.raises(ProposalError, match="correction_origin invalid"):
        load_prior_worker_and_receipt(root, "tasks/task.json", art_unk)

    art_rev_none = _make_real_artifacts(
        worker_index=1, correction_origin="REVIEWER", final_reviewer_run_id=None
    )
    with pytest.raises(ProposalError, match="final_reviewer_run_id cannot be None"):
        load_prior_worker_and_receipt(root, "tasks/task.json", art_rev_none)

    art_failed_rev = _make_test_failed_artifacts(
        worker_index=1, final_reviewer_run_id="run-20260925-001-reviewer-0"
    )
    with pytest.raises(ProposalError, match="final_reviewer_run_id must be None"):
        load_prior_worker_and_receipt(root, "tasks/task.json", art_failed_rev)
