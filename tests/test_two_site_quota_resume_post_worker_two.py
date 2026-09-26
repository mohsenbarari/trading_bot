"""Tests for post-result worker-2 binding context under pipeline lock."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
try:
    import scripts.two_site_quota_resume_post_worker_one as post_one_mod
except ImportError:
    import two_site_quota_resume_post_worker_one as post_one_mod  # type: ignore[no-redef]
import scripts.two_site_quota_resume_post_worker_two as post_mod
from scripts.two_site_quota_resume_post_worker_two import (
    _EXPECTED_BOUND_KEYS,
    worker_two_post_result_context,
)

_EXPECTED_SEVEN_KEYS = frozenset({
    "base_context",
    "worker_info",
    "returned_state",
    "current_worker",
    "seen_convs",
    "complete_history",
    "state_check",
})


def _setup_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    origin: str = "REVIEWER",
) -> dict:
    task_path = tmp_path / "task.json"
    task_path.write_text("{}", encoding="utf-8")

    run_id = "run-sample-worker-2"
    sha = "a" * 64
    artifacts = {
        "worker_index": 2,
        "semantic_task_fingerprint": "fp-123",
        "final_worker_run_id": "run-sample-worker-1",
        "proposal_sha256": "b" * 64,
        "original_run_prefix": "orig-pfx",
        "source_head": "0" * 40,
    }
    evidence = {
        "feedback_sha": "c" * 64,
        "worker_info": {
            "result": {"changes": [{"path": "file.py"}]},
            "status": {"conversation_id": "conv-1", "result_sha256": "b" * 64},
            "result_sha": "b" * 64,
            "result_text": "text1",
            "conversation_id": "conv-1",
        },
    }
    if origin != "REVIEWER":
        artifacts["correction_origin"] = origin

    status_bytes = None
    status_file = None
    if origin == "REVIEWER":
        reviewer_dir = tmp_path / "reviewer"
        reviewer_dir.mkdir(parents=True, exist_ok=True)
        status_file = reviewer_dir / "status.json"
        status_bytes = b'{"status": "ok"}'
        status_file.write_bytes(status_bytes)
        artifacts["reviewer_path"] = str(reviewer_dir / "result.json")

    ctx = {
        "continuation_run_prefix": "run-sample",
        "source_head": "0" * 40,
        "initial_refs": {"refs/heads/main": "0" * 40},
        "admitted_request": {
            "role": "worker",
            "original_request_id": "orig-req-123",
        },
        "admitted_task_path": task_path,
        "original_terminal_receipt": {"terminal_quota": "receipt-123"},
        "artifacts": artifacts,
        "evidence": evidence,
        "task": {"task_id": "task-123"},
        "sources": {},
    }

    returned_state = {
        "state": "WORKER_PROPOSAL_READY",
        "task_id": "task-123",
        "source_head": "0" * 40,
        "final_worker_run_id": run_id,
        "proposal_sha256": sha,
        "changed_paths": ["file.py"],
        "proposal_path": f".local/two-site-refactor/proposals/{run_id}/result.json",
        "correction_count": 2,
        "semantic_task_fingerprint": "fp-123",
    }

    real_bound = {
        "worker_info": {
            "result": {"changes": [{"path": "file.py"}]},
            "status": {"conversation_id": "conv-2", "result_sha256": sha},
            "result_sha": sha,
            "result_text": "text2",
            "conversation_id": "conv-2",
        },
        "returned_state": copy.deepcopy(returned_state),
        "current_worker": (run_id, sha, "text2", "conv-2"),
        "seen_convs": {"conv-1", "conv-2"},
        "complete_history": {
            "complete": True,
            "ordered_conversation_ids": ["conv-1", "conv-2"],
        },
    }

    request = {"continuation": True}
    continuation_state = {"open": False}

    @contextmanager
    def fake_worker_correction_continuation_context(root: Path, req: dict):
        continuation_state["open"] = True
        try:
            yield ctx
        finally:
            continuation_state["open"] = False

    monkeypatch.setattr(
        post_mod,
        "worker_correction_continuation_context",
        fake_worker_correction_continuation_context,
    )

    val_mock = MagicMock(
        return_value=(ctx["admitted_request"], ctx["admitted_task_path"], None)
    )
    monkeypatch.setattr(
        post_mod, "validate_quota_continuation_request", val_mock
    )

    receipt_mock = MagicMock(return_value=ctx["original_terminal_receipt"])
    monkeypatch.setattr(post_mod, "read_terminal_quota_record", receipt_mock)

    art_mock = MagicMock(return_value=ctx["artifacts"])
    monkeypatch.setattr(post_mod, "derive_worker_resume_artifacts", art_mock)

    ev_mock = MagicMock(return_value=ctx["evidence"])
    monkeypatch.setattr(post_mod, "load_worker_correction_evidence", ev_mock)

    file_mock = MagicMock(return_value=status_bytes)
    monkeypatch.setattr(post_one_mod, "bounded_file_bytes", file_mock)
    monkeypatch.setattr(post_mod, "bounded_file_bytes", file_mock)

    check_mock = MagicMock(return_value=None)
    monkeypatch.setattr(post_mod, "check_state", check_mock)

    read_bind_mock = MagicMock(return_value=copy.deepcopy(real_bound))
    monkeypatch.setattr(
        post_mod, "read_and_bind_worker_two_result", read_bind_mock
    )

    return {
        "ctx": ctx,
        "request": request,
        "returned_state": returned_state,
        "real_bound": real_bound,
        "continuation_state": continuation_state,
        "val_mock": val_mock,
        "receipt_mock": receipt_mock,
        "art_mock": art_mock,
        "ev_mock": ev_mock,
        "file_mock": file_mock,
        "check_mock": check_mock,
        "read_bind_mock": read_bind_mock,
        "status_file": status_file,
    }


def test_worker_two_post_result_context_success(tmp_path, monkeypatch):
    env = _setup_env(tmp_path, monkeypatch)
    returned_state = env["returned_state"]
    real_bound = env["real_bound"]
    state = env["continuation_state"]

    assert state["open"] is False

    with worker_two_post_result_context(
        tmp_path, env["request"], returned_state
    ) as yielded:
        assert state["open"] is True
        assert set(yielded.keys()) == _EXPECTED_SEVEN_KEYS
        assert set(real_bound.keys()) == _EXPECTED_BOUND_KEYS

        yielded["state_check"]()
        yielded["state_check"]()

        assert yielded["base_context"] is not env["ctx"]
        assert yielded["returned_state"] is not real_bound["returned_state"]
        assert yielded["seen_convs"] is not real_bound["seen_convs"]
        yielded["returned_state"]["mutated_key"] = "tampered"
        assert "mutated_key" not in real_bound["returned_state"]
        yielded["seen_convs"].add("invented-conv")
        assert "invented-conv" not in real_bound["seen_convs"]

        returned_state["external_mutation"] = "rogue"
        assert "external_mutation" not in yielded["returned_state"]
        yielded["state_check"]()

    assert state["open"] is False


@pytest.mark.parametrize(
    ("drift_target", "error_match"),
    [
        ("admission", "admitted request drifted across recheck"),
        ("receipt", "original terminal quota record drifted across recheck"),
        ("artifact", "worker resume artifacts drifted across recheck"),
        ("evidence", "correction evidence drifted across recheck"),
        ("reviewer_raw_bytes", "prior reviewer status bytes drifted across recheck"),
        ("check_state_error", "check_state failed"),
        ("bound_result", "worker-2 result binding drifted across recheck"),
    ],
)
def test_worker_two_post_result_drift_fail_closed(
    tmp_path, monkeypatch, drift_target, error_match
):
    env = _setup_env(tmp_path, monkeypatch)
    with worker_two_post_result_context(
        tmp_path, env["request"], env["returned_state"]
    ) as yielded:
        if drift_target == "admission":
            drifted_req = dict(env["ctx"]["admitted_request"])
            drifted_req["original_request_id"] = "different-req-id"
            env["val_mock"].return_value = (
                drifted_req,
                env["ctx"]["admitted_task_path"],
                None,
            )
        elif drift_target == "receipt":
            env["receipt_mock"].return_value = {"terminal_quota": "drifted"}
        elif drift_target == "artifact":
            drifted_art = dict(env["ctx"]["artifacts"])
            drifted_art["semantic_task_fingerprint"] = "drifted-fp"
            env["art_mock"].return_value = drifted_art
        elif drift_target == "evidence":
            drifted_ev = dict(env["ctx"]["evidence"])
            drifted_ev["feedback_sha"] = "1" * 64
            env["ev_mock"].return_value = drifted_ev
        elif drift_target == "reviewer_raw_bytes":
            env["file_mock"].return_value = b'{"status": "tampered"}'
            if env.get("status_file") is not None:
                env["status_file"].write_bytes(b'{"status": "tampered"}')
        elif drift_target == "check_state_error":
            env["check_mock"].side_effect = ProposalError("check_state failed")
        elif drift_target == "bound_result":
            drifted_bound = dict(env["real_bound"])
            drifted_bound["seen_convs"] = set()
            env["read_bind_mock"].return_value = drifted_bound

        with pytest.raises(ProposalError, match=error_match):
            yielded["state_check"]()


def test_wrong_worker_index_rejected_before_yield(tmp_path, monkeypatch):
    env = _setup_env(tmp_path, monkeypatch)
    env["ctx"]["artifacts"]["worker_index"] = 1
    with pytest.raises(ProposalError, match="worker_index must be 2, got 1"):
        with worker_two_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass


@pytest.mark.parametrize(
    ("mutate_fn", "error_match"),
    [
        (
            lambda b: b.pop("complete_history"),
            "bound keys mismatch",
        ),
        (
            lambda b: b.__setitem__("seen_convs", list(b["seen_convs"])),
            "seen_convs must be a set",
        ),
        (
            lambda b: b.__setitem__("current_worker", ("only", "3", "items")),
            "current_worker must be a 4-tuple",
        ),
        (
            lambda b: b.__setitem__("complete_history", {"complete": False}),
            "worker-2 history must be complete",
        ),
    ],
)
def test_malformed_five_key_binding_rejected_before_yield(
    tmp_path, monkeypatch, mutate_fn, error_match
):
    env = _setup_env(tmp_path, monkeypatch)
    bad_bound = copy.deepcopy(env["real_bound"])
    mutate_fn(bad_bound)
    env["read_bind_mock"].return_value = bad_bound

    with pytest.raises(ProposalError, match=error_match):
        with worker_two_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass


def test_test_gate_rejects_mixed_origin_at_entry(tmp_path, monkeypatch):
    env = _setup_env(tmp_path, monkeypatch, origin="BOOTSTRAP")
    env["ctx"]["evidence"]["correction_origin"] = "TEST_FAILED"
    with pytest.raises(
        ProposalError, match="mixed correction_origin between artifacts and evidence"
    ):
        with worker_two_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass

    env["file_mock"].assert_not_called()


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
def test_worker_two_post_result_context_test_gate_success(
    tmp_path, monkeypatch, origin
):
    env = _setup_env(tmp_path, monkeypatch, origin=origin)
    returned_state = env["returned_state"]
    real_bound = env["real_bound"]
    state = env["continuation_state"]

    assert state["open"] is False

    with worker_two_post_result_context(
        tmp_path, env["request"], returned_state
    ) as yielded:
        assert state["open"] is True
        assert set(yielded.keys()) == _EXPECTED_SEVEN_KEYS
        assert set(real_bound.keys()) == _EXPECTED_BOUND_KEYS
        assert yielded["complete_history"] == real_bound["complete_history"]

        env["file_mock"].assert_not_called()
        yielded["state_check"]()
        env["file_mock"].assert_not_called()

        assert yielded["base_context"] is not env["ctx"]
        assert yielded["returned_state"] is not real_bound["returned_state"]
        assert yielded["seen_convs"] is not real_bound["seen_convs"]

    assert state["open"] is False


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
@pytest.mark.parametrize(
    ("fake_field", "fake_value", "error_match"),
    [
        (
            "reviewer_path",
            "/tmp/reviewer/result.json",
            "artifacts reviewer_path must be None",
        ),
        (
            "reviewer_status",
            {"conversation_id": "conv-rev"},
            "evidence reviewer_status must be None",
        ),
        (
            "reviewer_sha",
            "0" * 64,
            "evidence reviewer_sha must be None",
        ),
        (
            "reviewer_sha256",
            "0" * 64,
            "evidence reviewer sha must be None",
        ),
    ],
)
def test_test_gate_rejects_fake_reviewer_at_entry(
    tmp_path, monkeypatch, origin, fake_field, fake_value, error_match
):
    env = _setup_env(tmp_path, monkeypatch, origin=origin)
    if fake_field == "reviewer_path":
        env["ctx"]["artifacts"][fake_field] = fake_value
    else:
        env["ctx"]["evidence"][fake_field] = fake_value

    with pytest.raises(ProposalError, match=error_match):
        with worker_two_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass

    env["file_mock"].assert_not_called()


@pytest.mark.parametrize("origin", ["BOOTSTRAP", "TEST_FAILED"])
@pytest.mark.parametrize(
    ("drift_target", "error_match"),
    [
        ("origin", "correction origin drifted across recheck"),
        ("evidence", "correction evidence drifted across recheck"),
        ("bound_result", "worker-2 result binding drifted across recheck"),
        ("check_state_error", "check_state failed"),
    ],
)
def test_worker_two_test_gate_drift_fail_closed(
    tmp_path, monkeypatch, origin, drift_target, error_match
):
    env = _setup_env(tmp_path, monkeypatch, origin=origin)
    with worker_two_post_result_context(
        tmp_path, env["request"], env["returned_state"]
    ) as yielded:
        if drift_target == "origin":
            drifted_origin = "TEST_FAILED" if origin == "BOOTSTRAP" else "BOOTSTRAP"
            drifted_art = dict(env["ctx"]["artifacts"], correction_origin=drifted_origin)
            env["ctx"]["artifacts"] = drifted_art
            env["art_mock"].return_value = drifted_art
        elif drift_target == "evidence":
            drifted_ev = dict(env["ctx"]["evidence"])
            drifted_ev["feedback"] = {"reviewer": "test-gate", "findings": ["drifted"]}
            env["ev_mock"].return_value = drifted_ev
        elif drift_target == "bound_result":
            drifted_bound = dict(env["real_bound"], seen_convs=set())
            env["read_bind_mock"].return_value = drifted_bound
        elif drift_target == "check_state_error":
            env["check_mock"].side_effect = ProposalError("check_state failed")

        with pytest.raises(ProposalError, match=error_match):
            yielded["state_check"]()

        env["file_mock"].assert_not_called()
