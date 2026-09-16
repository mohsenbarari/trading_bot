"""Hermetic test contracts for controller_run_id provider metrics."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import two_site_provider_metrics as metrics

HEAD = "a" * 40
TOKENS = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "thinking_tokens": 3}
CTRL = "ctrl-run-1"


def _task_payload(task_id: str = "task", head: str = HEAD, gap: str = "docs/ref") -> dict:
    goal = f"Scope description\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" + json.dumps({"gap_reference": gap})
    return {
        "source_head": head,
        "task": {
            "task_id": task_id,
            "goal": goal,
            "requirements": ["r1", "r2"],
            "test_selectors": ["pytest -q tests/test_two_site_provider_metrics.py"],
        },
    }


def _write_task(path: Path, task_id: str = "task", head: str = HEAD, gap: str = "docs/ref") -> Path:
    task_file = path / "task.json"
    task_file.write_text(json.dumps(_task_payload(task_id, head, gap)))
    return task_file


def _create_attempt(
    root: Path,
    name: str,
    controller_run_id: str = CTRL,
    task_id: str = "task",
    head: str = HEAD,
    role: str = "worker",
    state: str = "PROPOSAL_READY",
    usage: dict | None = TOKENS,
    duration_seconds: float | None = 5.0,
    with_task: bool = True,
    **extra_status,
) -> Path:
    path = root / ".local/two-site-refactor/proposals" / name
    path.mkdir(parents=True, exist_ok=True)
    status = {
        "controller_run_id": controller_run_id,
        "run_id": name,
        "task_id": task_id,
        "head": head,
        "role": role,
        "state": state,
        "usage": usage,
        "duration_seconds": duration_seconds,
    }
    status.update(extra_status)
    (path / "status.json").write_text(json.dumps(status))
    if with_task:
        _write_task(path, task_id=task_id, head=head)
    return path


def test_normal_initial_worker_reviewer_pair_first_pass_true(tmp_path):
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state="PROPOSAL_READY")
    _create_attempt(
        tmp_path,
        "crun-reviewer-0",
        role="verifier",
        state="VERIFIED",
        input_worker_run_id="crun-worker-0",
    )
    task_obj = _task_payload("task", HEAD, "docs/ref")["task"]
    expected_task_sha = hashlib.sha256(json.dumps(task_obj, sort_keys=True).encode("utf-8")).hexdigest()

    res_none = metrics.provider_metrics(
        tmp_path, task_id=None, head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    assert res_none["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_none["first_pass"] is True
    assert res_none["attempts"] == 2
    assert res_none["worker_attempts"] == 1
    assert res_none["reviewer_attempts"] == 1
    assert res_none["failed_attempts"] == 0
    assert res_none["successful_correction_rounds"] == 0
    assert res_none["usage"] == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert res_none["observed_usage_subtotal"] == res_none["usage"]
    assert res_none["phase_seconds"] == {"provider_reported": 10.0, "outside_provider_unattributed": 40.0}
    assert res_none["rework_taxonomy"] == {
        "state": "OBSERVED",
        "initial_outcome": "FIRST_PASS",
        "rework_path": "NONE",
        "terminal_outcome": "VERIFIED",
    }
    assert set(res_none["task_metadata"].keys()) == {
        "task_id",
        "task_sha256",
        "declared_requirement_count",
        "focused_selector_count",
        "gap_reference",
    }
    assert res_none["task_metadata"] == {
        "task_id": "task",
        "task_sha256": expected_task_sha,
        "declared_requirement_count": 2,
        "focused_selector_count": 1,
        "gap_reference": "docs/ref",
    }

    res_explicit = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    assert res_explicit == res_none


def test_tagged_truncated_blocked_worker_with_valid_stream_usage(tmp_path):
    worker = _create_attempt(tmp_path, "crun-worker-0", role="worker", state="BLOCKED", usage=None)
    stream_line = json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n"
    (worker / "stream.jsonl").write_text(stream_line)
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=20.0
    )
    assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res["attempts"] == 1
    assert res["worker_attempts"] == 1
    assert res["failed_attempts"] == 1
    assert res["first_pass"] is None
    assert res["usage_observed_attempts"] == 1
    assert res["observed_usage_subtotal"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res["usage"] == res["observed_usage_subtotal"]


def test_retry_then_truncation_aggregation(tmp_path):
    w0 = _create_attempt(tmp_path, "crun-worker-0-retry-0", role="worker", state="BLOCKED", usage=None)
    (w0 / "stream.jsonl").write_text(json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n")
    _create_attempt(tmp_path, "crun-worker-0-retry-1", role="worker", state="PROPOSAL_READY", usage=TOKENS)
    _create_attempt(
        tmp_path,
        "crun-reviewer-0",
        role="verifier",
        state="VERIFIED",
        usage=TOKENS,
        input_worker_run_id="crun-worker-0-retry-1",
    )
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=60.0
    )
    assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res["attempts"] == 3
    assert res["worker_attempts"] == 2
    assert res["reviewer_attempts"] == 1
    assert res["failed_attempts"] == 1
    assert res["first_pass"] is False
    assert res["usage_observed_attempts"] == 3
    assert res["usage"] == {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}
    assert res["rework_taxonomy"] == {
        "state": "OBSERVED",
        "initial_outcome": "ATTEMPT_BLOCKED",
        "rework_path": "ATTEMPT_BLOCKED",
        "terminal_outcome": "VERIFIED",
    }


def test_corrected_verified_chain_first_pass_false(tmp_path):
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state="PROPOSAL_READY")
    _create_attempt(
        tmp_path, "crun-reviewer-0", role="verifier", state="CHANGES_REQUIRED", input_worker_run_id="crun-worker-0"
    )
    _create_attempt(
        tmp_path,
        "crun-worker-1",
        role="worker",
        state="PROPOSAL_READY",
        input_proposal_sha256="b" * 64,
    )
    _create_attempt(
        tmp_path,
        "crun-reviewer-1",
        role="verifier",
        state="VERIFIED",
        input_worker_run_id="crun-worker-1",
    )
    res = metrics.provider_metrics(
        tmp_path,
        task_id="task",
        head_before=HEAD,
        final_worker_id="crun-worker-1",
        controller_run_id=CTRL,
        cycle_duration=100.0,
    )
    assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res["first_pass"] is False
    assert res["successful_correction_rounds"] == 1
    assert res["attempts"] == 4
    assert res["rework_taxonomy"] == {
        "state": "OBSERVED",
        "initial_outcome": "REVIEWER_CHANGES_REQUIRED",
        "rework_path": "REVIEWER_CHANGES_REQUIRED",
        "terminal_outcome": "VERIFIED",
    }


def test_mixed_structural_rework_is_bounded_without_reviewer_text(tmp_path):
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state="PROPOSAL_READY")
    _create_attempt(
        tmp_path, "crun-reviewer-0", role="verifier", state="CHANGES_REQUIRED", input_worker_run_id="crun-worker-0"
    )
    _create_attempt(tmp_path, "crun-worker-1", role="worker", state="BLOCKED")
    _create_attempt(
        tmp_path, "crun-worker-2", role="worker", state="PROPOSAL_READY", input_proposal_sha256="b" * 64
    )
    _create_attempt(
        tmp_path, "crun-reviewer-2", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-2"
    )
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, final_worker_id="crun-worker-2",
        controller_run_id=CTRL, cycle_duration=100.0,
    )
    assert res["rework_taxonomy"] == {
        "state": "OBSERVED",
        "initial_outcome": "REVIEWER_CHANGES_REQUIRED",
        "rework_path": "MIXED",
        "terminal_outcome": "VERIFIED",
    }


@pytest.mark.parametrize(
    "config",
    [
        {"rev_state": "CHANGES_REQUIRED", "rev_input": "crun-worker-0", "final": None},
        {"rev_state": "VERIFIED", "rev_input": "unknown-worker", "final": None},
        {"rev_state": "VERIFIED", "rev_input": "crun-worker-0", "final": "crun-worker-1"},
        {"rev_state": "VERIFIED", "rev_input": "crun-worker-0", "worker_state": "BLOCKED", "final": None},
    ],
)
def test_blocked_or_no_verified_chain_yields_null_first_pass(tmp_path, config):
    worker_state = config.get("worker_state", "PROPOSAL_READY")
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state=worker_state)
    _create_attempt(
        tmp_path,
        "crun-reviewer-0",
        role="verifier",
        state=config["rev_state"],
        input_worker_run_id=config["rev_input"],
    )
    res = metrics.provider_metrics(
        tmp_path,
        task_id="task",
        head_before=HEAD,
        final_worker_id=config["final"],
        controller_run_id=CTRL,
        cycle_duration=50.0,
    )
    assert res["first_pass"] is None


@pytest.mark.parametrize(
    "mismatch_kwargs,expected_state",
    [
        ({"controller_run_id": "different-ctrl"}, "UNAVAILABLE"),
        ({"head": "b" * 40}, "PARTIAL"),
        ({"task_id": "other-task"}, "PARTIAL"),
    ],
)
def test_controller_head_task_mismatch_single_attempt(tmp_path, mismatch_kwargs, expected_state):
    _create_attempt(tmp_path, "crun-worker-0", **mismatch_kwargs)
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=20.0
    )
    assert res["state"] == expected_state
    assert res["first_pass"] is None


def test_multiple_task_ids_under_controller_run_is_partial(tmp_path):
    _create_attempt(tmp_path, "crun-worker-0", task_id="task-a")
    _create_attempt(tmp_path, "crun-reviewer-0", task_id="task-b", role="verifier", state="VERIFIED")
    res = metrics.provider_metrics(
        tmp_path, task_id=None, head_before=HEAD, controller_run_id=CTRL, cycle_duration=20.0
    )
    assert res["state"] == "PARTIAL"
    assert res["first_pass"] is None
    assert res["task_metadata"] is None
    assert res["rework_taxonomy"] == {
        "state": "UNKNOWN",
        "initial_outcome": "UNKNOWN",
        "rework_path": "UNKNOWN",
        "terminal_outcome": "UNKNOWN",
    }


@pytest.mark.parametrize(
    "bad_args",
    [
        {"head_before": "short-head"},
        {"controller_run_id": "illegal/char"},
        {"task_id": "bad task id!"},
        {"final_worker_id": "bad final id!"},
    ],
)
def test_invalid_parameters_return_unavailable(tmp_path, bad_args):
    _create_attempt(tmp_path, "crun-worker-0")
    kwargs = {"task_id": "task", "head_before": HEAD, "controller_run_id": CTRL}
    kwargs.update(bad_args)
    res = metrics.provider_metrics(tmp_path, **kwargs)
    assert res["state"] == "UNAVAILABLE"
    assert res["rework_taxonomy"] == {
        "state": "UNAVAILABLE",
        "initial_outcome": "UNKNOWN",
        "rework_path": "UNKNOWN",
        "terminal_outcome": "UNKNOWN",
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"run_id": "mismatched-name"},
        {"role": None},
        {"role": "invalid-role"},
        {"state": "RUNNING"},
        {"state": None},
    ],
)
def test_controller_invalid_canonical_structure_partial(tmp_path, patch):
    _create_attempt(
        tmp_path,
        "crun-worker-0",
        role="worker",
        state="PROPOSAL_READY",
        usage=TOKENS,
        duration_seconds=5.0,
    )
    invalid_dir = tmp_path / ".local/two-site-refactor/proposals/crun-worker-bad"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "controller_run_id": CTRL,
        "run_id": "crun-worker-bad",
        "task_id": "task",
        "head": HEAD,
        "role": "worker",
        "state": "PROPOSAL_READY",
        "usage": TOKENS,
        "duration_seconds": 15.0,
    }
    for k, v in patch.items():
        if v is None:
            status.pop(k, None)
        else:
            status[k] = v
    (invalid_dir / "status.json").write_text(json.dumps(status))
    _write_task(invalid_dir, task_id="task", head=HEAD)

    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    assert res["state"] == "PARTIAL"
    assert res["first_pass"] is None
    assert res["task_metadata"] is None
    assert res["attempts"] == 1
    assert res["worker_attempts"] == 1
    assert res["reviewer_attempts"] == 0
    assert res["usage_observed_attempts"] == 1
    assert res["observed_usage_subtotal"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert res["phase_seconds"]["provider_reported"] is None
    assert res["phase_seconds"]["outside_provider_unattributed"] is None


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_status_json_aliases_rejected_under_controller(tmp_path, kind):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    path = _create_attempt(root, "crun-worker-0")
    status_file = path / "status.json"
    other = outside / f"status_target_{kind}.json"
    status_file.rename(other)
    if kind == "symlink":
        status_file.symlink_to(other)
    else:
        os.link(other, status_file)
    res = metrics.provider_metrics(
        root, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=20.0
    )
    assert res["state"] == "UNAVAILABLE"


def test_directory_symlink_entry_rejected_under_controller(tmp_path):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    real_outside_path = _create_attempt(outside, "crun-worker-outside")
    proposals = root / ".local/two-site-refactor/proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    symlink_entry = proposals / "crun-worker-symlink"
    symlink_entry.symlink_to(real_outside_path, target_is_directory=True)
    _create_attempt(root, "crun-worker-real")
    res = metrics.provider_metrics(
        root, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=20.0
    )
    assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res["attempts"] == 1


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_task_json_aliases_rejected_under_controller(tmp_path, kind):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    path = _create_attempt(root, "crun-worker-0")
    _create_attempt(
        root, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0"
    )
    task_file = path / "task.json"
    other = outside / f"task_target_{kind}.json"
    task_file.rename(other)
    if kind == "symlink":
        task_file.symlink_to(other)
    else:
        os.link(other, task_file)
    res = metrics.provider_metrics(
        root, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    assert res["state"] == "PARTIAL"
    assert res["first_pass"] is None
    assert res["task_metadata"] is None


@pytest.mark.parametrize(
    "raw_stream",
    [
        "{\n",
        "{}",
        "[]\n",
        "x" * 2_000_001,
        json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n" +
        json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n",
    ],
)
def test_malformed_and_duplicate_result_stream(tmp_path, raw_stream):
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state="PROPOSAL_READY", usage=TOKENS)
    failed = _create_attempt(tmp_path, "crun-worker-1", role="worker", state="BLOCKED", usage=None)
    (failed / "stream.jsonl").write_text(raw_stream)
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=30.0
    )
    assert res["usage"]["total_tokens"] is None
    assert res["observed_usage_subtotal"]["total_tokens"] == 14
    assert res["usage_observed_attempts"] == 1
    assert res["attempts"] == 2


def test_over_32_matches_returns_unavailable(tmp_path):
    for i in range(33):
        _create_attempt(tmp_path, f"crun-worker-{i:02d}")
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    assert res["state"] == "UNAVAILABLE"


def test_directory_scan_overflow_8192(tmp_path):
    folder = tmp_path / ".local/two-site-refactor/proposals"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(8193):
        (folder / f"dir_{i}").mkdir()
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=10.0
    )
    assert res["state"] == "UNAVAILABLE"


def test_missing_usage_yields_null_full_totals_plus_honest_subtotal(tmp_path):
    _create_attempt(tmp_path, "crun-worker-0", role="worker", state="PROPOSAL_READY", usage=TOKENS)
    _create_attempt(
        tmp_path,
        "crun-reviewer-0",
        role="verifier",
        state="VERIFIED",
        usage=None,
        input_worker_run_id="crun-worker-0",
    )
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=40.0
    )
    assert res["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert res["observed_usage_subtotal"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res["usage_observed_attempts"] == 1
    assert res["attempts"] == 2


def test_sentinel_not_leaked_in_metrics(tmp_path):
    sentinel = "SECRET_TELEMETRY_SENTINEL_TOKEN_XYZ987"
    worker = _create_attempt(
        tmp_path,
        "crun-worker-0",
        role="worker",
        state="PROPOSAL_READY",
        usage=None,
        prompt=sentinel,
        source=sentinel,
        finding=[sentinel],
        findings=[sentinel],
        command=f"run {sentinel}",
        conversation_id=sentinel,
        error=sentinel,
        error_text=sentinel,
    )
    (worker / "prompt.txt").write_text(sentinel)
    (worker / "source.py").write_text(f"code = '{sentinel}'\n")
    stream_line = json.dumps({"event": "result", "result": {"usage": TOKENS, "response": sentinel}}) + "\n"
    (worker / "stream.jsonl").write_text(stream_line)

    _create_attempt(
        tmp_path,
        "crun-reviewer-0",
        role="verifier",
        state="VERIFIED",
        usage=TOKENS,
        input_worker_run_id="crun-worker-0",
    )
    res = metrics.provider_metrics(
        tmp_path, task_id="task", head_before=HEAD, controller_run_id=CTRL, cycle_duration=50.0
    )
    serialized = json.dumps(res)
    assert sentinel not in serialized
    assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert set(res["rework_taxonomy"].values()) <= {
        "OBSERVED", "FIRST_PASS", "NONE", "VERIFIED",
    }
