"""Tests for post-result worker-1 binding context under pipeline lock."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from scripts.two_site_proposal_pipeline import ProposalError
import scripts.two_site_quota_resume_post_worker_one as post_mod
from scripts.two_site_quota_resume_post_worker_one import (
    _EXPECTED_BOUND_KEYS,
    worker_one_post_result_context,
)
from scripts.two_site_quota_resume_worker_one_result_contract import (
    bind_worker_one_result,
)
from tests.test_two_site_quota_resume_worker_one_result_contract import (
    _make_valid_bundle,
)

_EXPECTED_NINE_KEYS = frozenset({
    "base_context",
    "worker_info",
    "returned_state",
    "current_worker",
    "seen_convs",
    "seen_norm_findings",
    "seen_test_signatures",
    "seen_bootstrap_signatures",
    "state_check",
})


def _setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    ctx, returned_state, validated = _make_valid_bundle()
    real_bound = bind_worker_one_result(ctx, returned_state, validated)

    reviewer_dir = tmp_path / "reviewer"
    reviewer_dir.mkdir(parents=True, exist_ok=True)
    status_file = reviewer_dir / "status.json"
    status_bytes = b'{"status": "ok"}'
    status_file.write_bytes(status_bytes)

    task_path = tmp_path / "task.json"
    task_path.write_text("{}", encoding="utf-8")

    ctx["admitted_request"] = {
        "role": "worker",
        "original_request_id": "orig-req-123",
    }
    ctx["admitted_task_path"] = task_path
    ctx["original_terminal_receipt"] = {"terminal_quota": "receipt-123"}
    ctx["initial_refs"] = {
        "refs/heads/main": "0123456789abcdef0123456789abcdef01234567"
    }
    ctx["artifacts"]["reviewer_path"] = str(reviewer_dir / "result.json")
    ctx["artifacts"]["proposal_sha256"] = returned_state["proposal_sha256"]
    ctx["evidence"]["feedback_sha"] = "0" * 64
    ctx["sources"] = {}

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
    monkeypatch.setattr(post_mod, "bounded_file_bytes", file_mock)

    check_mock = MagicMock(return_value=None)
    monkeypatch.setattr(post_mod, "check_state", check_mock)

    read_bind_mock = MagicMock(return_value=copy.deepcopy(real_bound))
    monkeypatch.setattr(
        post_mod, "read_and_bind_worker_one_result", read_bind_mock
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
    }


def test_worker_one_post_result_context_success(tmp_path, monkeypatch):
    env = _setup_env(tmp_path, monkeypatch)
    returned_state = env["returned_state"]
    real_bound = env["real_bound"]
    state = env["continuation_state"]

    assert state["open"] is False

    with worker_one_post_result_context(
        tmp_path, env["request"], returned_state
    ) as yielded:
        assert state["open"] is True
        assert set(yielded.keys()) == _EXPECTED_NINE_KEYS
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
        ("bound_result", "worker-1 result binding drifted across recheck"),
    ],
)
def test_worker_one_post_result_drift_fail_closed(
    tmp_path, monkeypatch, drift_target, error_match
):
    env = _setup_env(tmp_path, monkeypatch)
    with worker_one_post_result_context(
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
    env["ctx"]["artifacts"]["worker_index"] = 2
    with pytest.raises(ProposalError, match="worker_index must be 1, got 2"):
        with worker_one_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass


@pytest.mark.parametrize(
    ("mutate_fn", "error_match"),
    [
        (
            lambda b: b.pop("seen_convs"),
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
    ],
)
def test_malformed_seven_key_binding_rejected_before_yield(
    tmp_path, monkeypatch, mutate_fn, error_match
):
    env = _setup_env(tmp_path, monkeypatch)
    bad_bound = copy.deepcopy(env["real_bound"])
    mutate_fn(bad_bound)
    env["read_bind_mock"].return_value = bad_bound

    with pytest.raises(ProposalError, match=error_match):
        with worker_one_post_result_context(
            tmp_path, env["request"], env["returned_state"]
        ):
            pass
